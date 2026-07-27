#!/usr/bin/env python3
"""
check_convergence.py — READ-ONLY convergence watch.

Reports, per ticker, how many DISTINCT INSTRUMENTS have fired: 3+ is a fire, 2 is a
near-miss worth watching.

TWO MODES, and the difference matters — an earlier version conflated them and reported
"6 near misses" when the gate's live window held ZERO:

  --mode best  (default)  the best window ANYWHERE IN HISTORY. Answers "has this ticker
                          ever come close?" Useful for judging whether the gate is
                          reachable at all.
  --mode live             the gate's OWN trailing window ending now — what RULE_10 would
                          actually see on this run. Answers "is the pool filling?"
                          Applies the gate's 7-day DEDUP: a ticker already corroborated
                          inside that window is shown as `dedup-suppressed`, not FIRE,
                          because the gate would emit nothing for it.

`best` will always be >= `live`. A ticker can show as a best-window near miss whose legs
are months old and long outside the live window.

COUPLED TO THE GATE BY IMPORT for every decision it makes: `rule10_instruments`
(collapsing), `RULE_10_EXCLUDED` (eligibility), `RULE_10_MIN_INSTRUMENTS` (threshold),
`CONVERGENCE_WINDOW_DAYS` (window) and `_already_corroborated`/`DEDUP_WINDOW_DAYS`
(dedup). Change any of those in the gate and this script's output changes with them.
A local copy would drift silently — that is the RULE_01 lesson, where an
eligible-but-unmapped rule became its own phantom instrument because two places
disagreed about the same question.

⚠️ ONE THING IS STILL RESTATED, NOT IMPORTED: the candidate SELECT below. The gate's
`_candidate_alerts` bakes its own trailing-window filter into SQL, which `--mode best`
must not have (it scans all history), so the predicates — non-empty ticker, severity
in HIGH/CRITICAL — are written out here. **That is a real drift risk**: if the gate
ever changes which alerts are candidates, this file must change too. It is not the
"never a copy" this comment previously claimed. Pinned by
`tests/test_check_convergence.py`, which fails if the two predicate sets diverge.

SELECT-only. It never writes: the DB is opened `mode=ro`, so the driver itself
refuses, and the one query is a literal SELECT.

Usage:
    python scripts/check_convergence.py                 # working DB, read-only
    python scripts/check_convergence.py --days 30       # wider look-back
    DATABASE_PATH=/path/to/copy.db python scripts/check_convergence.py
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# THE COUPLING — the gate's own logic and constants, imported rather than restated.
from scripts.rule_10_corroboration import _already_corroborated, DEDUP_WINDOW_DAYS
from jpt_common import (RULE_10_EXCLUDED, RULE_10_MIN_INSTRUMENTS, _get_db_path,
                        rule10_instruments)
from scripts.rule_10_corroboration import CONVERGENCE_WINDOW_DAYS

CANDIDATES = """
    SELECT ticker, UPPER(TRIM(rule)) AS rule, created_at
    FROM alerts
    WHERE ticker IS NOT NULL AND TRIM(ticker) != ''
      AND severity IN ('HIGH', 'CRITICAL')
    ORDER BY created_at
