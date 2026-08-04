#!/usr/bin/env python3
"""Re-key stored RULE_02 alerts on the resolved symbol; un-key the phantoms.

WHAT THIS CORRECTS
------------------
RULE_02 grouped and keyed clusters on the raw parse string, with no `tickers` join
and no `normalize_ticker`. So `alerts.ticker` — the column RULE_10 converges on —
holds whatever the PDF parser emitted. `US` is not a symbol; 213 transactions carry
it, all unlinked, and it produced four alerts. `CA` produced a fifth.

Measured on the working DB (md5 177f474b03495c20df10a21335ca9dc3):
**5 of 82 stored RULE_02 alerts key on an unresolved symbol; 4 are HIGH.**

    id 16 [HIGH]   Cluster: 2 members sold CA ...
    id 66 [HIGH]   Cluster: 3 members sold US ...
    id 67 [MEDIUM] Cluster: 4 members traded US ...
    id 68 [HIGH]   Cluster: 3 members bought US ...
    id 69 [HIGH]   Cluster: 2 members bought US ...

The code is fixed forward (`rule_02_cluster.py`). This handles the stored rows.

⚠️ id 66 is ALSO corrected by `remap_rule02_directional_count.py` (defect #1). The
two are independent and commute — #1 rewrites `headline`/`severity`, #2 rewrites
`ticker`. Run either order; each backs up only the columns it touches.

WHAT IT DOES
------------
* UNRESOLVED key -> `ticker=''`, `lifecycle_stage='review'`, the raw symbol
  recorded in `why_matters`. The alert is KEPT. Absence from `tickers` is a
  coverage gap for a human to triage, NOT evidence the symbol is fake — a listed
  company can legitimately be missing from the table (the SPCX lesson). Nothing is
  deleted and nothing is fuzzy-resolved to a near neighbour.
* RESOLVED but non-canonical (e.g. `BRK-B`) -> re-keyed to the canonical form, so
  the same company stops occupying two corroboration keys.

⭐ WHY THE BLANK KEY IS THE PART THAT MATTERS, unlike defect #1's retraction:
`rule_10_corroboration._candidate_alerts` selects
`WHERE ticker IS NOT NULL AND ticker != ''`, so blanking genuinely removes the
alert from the gate. Retraction would NOT: RULE_02 is not in
`jpt_common.SIGNED_RULES`, so `alert_corroborates` short-circuits True regardless
of `lifecycle_stage`, and a retracted RULE_02 alert still supplies the
`congressional` instrument. **Do not read `lifecycle_stage='review'` here as the
thing that closes the gate — the empty ticker is.** Signing RULE_02 remains the
separate session that makes lifecycle actually bite.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No merging of split real symbols. `transactions.ticker_id` is assigned by a
company-NAME matcher and three of its groupings join distinct companies
(`IDEXX`->DLB, `MTRS`->GIS, `CNSWF`->STZ), so an FK-driven merge would corrupt the
corpus in a way that is far harder to undo than the phantom it fixes. The
fragmentation (WMT/CS, META/FB, NOC/CORP, STX/PLC …) is left in place and recorded
as a known residual; the ingestion linker is the real defect.

THE GUARD, and why it is not #1's corpus-reproduction gate
----------------------------------------------------------
#1 changed cluster MEMBERSHIP, so it had to prove the baseline could reproduce the
stored corpus before deciding an alert was stale. #2 changes a single column that
is a pure function of the stored ticker, so reconstruction proves nothing. The
failure mode here is different and worse: if `tickers` were empty or truncated,
EVERY alert would look unresolved and this script would blank the whole corpus.

So the guard mirrors `rule_01b_first_touch`'s own alarms: refuse to write if the
validity set is empty, or if a majority of stored RULE_02 keys fail to resolve —
that means the resolution set is broken, not the data.

⚠️ SEQUENCING — RUN THIS BEFORE THE FIXED RULE RUNS AGAIN.
`alert_exists` keys on `(rule, ticker, headline)`. The fix changes an unresolved
cluster's stored ticker from e.g. `US` to `''`, which is a different dedup key, so
a rule run that happens BEFORE this remap will re-emit one duplicate per unresolved
cluster still inside the 7-day window. Measured: with a recent `ticker='US'` alert
present, the old code emits 0 and the new code emits 1.

Harmless on today's corpus only by accident — every stored RULE_02 alert predates
the window (newest `created_at` is 2026-07-20). Do not rely on that; run the remap
first, or accept one duplicate per recent unresolved alert.

DISCIPLINE (mirrors scripts/remap_rule09_tickers.py)
----------------------------------------------------
* STANDALONE. Never imported by the migration chain; never run by the scheduler.
* DRY-RUN BY DEFAULT. `--apply` required. READ the dry run first.
* IDEMPOTENT. Re-running after an `--apply` changes zero rows.
* REVERSIBLE. Pre-images to `rule02_ticker_remap_backup`; the undo is printed.
* NO RESCORE. Touches `ticker`, `lifecycle_stage`, `why_matters` only — never
  novelty, opportunity, evidence_confidence or any detection-time score.
* PREPARED, NOT RUN against the working DB.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import normalize_ticker  # noqa: E402
from rule_02_cluster import UNRESOLVED_FLAG, _validity_set  # noqa: E402

RULE = "RULE_02"

#: Refuse to write if more than this fraction of stored keys fail to resolve.
#: A real corpus has a handful of phantoms; a majority means `tickers` is broken.
MAJORITY_UNRESOLVED = 0.5


def _connect(db_path: str | None) -> sqlite3.Connection:
    if db_path:
        conn = sqlite3.connect(db_path)
    else:
        from jpt_common import _get_db_path

        conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def classify(conn):
    """Return (unkey, rekey, ok, guard). Read-only."""
    valid = _validity_set(conn)
    rows = conn.execute(
        "SELECT id, ticker, severity, headline, lifecycle_stage, why_matters "
        "FROM alerts WHERE rule = ? ORDER BY id",
        (RULE,),
    ).fetchall()

    unkey, rekey, ok = [], [], 0
    for a in rows:
        stored = a["ticker"] or ""
        norm = normalize_ticker(stored) if stored else ""
        already_flagged = (a["why_matters"] or "").startswith(UNRESOLVED_FLAG)

        if stored == "" and already_flagged:
            ok += 1                       # already un-keyed by a previous run
            continue
        if norm and norm in valid:
            if norm != stored:
                rekey.append({"id": a["id"], "old": stored, "new": norm,
                              "severity": a["severity"]})
            else:
                ok += 1
            continue
        unkey.append({"id": a["id"], "key": stored, "severity": a["severity"],
                      "headline": a["headline"]})

    total = len(rows)
    guard = {
        "validity_set": len(valid),
        "stored": total,
        "unresolved": len(unkey),
        "ratio": (len(unkey) / total) if total else 0.0,
    }
    return unkey, rekey, ok, guard


def preflight(conn, window_days: int | None):
    """READ-ONLY prod counts. Run this, and read it, before ever passing --apply."""
    where = "rule = ?"
    params: list = [RULE]
    if window_days:
        where += " AND date(created_at) >= date('now', ?)"
        params.append(f"-{window_days} days")
    valid = _validity_set(conn)
    rows = conn.execute(
        f"SELECT id, ticker, severity FROM alerts WHERE {where}", params
    ).fetchall()
    unres = [r for r in rows
             if not ((normalize_ticker(r["ticker"]) if r["ticker"] else "") in valid)]
    print(f"\n  PRE-FLIGHT (read-only)"
          f"{f' — created within {window_days} days' if window_days else ' — all time'}")
    print(f"    validity set (`tickers`): {len(valid)}")
    print(f"    RULE_02 alerts in window: {len(rows)}")
    print(f"    ...on an unresolved key : {len(unres)}"
          f"   HIGH/CRITICAL: {sum(1 for r in unres if r['severity'] in ('HIGH','CRITICAL'))}")
    from collections import Counter
    print(f"    unresolved keys         : {Counter(r['ticker'] for r in unres).most_common()}")
    print("    split real symbols are NOT merged by this script (known residual).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="Actually write.")
    parser.add_argument("--db", default=None, help="DB path (default: the resolved app DB).")
    parser.add_argument("--preflight", action="store_true",
                        help="Read-only prod counts only, then exit.")
    parser.add_argument("--window-days", type=int, default=None,
                        help="Restrict the pre-flight to alerts created in the last N days.")
    args = parser.parse_args()

    conn = _connect(args.db)

    if args.preflight:
        preflight(conn, args.window_days)
        return 0

    unkey, rekey, ok, guard = classify(conn)

    print(f"\nRULE_02 ticker-resolution remap — {guard['stored']} stored alerts")
    print(f"  validity set: {guard['validity_set']} symbols")
    print(f"  already correct: {ok}")
    print(f"\n  UN-KEY ({len(unkey)}) — symbol does not resolve; alert KEPT, flagged:")
    for c in unkey:
        print(f"    id {c['id']:>4} [{c['severity']:<6}] key={c['key']!r}  {c['headline'][:52]}")
    print(f"\n  RE-KEY ({len(rekey)}) — resolved but non-canonical:")
    for c in rekey:
        print(f"    id {c['id']:>4} [{c['severity']:<6}] {c['old']!r} -> {c['new']!r}")

    if guard["validity_set"] == 0:
        print("\n  REFUSING — `tickers` is EMPTY, so the validity set is unavailable and\n"
              "  every alert would look unresolved. Fix the resolution set first.")
        return 2
    if guard["ratio"] > MAJORITY_UNRESOLVED:
        print(f"\n  REFUSING — {guard['ratio']:.0%} of stored keys fail to resolve. That is a\n"
              "  broken resolution set, not a corpus of phantoms. Investigate `tickers`.")
        return 2

    if not args.apply:
        print(f"\n  DRY RUN — nothing written. {len(unkey)} to un-key, {len(rekey)} to re-key.")
        print("  Run --preflight against prod FIRST, read it, then re-run with --apply.")
        return 0

    if not unkey and not rekey:
        print("\n  Nothing to do; already applied.")
        return 0

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rule02_ticker_remap_backup (
            alert_id        INTEGER PRIMARY KEY,
            old_ticker      TEXT,
            old_lifecycle   TEXT,
            old_why_matters TEXT
        )
    """)

    def _backup(alert_id):
        r = conn.execute("SELECT ticker, lifecycle_stage, why_matters FROM alerts WHERE id=?",
                         (alert_id,)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO rule02_ticker_remap_backup"
            "(alert_id, old_ticker, old_lifecycle, old_why_matters) VALUES (?,?,?,?)",
            (alert_id, r["ticker"], r["lifecycle_stage"], r["why_matters"]),
        )

    for c in unkey:
        _backup(c["id"])
        conn.execute(
            "UPDATE alerts SET ticker = '', lifecycle_stage = 'review', "
            "why_matters = ? || COALESCE(' | ' || why_matters, '') WHERE id = ?",
            (UNRESOLVED_FLAG + repr(c["key"]), c["id"]),
        )
    for c in rekey:
        _backup(c["id"])
        conn.execute("UPDATE alerts SET ticker = ? WHERE id = ?", (c["new"], c["id"]))
    conn.commit()

    print(f"\n  APPLIED — {len(unkey)} un-keyed, {len(rekey)} re-keyed; "
          f"pre-images in rule02_ticker_remap_backup.")
    print("  No score column was touched.")
    print("  Undo: UPDATE alerts SET "
          "ticker=(SELECT old_ticker FROM rule02_ticker_remap_backup b WHERE b.alert_id=alerts.id), "
          "lifecycle_stage=(SELECT old_lifecycle FROM rule02_ticker_remap_backup b WHERE b.alert_id=alerts.id), "
          "why_matters=(SELECT old_why_matters FROM rule02_ticker_remap_backup b WHERE b.alert_id=alerts.id) "
          "WHERE id IN (SELECT alert_id FROM rule02_ticker_remap_backup);")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
