#!/usr/bin/env python3
"""Retract the RULE_02 alerts whose member count was inflated by non-directional trades.

WHAT THIS CORRECTS
------------------
`rule_02_cluster.find_clusters` took its member count from every member with any
row in the 7-day window, and its verb from a net over each member's FIRST row.
A member whose only activity on the ticker was an *exchange* therefore counted
toward the headline number and could swing its verb, so RULE_02 emitted alerts
claiming a directional consensus that the underlying trades do not support.

The code is fixed forward (rule_02_cluster.py). This script exists only for the
alerts already stored, which the fix cannot reach — detection-time output is
immutable, so the corpus is corrected by an explicit, reviewed remap or not at all.

Measured on the working DB (md5 177f474b03495c20df10a21335ca9dc3), 4 of 82
RULE_02 alerts are affected, 3 of them HIGH:

    id 73 [HIGH]   "3 members sold WAT"     -> 1 directional (Dingell, Hern: exchange)
    id 74 [HIGH]   "2 members sold WAT"     -> 1 directional (Dingell: exchange)
    id  5 [HIGH]   "2 members bought ABT"   -> 1 directional (Friedman: exchange)
    id 72 [MEDIUM] "2 members traded VSNT"  -> 0 directional (Dingell, Pelosi: exchange)

WHY RETRACT RATHER THAN RE-COUNT
--------------------------------
Every one of the four falls BELOW the minimum cluster size once the uncounted
members are removed — three drop to a single directional member and one to zero.
There is no smaller true cluster hiding inside them to rewrite the headline to; the
signal is not overstated, it is absent. Rewriting "3 members" to "1 member" would
manufacture a cluster alert for a lone trade, which is RULE_01B's job, not RULE_02's.

So they are retracted in place: `lifecycle_stage='retracted'`, with the reason
recorded on the row. Nothing is deleted — a retracted alert stays auditable, and
the count of what we got wrong is itself a number worth keeping.

⚠️ THE GATE DOES NOT HONOUR RETRACTION for RULE_02. RULE_02 is not in
`jpt_common.SIGNED_RULES`, so `alert_corroborates` short-circuits True for it and a
retracted RULE_02 alert can still complete a RULE_10 convergence. Retracting here is
therefore necessary but NOT sufficient; closing it means signing RULE_02, which is a
separate human-gated session that this fix unblocks. Do not assume this script makes
the gate honest.

DISCIPLINE (mirrors scripts/remap_rule09_tickers.py)
----------------------------------------------------
* STANDALONE. Never imported by the migration chain; never run by the scheduler.
* DRY-RUN BY DEFAULT. `--apply` is required to write. READ the dry run first.
* IDEMPOTENT. Re-running after an `--apply` changes zero rows.
* REVERSIBLE. `--apply` writes the pre-image of every touched row to
  `rule02_directional_remap_backup` before updating, and prints the undo.
* PREPARED, NOT RUN. This has never been executed against the working DB.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rule_02_cluster import COUNTED_DIRECTIONS, member_direction  # noqa: E402

RULE = "RULE_02"
REASON = "retracted: member count included non-directional (exchange-only) members"


def _connect(db_path: str | None) -> sqlite3.Connection:
    if db_path:
        conn = sqlite3.connect(db_path)
    else:
        from jpt_common import _get_db_path

        conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _member_index(conn) -> dict[str, str]:
    return {
        r["full_name"]: r["bioguide_id"]
        for r in conn.execute("SELECT bioguide_id, full_name FROM members")
        if r["full_name"]
    }


def _segment(tagstr: str, names_by_len: list[str]) -> list[str]:
    """Recover member names from the comma-joined tag string.

    The names are not stored as a list — they are joined with "," and member names
    themselves contain ", " ("Kean, Thomas H."). Longest-match against the roster is
    the only way back. Any residue is returned with a "?" prefix so the caller can
    refuse to touch a row it could not fully parse.
    """
    out: list[str] = []
    rest = (tagstr or "").strip()
    while rest:
        for name in names_by_len:
            if rest.startswith(name):
                out.append(name)
                rest = rest[len(name):].lstrip(", ").strip()
                break
        else:
            piece = rest.split(",")[0].strip()
            out.append("?" + piece)
            rest = rest[len(piece):].lstrip(", ").strip()
    return out


def find_affected(conn) -> tuple[list[dict], list[dict]]:
    """Return (to_retract, skipped). Read-only."""
    ids_by_name = _member_index(conn)
    names_by_len = sorted(ids_by_name, key=len, reverse=True)

    to_retract: list[dict] = []
    skipped: list[dict] = []

    rows = conn.execute(
        "SELECT id, ticker, headline, severity, tags, lifecycle_stage "
        "FROM alerts WHERE rule = ? ORDER BY id",
        (RULE,),
    ).fetchall()

    for a in rows:
        names = _segment(a["tags"] or "", names_by_len)
        if not names or any(n.startswith("?") for n in names):
            skipped.append({"id": a["id"], "why": f"tags did not segment: {a['tags']!r}"})
            continue

        counted = 0
        detail = []
        for name in names:
            mid = ids_by_name.get(name)
            if mid is None:
                counted = -1
                skipped.append({"id": a["id"], "why": f"name not on roster: {name!r}"})
                break
            member_rows = [
                {"transaction_type": r["transaction_type"]}
                for r in conn.execute(
                    "SELECT transaction_type FROM transactions "
                    "WHERE member_id = ? AND raw_ticker_string = ?",
                    (mid, a["ticker"]),
                )
            ]
            d = member_direction(member_rows)
            if d in COUNTED_DIRECTIONS:
                counted += 1
            detail.append((name, d))
        if counted < 0:
            continue

        # The alert claims len(names) members. If fewer than that actually traded
        # directionally, the headline overstates — and because all four known cases
        # collapse below the cluster minimum, retraction is the correction.
        if counted < len(names):
            to_retract.append(
                {
                    "id": a["id"],
                    "ticker": a["ticker"],
                    "severity": a["severity"],
                    "headline": a["headline"],
                    "claimed": len(names),
                    "counted": counted,
                    "detail": detail,
                    "already": (a["lifecycle_stage"] or "") == "retracted",
                }
            )

    return to_retract, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Actually write. Without this the script only reports.")
    parser.add_argument("--db", default=None, help="DB path (default: the resolved app DB).")
    args = parser.parse_args()

    conn = _connect(args.db)
    to_retract, skipped = find_affected(conn)

    pending = [c for c in to_retract if not c["already"]]

    print(f"\nRULE_02 directional-count remap — {len(to_retract)} affected, "
          f"{len(pending)} pending, {len(skipped)} skipped\n")
    for c in to_retract:
        mark = "(already retracted)" if c["already"] else ""
        print(f"  id {c['id']:>4} [{c['severity']:<6}] claims {c['claimed']} "
              f"-> {c['counted']} directional  {mark}")
        print(f"           {c['headline']}")
        for name, d in c["detail"]:
            print(f"             {'COUNTED ' if d in COUNTED_DIRECTIONS else 'uncounted'}  {name}  ({d})")
    for s in skipped:
        print(f"  SKIP id {s['id']}: {s['why']}")

    if not args.apply:
        print("\n  DRY RUN — nothing written. Read the rows above, then re-run with --apply.")
        return 0

    if not pending:
        print("\n  Nothing to do; already applied.")
        return 0

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rule02_directional_remap_backup (
            alert_id        INTEGER PRIMARY KEY,
            old_lifecycle   TEXT,
            old_why_matters TEXT
        )
    """)
    for c in pending:
        row = conn.execute(
            "SELECT lifecycle_stage, why_matters FROM alerts WHERE id = ?", (c["id"],)
        ).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO rule02_directional_remap_backup"
            "(alert_id, old_lifecycle, old_why_matters) VALUES (?,?,?)",
            (c["id"], row["lifecycle_stage"], row["why_matters"]),
        )
        note = (f"{REASON} — claimed {c['claimed']}, "
                f"{c['counted']} traded directionally")
        conn.execute(
            "UPDATE alerts SET lifecycle_stage = 'retracted', "
            "why_matters = COALESCE(why_matters || ' | ', '') || ? WHERE id = ?",
            (note, c["id"]),
        )
    conn.commit()

    print(f"\n  APPLIED — {len(pending)} alert(s) retracted; "
          f"pre-images in rule02_directional_remap_backup.")
    print("  Undo: UPDATE alerts SET lifecycle_stage=(SELECT old_lifecycle FROM "
          "rule02_directional_remap_backup b WHERE b.alert_id=alerts.id), "
          "why_matters=(SELECT old_why_matters FROM rule02_directional_remap_backup b "
          "WHERE b.alert_id=alerts.id) WHERE id IN "
          "(SELECT alert_id FROM rule02_directional_remap_backup);")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
