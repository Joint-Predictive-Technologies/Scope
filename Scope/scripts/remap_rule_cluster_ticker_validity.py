#!/usr/bin/env python3
"""Un-key stored RULE_CLUSTER alerts whose symbol is not in `tickers`.

WHAT THIS CORRECTS
------------------
`rule_cluster._gather` keys clusters on `normalize_ticker(resolved_symbol or
raw_ticker_string)`. When the ingestion linker CORRECTLY declines to link a row
(`ticker_id` NULL), the `or` falls back to the raw parse string and resurrects the
garbage the linker just rejected. That is how a 3-member `US` cluster built entirely
of U.S. Treasury bills reached the gate, and how state abbreviations lifted out of
municipal-bond descriptions (`'Arlington, Municipal Bond'` -> `TX`) become keys.

The rule is fixed forward. This handles the alerts already stored.

⭐ KEY REMOVAL IS THE MECHANISM, NOT RETRACTION. RULE_CLUSTER is NOT in
`jpt_common.SIGNED_RULES`, so `alert_corroborates` short-circuits True regardless of
`lifecycle_stage` — a retracted RULE_CLUSTER alert still supplies the `congressional`
instrument. What actually removes it from the gate is
`rule_10_corroboration._candidate_alerts`' `ticker IS NOT NULL AND ticker != ''`.
So this blanks the KEY; the `review` stage it also sets is for human triage only.
**Do not read `lifecycle_stage='review'` as the thing that closes the gate.**

⭐ THE ALERT IS KEPT. Absence from `tickers` is a coverage gap, not proof the symbol
is fake — a listed company can be missing from the table. Measured: `FI` (Fiserv;
the table still holds the pre-2023 `FISV`), `CTRA` and `NSRGY` are real and absent.
Nothing is deleted and nothing is fuzzy-resolved to a near neighbour.

MEASURED ON THE WORKING DB (md5 177f474b03495c20df10a21335ca9dc3)
-----------------------------------------------------------------
**0 rows to change.** There is exactly one stored RULE_CLUSTER alert (id 8800,
`SPCX`, HIGH) and `SPCX` IS in `tickers` — it reached the rule through the raw
fallback because all four of its transactions have `ticker_id` NULL, which is
precisely why the fallback is validated rather than deleted.

So this script is a NO-OP locally. It exists for production, where the corpus is
larger and the `US` cluster is expected to be present.

THE GUARD
---------
The failure mode that matters is a truncated or empty `tickers`: every alert would
look unvalidated and this would blank the entire corpus. So it refuses to write when
the validity set is empty, or when more than half of stored keys fail to resolve.
Mirrors `rule_01b_first_touch`'s own alarms.

DISCIPLINE (mirrors scripts/remap_rule02_ticker_resolution.py)
--------------------------------------------------------------
* STANDALONE. Never imported by the migration chain; never run by the scheduler.
* DRY-RUN BY DEFAULT. `--apply` required. READ the dry run first.
* IDEMPOTENT. Re-running after an `--apply` changes zero rows.
* REVERSIBLE. Pre-images to `rule_cluster_ticker_remap_backup`; undo is printed.
* NO RESCORE. Touches `ticker`, `lifecycle_stage`, `why_matters` only.
* PREPARED, NOT RUN against the working DB.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import normalize_ticker  # noqa: E402
from scripts.rule_cluster import UNVALIDATED_FLAG, _validity_set  # noqa: E402

RULE = "RULE_CLUSTER"
MAJORITY_UNVALIDATED = 0.5


def _connect(db_path: str | None) -> sqlite3.Connection:
    if db_path:
        conn = sqlite3.connect(db_path)
    else:
        from jpt_common import _get_db_path

        conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def classify(conn):
    valid = _validity_set(conn)
    rows = conn.execute(
        "SELECT id, ticker, severity, headline, lifecycle_stage, why_matters "
        "FROM alerts WHERE rule = ? ORDER BY id",
        (RULE,),
    ).fetchall()
    unkey, ok = [], 0
    for a in rows:
        stored = a["ticker"] or ""
        if stored == "" and (a["why_matters"] or "").find(UNVALIDATED_FLAG.strip()) >= 0:
            ok += 1                                   # already un-keyed
            continue
        if stored and normalize_ticker(stored) in valid:
            ok += 1
            continue
        unkey.append({"id": a["id"], "key": stored, "severity": a["severity"],
                      "headline": a["headline"]})
    guard = {"validity_set": len(valid), "stored": len(rows), "unvalidated": len(unkey),
             "ratio": (len(unkey) / len(rows)) if rows else 0.0}
    return unkey, ok, guard


def preflight(conn, window_days: int | None):
    """READ-ONLY prod counts. Run and READ this before ever passing --apply."""
    where, params = "rule = ?", [RULE]
    if window_days:
        where += " AND date(created_at) >= date('now', ?)"
        params.append(f"-{window_days} days")
    valid = _validity_set(conn)
    rows = conn.execute(f"SELECT id, ticker, severity FROM alerts WHERE {where}", params).fetchall()
    bad = [r for r in rows
           if not (r["ticker"] and normalize_ticker(r["ticker"]) in valid)]
    print(f"\n  PRE-FLIGHT (read-only)"
          f"{f' — created within {window_days} days' if window_days else ' — all time'}")
    print(f"    validity set (`tickers`)      : {len(valid)}")
    print(f"    RULE_CLUSTER alerts in window : {len(rows)}")
    print(f"    ...on an unvalidated key      : {len(bad)}"
          f"   HIGH/CRITICAL: {sum(1 for r in bad if r['severity'] in ('HIGH','CRITICAL'))}")
    from collections import Counter
    print(f"    unvalidated keys              : {Counter(r['ticker'] for r in bad).most_common()}")
    print("    NOTE key-removal is what closes the gate; RULE_CLUSTER is unsigned, so")
    print("         lifecycle_stage alone would not (alert_corroborates returns True).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="Actually write.")
    parser.add_argument("--db", default=None, help="DB path (default: the resolved app DB).")
    parser.add_argument("--preflight", action="store_true", help="Read-only counts, then exit.")
    parser.add_argument("--window-days", type=int, default=None)
    args = parser.parse_args()

    conn = _connect(args.db)
    if args.preflight:
        preflight(conn, args.window_days)
        return 0

    unkey, ok, guard = classify(conn)
    print(f"\nRULE_CLUSTER ticker-validity remap — {guard['stored']} stored alerts")
    print(f"  validity set: {guard['validity_set']} symbols;  already correct: {ok}")
    print(f"\n  UN-KEY ({len(unkey)}) — symbol not in `tickers`; alert KEPT, flagged:")
    for c in unkey:
        print(f"    id {c['id']:>5} [{c['severity']:<8}] key={c['key']!r}  {c['headline'][:48]}")

    if guard["validity_set"] == 0:
        print("\n  REFUSING — `tickers` is EMPTY, so every alert looks unvalidated and this\n"
              "  would blank the whole corpus. Fix the validity set first.")
        return 2
    if guard["ratio"] > MAJORITY_UNVALIDATED:
        print(f"\n  REFUSING — {guard['ratio']:.0%} of stored keys fail to validate. That is a\n"
              "  truncated `tickers`, not a corpus of garbage. Investigate before writing.")
        return 2

    if not args.apply:
        print(f"\n  DRY RUN — nothing written. {len(unkey)} to un-key.")
        print("  Run --preflight against prod FIRST, read it, then re-run with --apply.")
        return 0
    if not unkey:
        print("\n  Nothing to do; already applied.")
        return 0

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rule_cluster_ticker_remap_backup (
            alert_id        INTEGER PRIMARY KEY,
            old_ticker      TEXT,
            old_lifecycle   TEXT,
            old_why_matters TEXT
        )
    """)
    for c in unkey:
        r = conn.execute("SELECT ticker, lifecycle_stage, why_matters FROM alerts WHERE id=?",
                         (c["id"],)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO rule_cluster_ticker_remap_backup"
            "(alert_id, old_ticker, old_lifecycle, old_why_matters) VALUES (?,?,?,?)",
            (c["id"], r["ticker"], r["lifecycle_stage"], r["why_matters"]),
        )
        conn.execute(
            "UPDATE alerts SET ticker = '', lifecycle_stage = 'review', "
            "why_matters = COALESCE(why_matters, '') || ? WHERE id = ?",
            (UNVALIDATED_FLAG + repr(c["key"]), c["id"]),
        )
    conn.commit()
    print(f"\n  APPLIED — {len(unkey)} un-keyed; pre-images in rule_cluster_ticker_remap_backup.")
    print("  No score column was touched.")
    print("  Undo: UPDATE alerts SET "
          "ticker=(SELECT old_ticker FROM rule_cluster_ticker_remap_backup b WHERE b.alert_id=alerts.id), "
          "lifecycle_stage=(SELECT old_lifecycle FROM rule_cluster_ticker_remap_backup b WHERE b.alert_id=alerts.id), "
          "why_matters=(SELECT old_why_matters FROM rule_cluster_ticker_remap_backup b WHERE b.alert_id=alerts.id) "
          "WHERE id IN (SELECT alert_id FROM rule_cluster_ticker_remap_backup);")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
