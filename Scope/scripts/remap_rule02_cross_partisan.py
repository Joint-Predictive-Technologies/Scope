#!/usr/bin/env python3
"""Retract stored RULE_02 alerts that are SINGLE-PARTY under the refined definition.

WHAT THIS CORRECTS
------------------
RULE_02's refined meaning is **"a Democrat AND a Republican agree"** — not "N
members clustered". `rule_02_cluster.py` now enforces that forward
(`is_cross_partisan`, gated on the COUNTED members). This handles the alerts
already stored under the old, looser definition.

MEASURED on the working DB (md5 177f474b03495c20df10a21335ca9dc3): of 82 stored
RULE_02 alerts, **54 are cross-partisan and 28 are single-party — 17 of them
HIGH/CRITICAL**. All 28 are currently `lifecycle_stage='created'`. Examples:

    id  4 [MEDIUM] {Democratic: 2}  "2 members traded AAPL within 7 days (MIXED)"
    id  9 [HIGH]   {Democratic: 2}  "2 members sold AMZN within 7 days (NET_SHORT)"

⚠️ RETRACTION IS GATE-COSMETIC — AND ALSO PRODUCT-COSMETIC.
`rule_10_corroboration._candidate_alerts` never reads `lifecycle_stage`, so
retracting these rows changes NOTHING about gate membership. Nor does anything
FILTER `retracted`: the rows stay in the feed carrying a badge. Both true of the
prior RULE_02 remaps; stated here so "retracted" is not mistaken for "removed".

⚠️ AND THESE 28 ARE NOT EVIDENCE ABOUT THE FORWARD GATE. An earlier draft of this
docstring said all 28 are the sole congressional contributor on their ticker
within 14 days. That is arithmetically true and MISLEADING, on three counts, and a
verification pass caught it:

  * **All 28 are 2-MEMBER clusters**, and `--min-members` defaults to 3. Today's
    rule would not emit one of them whatever their party. They are pre-threshold
    legacy rows, so they say nothing about the cross-partisan gate.
  * 11 of the 28 are MEDIUM and were never gate candidates at all
    (`_candidate_alerts` requires `severity IN ('HIGH','CRITICAL')`).
  * Of the 17 HIGH, 3 sit on a ticker that keeps a SURVIVING cross-partisan
    RULE_02 alert inside the window, which supplies the same `congressional`
    instrument.

So the gate-relevant figure is **14**, not 28. The honest forward evidence lives
in the rule's own before/after, not here: **0 clusters suppressed in the live 90d
window, 11 of 47 at 365d.**

⚠️ RUN ORDER — THIS ONE GOES LAST
-----------------------------------
    1. remap_rule02_directional_count.py   (retract 4, direction-correct 3)
    2. remap_rule02_ticker_resolution.py   (un-key 5)
    3. remap_rule02_identity_dedup.py      (retract 16)
    4. THIS ONE

It REFUSES to run until all three have, detected by their pre-image tables. The
order is not cosmetic: #1 corrects the directional member set and rewrites the
member names in `tags`, and THIS SCRIPT READS THAT FIELD — running it first would
judge cross-partisanship on a member set that includes non-directional members,
which is precisely the population the definition excludes.

⚠️ HOW MEMBERS ARE IDENTIFIED — AND WHY NOT BY SPLITTING ON COMMAS
-------------------------------------------------------------------
`tags` is a comma-joined list of `members.full_name`, and those names THEMSELVES
CONTAIN COMMAS ("Case, Ed"). Splitting on "," shreds them — the same hazard
`CLAUDE.md` records for RULE_06's positional tag string. Instead each name in the
roster is matched against the tags string LONGEST-FIRST, so "Begich, Nicholas J."
wins over "Begich, Nicholas" — two real people in DIFFERENT parties.

Any alert whose tags cannot be resolved to a member set, or that is ambiguous, is
**SKIPPED and reported**, never guessed. On the measured snapshot 82/82 resolve
and 0 ambiguities occur.

DISCIPLINE (mirrors the other RULE_02 remaps)
---------------------------------------------
* STANDALONE. Never imported by the migration chain; never run by the scheduler.
* DRY-RUN BY DEFAULT. `--apply` required. READ the dry run first.
* IDEMPOTENT. Re-running after an `--apply` changes zero rows.
* REVERSIBLE. Pre-images to `rule02_crosspartisan_remap_backup`; undo printed.
* NO RESCORE. Touches `lifecycle_stage` and `why_matters` only.
* PREPARED, NOT RUN against the working DB.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

RULE = "RULE_02"
REASON = "retracted: single-party under the cross-partisan definition — "
DEMOCRAT = "Democratic"
REPUBLICAN = "Republican"

#: The three remaps that must have run first. Their pre-image tables are the evidence.
PREREQ_TABLES = {
    "rule02_directional_remap_backup": "remap_rule02_directional_count.py",
    "rule02_ticker_remap_backup": "remap_rule02_ticker_resolution.py",
    "rule02_identity_remap_backup": "remap_rule02_identity_dedup.py",
}


def _connect(db_path: str | None) -> sqlite3.Connection:
    if db_path:
        conn = sqlite3.connect(db_path)
    else:
        from jpt_common import _get_db_path

        conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def missing_prereqs(conn) -> list[str]:
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return [script for table, script in PREREQ_TABLES.items() if table not in have]


def roster(conn) -> list[tuple[str, str]]:
    """(full_name, party), LONGEST NAME FIRST so the longest match wins."""
    rows = [(r["full_name"], r["party"]) for r in conn.execute(
        "SELECT full_name, party FROM members WHERE full_name IS NOT NULL AND full_name != ''")]
    return sorted(rows, key=lambda x: -len(x[0]))


def resolve_parties(tags: str, names: list[tuple[str, str]]) -> tuple[list[str], str | None]:
    """Party list for the members named in `tags`. Longest-match, non-overlapping.

    Returns (parties, problem). `problem` is non-None when the row must be SKIPPED
    rather than judged: no name matched at all, or the matched spans do not
    account for the whole tag string's name content.
    """
    if not tags or not tags.strip():
        return [], "empty tags"
    remaining = tags
    parties: list[str] = []
    for name, party in names:                      # longest first
        while name and name in remaining:
            parties.append(party)
            # Blank out the matched span so a shorter name cannot re-match inside it.
            remaining = remaining.replace(name, "\x00", 1)
    if not parties:
        return [], "no roster name matched"
    leftover = "".join(ch for ch in remaining if ch not in "\x00, ").strip()
    if leftover:
        return parties, f"unmatched name fragment {leftover[:40]!r}"
    return parties, None


def classify(conn):
    """Return (single_party, cross, skipped). Read-only."""
    names = roster(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT id, ticker, headline, tags, severity, created_at, lifecycle_stage, why_matters "
        "FROM alerts WHERE rule = ? ORDER BY id", (RULE,))]

    single, cross, skipped = [], [], []
    for a in rows:
        parties, problem = resolve_parties(a["tags"] or "", names)
        if problem:
            skipped.append({"id": a["id"], "why": problem})
            continue
        if DEMOCRAT in parties and REPUBLICAN in parties:
            cross.append(a)
            continue
        if (a["lifecycle_stage"] or "") == "retracted":
            skipped.append({"id": a["id"], "why": "already retracted"})
            continue
        single.append({"id": a["id"], "severity": a["severity"],
                       "headline": a["headline"], "lifecycle": a["lifecycle_stage"],
                       "composition": dict(Counter(parties))})
    return single, cross, skipped


def preflight(conn, window_days: int | None):
    """READ-ONLY prod counts. Run and READ this before ever passing --apply."""
    single, cross, skipped = classify(conn)
    if window_days:
        keep = {r["id"] for r in conn.execute(
            "SELECT id FROM alerts WHERE rule=? AND date(created_at) >= date('now', ?)",
            (RULE, f"-{window_days} days"))}
        single = [s for s in single if s["id"] in keep]
    hi = sum(1 for s in single if s["severity"] in ("HIGH", "CRITICAL"))
    print(f"\n  PRE-FLIGHT (read-only)"
          f"{f' — created within {window_days} days' if window_days else ' — all time'}")
    print(f"    single-party alerts     : {len(single)}   HIGH/CRITICAL: {hi}")
    print(f"    cross-partisan survivors: {len(cross)}")
    print(f"    skipped (unresolvable / already retracted): {len(skipped)}")
    print("    NOTE retraction is GATE-COSMETIC: _candidate_alerts never reads")
    print("         lifecycle_stage. The forward rule change is NOT cosmetic — see")
    print("         the module docstring.")


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
    single, cross, skipped = classify(conn)

    print(f"\nRULE_02 cross-partisan remap — {len(single)} single-party, "
          f"{len(cross)} cross-partisan, {len(skipped)} skipped")
    for s in single:
        print(f"    id {s['id']:>5} [{s['severity']:<6}] {s['composition']}  "
              f"{str(s['headline'])[:46]}")
    for s in skipped:
        print(f"    SKIP id {s['id']}: {s['why']}")

    if missing:
        print("\n  REFUSING — run these first, in this order:")
        for m in missing:
            print(f"      {m}")
        print("  #1 rewrites the member names in `tags`, and this script READS that")
        print("  field — judging cross-partisanship before it runs would use a member")
        print("  set that includes non-directional members.")
        print("  Detected by the absence of their pre-image tables.")
        return 2

    if not args.apply:
        print(f"\n  DRY RUN — nothing written. {len(single)} to retract.")
        print("  Run --preflight against prod FIRST, read it, then re-run with --apply.")
        return 0
    if not single:
        print("\n  Nothing to do; already applied.")
        return 0

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rule02_crosspartisan_remap_backup (
            alert_id        INTEGER PRIMARY KEY,
            old_lifecycle   TEXT,
            old_why_matters TEXT
        )
    """)
    for s in single:
        row = conn.execute(
            "SELECT lifecycle_stage, why_matters FROM alerts WHERE id=?", (s["id"],)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO rule02_crosspartisan_remap_backup"
            "(alert_id, old_lifecycle, old_why_matters) VALUES (?,?,?)",
            (s["id"], row["lifecycle_stage"], row["why_matters"]),
        )
        comp = "/".join(f"{k}:{v}" for k, v in sorted(s["composition"].items()))
        conn.execute(
            "UPDATE alerts SET lifecycle_stage='retracted', "
            "why_matters = COALESCE(why_matters || ' | ', '') || ? WHERE id = ?",
            (f"{REASON}{comp}", s["id"]),
        )
    conn.commit()
    print(f"\n  APPLIED — {len(single)} retracted; "
          f"pre-images in rule02_crosspartisan_remap_backup.")
    print("  No score column was touched. Retraction is gate-cosmetic (RULE_02 unsigned,")
    print("  and _candidate_alerts ignores lifecycle_stage).")
    print("  Undo: UPDATE alerts SET "
          "lifecycle_stage=(SELECT old_lifecycle FROM rule02_crosspartisan_remap_backup b WHERE b.alert_id=alerts.id), "
          "why_matters=(SELECT old_why_matters FROM rule02_crosspartisan_remap_backup b WHERE b.alert_id=alerts.id) "
          "WHERE id IN (SELECT alert_id FROM rule02_crosspartisan_remap_backup);")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
