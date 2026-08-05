#!/usr/bin/env python3
"""Retract RULE_02 alerts that are redundant under member-set identity.

WHAT THIS CORRECTS
------------------
RULE_02 identified an alert by its HEADLINE STRING and deduped over 7 days against a
90-day scan window. So:

  * a 4-member window and its 3-member sub-window both fired — different counts mean
    different headlines, and the dedup never saw them as related; and
  * an identical member set re-fired every time it fell outside the 7-day lookback.

The rule is fixed forward (`rule_02_cluster.py`: `_fingerprint`, `_prior_alerts`).
This handles the alerts already stored.

MEASURED on the working DB (md5 177f474b03495c20df10a21335ca9dc3): of 82 stored
RULE_02 alerts, **16 are redundant — 13 subsets and 3 refires — 13 of them HIGH**,
leaving 66 canonical. Examples:

    id 4159 / 8595  refire of id 1     "3 members bought AAPL"   (06-17 / 07-09 / 07-20)
    id 47 / 48 / 44 subset of id 46    the MSFT chain 46 > 47 > 48
    id 15           subset of id 14    BRK.B
    id 10 / 12      subset of id 11    AMZN

The canonical row kept is the LARGEST member set, earliest `created_at`.

⚠️ THIS IS CORRECTNESS-OF-RECORD, NOT A GATE CHANGE. `rule10_instruments` counts
DISTINCT RULES, so two RULE_02 alerts on one ticker were always ONE `congressional`
instrument — a double-fire never double-counted at the gate. And RULE_02 is not in
`jpt_common.SIGNED_RULES`, so `alert_corroborates` returns True regardless of
`lifecycle_stage`: **retraction here is gate-cosmetic.** What this buys is fewer
duplicate cards and less refire spam, not a corrected convergence. Do not oversell it.

⚠️ RUN ORDER — THIS ONE GOES LAST
-----------------------------------
    1. remap_rule02_directional_count.py   (retract 4, direction-correct 3)
    2. remap_rule02_ticker_resolution.py   (un-key 5)
    3. THIS ONE

The target sets genuinely overlap — ids 66 and 74 with #1, ids 66, 68 and 69 with #2 —
so order is not cosmetic. This script REFUSES to run until both of the others have,
which it detects by their pre-image tables (`rule02_directional_remap_backup`,
`rule02_ticker_remap_backup`). It also SKIPS any row already
`lifecycle_stage='retracted'`, so #1's retractions are never touched twice; rows #2
moved to `'review'` may still be retracted here, since redundancy is orthogonal to key
validity, and the pre-image records `'review'` so the undo restores it exactly.

DISCIPLINE (mirrors the other RULE_02 remaps)
---------------------------------------------
* STANDALONE. Never imported by the migration chain; never run by the scheduler.
* DRY-RUN BY DEFAULT. `--apply` required. READ the dry run first.
* IDEMPOTENT. Re-running after an `--apply` changes zero rows.
* REVERSIBLE. Pre-images to `rule02_identity_remap_backup`; the undo is printed.
* NO RESCORE. Touches `lifecycle_stage` and `why_matters` only.
* PREPARED, NOT RUN against the working DB.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

RULE = "RULE_02"
REASON = "retracted: redundant under member-set identity — "

#: The two remaps that must have run first. Their pre-image tables are the evidence.
PREREQ_TABLES = {
    "rule02_directional_remap_backup": "remap_rule02_directional_count.py",
    "rule02_ticker_remap_backup": "remap_rule02_ticker_resolution.py",
}


def _connect(db_path: str | None) -> sqlite3.Connection:
    if db_path:
        conn = sqlite3.connect(db_path)
    else:
        from jpt_common import _get_db_path

        conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _member_names(tags: str | None) -> frozenset:
    return frozenset(x.strip() for x in (tags or "").split(",") if x.strip())


def _direction(headline: str | None) -> str:
    for d in ("NET_LONG", "NET_SHORT", "MIXED"):
        if f"({d})" in (headline or ""):
            return d
    return "?"


def missing_prereqs(conn) -> list[str]:
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return [script for table, script in PREREQ_TABLES.items() if table not in have]


def classify(conn):
    """Return (redundant, canonical_count, skipped). Read-only.

    `redundant` is a list of {id, why, canonical, severity, headline, lifecycle}.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT id, ticker, headline, tags, severity, created_at, lifecycle_stage, why_matters "
        "FROM alerts WHERE rule = ? ORDER BY id", (RULE,))]

    redundant: dict[int, tuple[str, int]] = {}

    # 1) REFIRE — identical (ticker, member set, direction). Keep the earliest.
    groups = defaultdict(list)
    for a in rows:
        groups[(a["ticker"], _member_names(a["tags"]), _direction(a["headline"]))].append(a)
    for members in groups.values():
        if len(members) > 1:
            members.sort(key=lambda x: (x["created_at"] or "", x["id"]))
            for dup in members[1:]:
                redundant[dup["id"]] = ("refire", members[0]["id"])

    # 2) SUBSET — a member set strictly contained in another on the same ticker.
    for a in rows:
        if a["id"] in redundant:
            continue
        A = _member_names(a["tags"])
        if not A:
            continue
        for b in rows:
            if b["id"] == a["id"]:
                continue
            B = _member_names(b["tags"])
            if b["ticker"] == a["ticker"] and B and A < B:
                redundant[a["id"]] = ("subset", b["id"])
                break

    by_id = {a["id"]: a for a in rows}
    out, skipped = [], []
    for aid, (why, canon) in sorted(redundant.items()):
        a = by_id[aid]
        if (a["lifecycle_stage"] or "") == "retracted":
            # Already retracted — by the directional remap, or by a previous run of
            # this one. Never touch a row twice.
            skipped.append({"id": aid, "why": "already retracted"})
            continue
        out.append({"id": aid, "why": why, "canonical": canon,
                    "severity": a["severity"], "headline": a["headline"],
                    "lifecycle": a["lifecycle_stage"]})
    return out, len(rows) - len(redundant), skipped


