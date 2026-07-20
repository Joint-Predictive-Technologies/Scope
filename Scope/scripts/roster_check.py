#!/usr/bin/env python3
"""
roster_check.py — ROSTER_CHECK

Catches the next "Linda T. Sánchez": a filer whose name recurs across House
ingestions but never matches the members table (roster gap or normalization
miss). Reads the unmatched_filers ledger that ingest_house_index populates,
re-tests each recorded name against the *current* matcher (so names fixed by a
matcher/roster update auto-clear), and logs a ROSTER_CHECK activity_log row
flagging any name still unmatched after appearing on 2+ filings.

Monthly job — see api/main.py.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ingest_house_index as ih
from jpt_common import db_connection, log_activity

SOURCE = "ROSTER_CHECK"
MIN_OCCURRENCES = 2


def run() -> int:
    t0 = time.time()
    conn = db_connection()
    ih.ensure_unmatched_filers_table(conn)
    members = ih.load_members(conn)

    rows = conn.execute(
        "SELECT norm_name, first_name, last_name, occurrences, doc_ids "
        "FROM unmatched_filers"
    ).fetchall()

    resolved, flagged = [], []
    for r in rows:
        mid = ih.match_member_id(r["first_name"], r["last_name"], members)
        if mid is not None:
            # A matcher/roster fix has since resolved this name — clear it.
            resolved.append((r["norm_name"], mid))
            conn.execute("DELETE FROM unmatched_filers WHERE norm_name=?", (r["norm_name"],))
        elif (r["occurrences"] or 0) >= MIN_OCCURRENCES:
            flagged.append(f"{r['first_name']} {r['last_name']} (x{r['occurrences']})")
    conn.commit()

    if flagged:
        notes = (f"WARNING: {len(flagged)} recurring unmatched filer(s) "
                 f"(>={MIN_OCCURRENCES} filings) — likely roster gap or name-normalization "
                 f"miss: {'; '.join(flagged)}. resolved_since_last={len(resolved)}")
    else:
        notes = f"OK: no recurring unmatched filers. resolved_since_last={len(resolved)}"
    print(f"[{SOURCE}] {notes}")

    log_activity(conn, SOURCE, scanned=len(rows), flagged=len(flagged),
                 emitted=len(resolved), duration_seconds=round(time.time() - t0, 2),
                 notes=notes)
    conn.close()
    return len(flagged)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Flag recurring unmatched House filers.")
    p.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    p.parse_args()
    sys.exit(0 if run() == 0 else 0)  # advisory only — never fail the scheduler
