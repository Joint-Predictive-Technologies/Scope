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

`best` will always be >= `live`. A ticker can show as a best-window near miss whose legs
are months old and long outside the live window.

COUPLED TO THE GATE BY IMPORT, never a copy. It imports `rule10_instruments`,
`RULE_10_EXCLUDED`, `RULE_10_MIN_INSTRUMENTS` and `CONVERGENCE_WINDOW_DAYS` directly,
so if the gate's instrument map, eligibility, threshold or window change, this
script's output changes with them. A local copy would drift silently — that is the
RULE_01 lesson, where an eligible-but-unmapped rule became its own phantom instrument
because two places disagreed about the same question.

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
         ) -> list[tuple[str, int, list[str], str]]:
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
            out.append((ticker, len(insts), insts,
                        str(max((ts for ts, _ in events), default="-"))[:10]))
            continue
        best, best_at = [], None
        for start, _ in events:
            rules = [r for (ts, r) in events
                     if start <= ts <= start + timedelta(days=window_days)]
            # rule10_instruments does the collapsing — the same call the gate makes
            insts = rule10_instruments(rules)
            if len(insts) > len(best):
                best, best_at = insts, start
        out.append((ticker, len(best), best, str(best_at)[:10] if best_at else "-"))
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

    fires = [r for r in rows if r[1] >= RULE_10_MIN_INSTRUMENTS]
    near = [r for r in rows if r[1] == RULE_10_MIN_INSTRUMENTS - 1]

    label = ("BEST window anywhere in history" if args.mode == "best"
             else "LIVE trailing window — what the gate sees now")
    print(f"Convergence watch — {args.days}-day window, threshold "
          f"{RULE_10_MIN_INSTRUMENTS} instruments")
    print(f"MODE: {label}")
    print(f"(eligibility and collapsing come from the gate itself; excluded: "
          f"{', '.join(sorted(RULE_10_EXCLUDED))})\n")

    shown = 0
    for ticker, n, insts, when in rows:
        if n < args.min:
            continue
        shown += 1
        flag = ("  *** FIRE" if n >= RULE_10_MIN_INSTRUMENTS
                else "  <- near miss" if n == RULE_10_MIN_INSTRUMENTS - 1 else "")
        print(f"  {ticker:<8} {n} instrument(s)  {', '.join(insts):<46} from {when}{flag}")
    if not shown:
        print(f"  (no ticker reached {args.min}+ instruments in the window)")

    print(f"\n  FIRES ({RULE_10_MIN_INSTRUMENTS}+)      : {[r[0] for r in fires] or 'none'}")
    print(f"  NEAR MISSES ({RULE_10_MIN_INSTRUMENTS - 1}) : {[r[0] for r in near] or 'none'}")
    print(f"  tickers scanned : {len(rows)}   (read-only; nothing was written)")


if __name__ == "__main__":
    main()