def preflight(conn, window_days: int | None):
    """READ-ONLY prod counts. Run and READ this before ever passing --apply."""
    redundant, canonical, skipped = classify(conn)
    if window_days:
        keep = {r["id"] for r in conn.execute(
            "SELECT id FROM alerts WHERE rule=? AND date(created_at) >= date('now', ?)",
            (RULE, f"-{window_days} days"))}
        redundant = [r for r in redundant if r["id"] in keep]
    hi = sum(1 for r in redundant if r["severity"] in ("HIGH", "CRITICAL"))
    print(f"\n  PRE-FLIGHT (read-only)"
          f"{f' — created within {window_days} days' if window_days else ' — all time'}")
    print(f"    redundant alerts        : {len(redundant)}   HIGH/CRITICAL: {hi}")
    print(f"      of which refires      : {sum(1 for r in redundant if r['why']=='refire')}")
    print(f"      of which subsets      : {sum(1 for r in redundant if r['why']=='subset')}")
    print(f"    canonical survivors     : {canonical}")
    print(f"    already retracted (skip): {len(skipped)}")
    print("    NOTE gate-cosmetic: RULE_02 is unsigned and the gate counts DISTINCT")
    print("         RULES, so this is correctness-of-record, not a convergence change.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually write.")
    parser.add_argument("--db", default=None, help="DB path (default: the resolved app DB).")
    parser.add_argument("--preflight", action="store_true", help="Read-only counts, then exit.")
    parser.add_argument("--window-days", type=int, default=None)
    args = parser.parse_args()

    conn = _connect(args.db)

    if args.preflight:
        preflight(conn, args.window_days)
        return 0

    missing = missing_prereqs(conn)
    redundant, canonical, skipped = classify(conn)

    print(f"\nRULE_02 identity/dedup remap — {len(redundant)} redundant, "
          f"{canonical} canonical, {len(skipped)} skipped")
    for r in redundant:
        print(f"    id {r['id']:>5} [{r['severity']:<6}] {r['why']:<7} -> canonical id "
              f"{r['canonical']}   {str(r['headline'])[:44]}")
    for s in skipped:
        print(f"    SKIP id {s['id']}: {s['why']}")

    if missing:
        print("\n  REFUSING — run these first, in this order:")
        for m in missing:
            print(f"      {m}")
        print("  Their target sets overlap this one's (ids 66, 74 with the directional")
        print("  remap; 66, 68, 69 with the ticker remap), so order is not cosmetic.")
        print("  Detected by the absence of their pre-image tables.")
        return 2

    if not args.apply:
        print(f"\n  DRY RUN — nothing written. {len(redundant)} to retract.")
        print("  Run --preflight against prod FIRST, read it, then re-run with --apply.")
        return 0
    if not redundant:
        print("\n  Nothing to do; already applied.")
        return 0

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rule02_identity_remap_backup (
            alert_id        INTEGER PRIMARY KEY,
            old_lifecycle   TEXT,
            old_why_matters TEXT
        )
    """)
    for r in redundant:
        row = conn.execute(
            "SELECT lifecycle_stage, why_matters FROM alerts WHERE id=?", (r["id"],)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO rule02_identity_remap_backup"
            "(alert_id, old_lifecycle, old_why_matters) VALUES (?,?,?)",
            (r["id"], row["lifecycle_stage"], row["why_matters"]),
        )
        conn.execute(
            "UPDATE alerts SET lifecycle_stage='retracted', "
            "why_matters = COALESCE(why_matters || ' | ', '') || ? WHERE id = ?",
            (f"{REASON}{r['why']} of alert {r['canonical']}", r["id"]),
        )
    conn.commit()
    print(f"\n  APPLIED — {len(redundant)} retracted; "
          f"pre-images in rule02_identity_remap_backup.")
    print("  No score column was touched. Retraction is gate-cosmetic (RULE_02 unsigned).")
    print("  Undo: UPDATE alerts SET "
          "lifecycle_stage=(SELECT old_lifecycle FROM rule02_identity_remap_backup b WHERE b.alert_id=alerts.id), "
          "why_matters=(SELECT old_why_matters FROM rule02_identity_remap_backup b WHERE b.alert_id=alerts.id) "
          "WHERE id IN (SELECT alert_id FROM rule02_identity_remap_backup);")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