"""


def scan(conn, window_days: int, live_only: bool = False
         ) -> list[tuple[str, int, list[str], str, bool]]:
    """Instrument count per ticker. Pure read.

    live_only=False -> best window anywhere in history.
    live_only=True  -> the gate's trailing window ending now, i.e. what RULE_10 sees.
    """
    by_ticker: dict[str, list] = defaultdict(list)
    for row in conn.execute(CANDIDATES):
        rule = row["rule"]
        if rule in RULE_10_EXCLUDED:          # the gate's own eligibility, imported
            continue
        try:
            ts = datetime.fromisoformat(row["created_at"])
        except (TypeError, ValueError):
            continue
        by_ticker[row["ticker"]].append((ts, rule))

    out = []
    cutoff = datetime.now() - timedelta(days=window_days)
    for ticker, events in by_ticker.items():
        if live_only:
            # The gate's real shape: one trailing window ending now.
            rules = [r for (ts, r) in events if ts >= cutoff]
            insts = rule10_instruments(rules)
            # ...and the gate's real SUPPRESSION. Counting instruments is only half of
            # what RULE_10 does; a ticker corroborated inside DEDUP_WINDOW_DAYS emits
            # nothing. Without this the script prints "*** FIRE" for tickers the gate
            # is correctly silent on, which reads as the gate being broken.
            suppressed = (len(insts) >= RULE_10_MIN_INSTRUMENTS
                          and _already_corroborated(conn, ticker))
            out.append((ticker, len(insts), insts,
                        str(max((ts for ts, _ in events), default="-"))[:10], suppressed))
            continue
        best, best_at = [], None
        for start, _ in events:
            rules = [r for (ts, r) in events
                     if start <= ts <= start + timedelta(days=window_days)]
            # rule10_instruments does the collapsing — the same call the gate makes
            insts = rule10_instruments(rules)
            if len(insts) > len(best):
                best, best_at = insts, start
        # dedup is meaningless across historical windows — never suppressed in best mode
        out.append((ticker, len(best), best, str(best_at)[:10] if best_at else "-", False))
    return sorted(out, key=lambda r: (-r[1], r[0]))


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only convergence watch.")
    p.add_argument("--days", type=int, default=CONVERGENCE_WINDOW_DAYS,
                   help=f"window in days (default {CONVERGENCE_WINDOW_DAYS} — the gate's own)")
    p.add_argument("--min", type=int, default=2,
                   help="only show tickers at or above this instrument count")
    p.add_argument("--mode", choices=("best", "live"), default="best",
                   help="best = best window ever (default); live = the gate's trailing "
                        "window ending now, i.e. what RULE_10 would see this run")
    args = p.parse_args()

    path = str(_get_db_path(None))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)   # driver-level read-only
    conn.row_factory = sqlite3.Row
    try:
        rows = scan(conn, args.days, live_only=(args.mode == "live"))
    finally:
        conn.close()

    fires = [r for r in rows if r[1] >= RULE_10_MIN_INSTRUMENTS and not r[4]]
    dedup = [r for r in rows if r[1] >= RULE_10_MIN_INSTRUMENTS and r[4]]
    near = [r for r in rows if r[1] == RULE_10_MIN_INSTRUMENTS - 1]

    label = ("BEST window anywhere in history" if args.mode == "best"
             else "LIVE trailing window — what the gate sees now")
    print(f"Convergence watch — {args.days}-day window, threshold "
          f"{RULE_10_MIN_INSTRUMENTS} instruments")
    print(f"MODE: {label}")
    print(f"(eligibility and collapsing come from the gate itself; excluded: "
          f"{', '.join(sorted(RULE_10_EXCLUDED))})\n")

    shown = 0
    for ticker, n, insts, when, suppressed in rows:
        if n < args.min:
            continue
        shown += 1
        flag = (f"  dedup-suppressed (corroborated within {DEDUP_WINDOW_DAYS}d)" if suppressed
                else "  *** FIRE" if n >= RULE_10_MIN_INSTRUMENTS
                else "  <- near miss" if n == RULE_10_MIN_INSTRUMENTS - 1 else "")
        print(f"  {ticker:<8} {n} instrument(s)  {', '.join(insts):<46} from {when}{flag}")
    if not shown:
        print(f"  (no ticker reached {args.min}+ instruments in the window)")

    print(f"\n  FIRES ({RULE_10_MIN_INSTRUMENTS}+)      : {[r[0] for r in fires] or 'none'}")
    if args.mode == "live":
        print(f"  DEDUP-SUPPRESSED : {[r[0] for r in dedup] or 'none'}"
              f"   (at threshold, but corroborated within {DEDUP_WINDOW_DAYS}d — "
              f"the gate emits nothing)")
    print(f"  NEAR MISSES ({RULE_10_MIN_INSTRUMENTS - 1}) : {[r[0] for r in near] or 'none'}")
    print(f"  tickers scanned : {len(rows)}   (read-only; nothing was written)")


if __name__ == "__main__":
    main()
