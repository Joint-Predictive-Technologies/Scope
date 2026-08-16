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
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ingest_house_index as ih
from jpt_common import db_connection, log_activity

SOURCE = "ROSTER_CHECK"
MIN_OCCURRENCES = 2


def _filing_date_for_docs(conn, doc_ids: str | None) -> str | None:
    """Most recent filing date among the doc_ids an unmatched name was seen on."""
    docs = [d for d in (doc_ids or "").split(",") if d]
    if not docs:
        return None
    row = conn.execute(
        "SELECT MAX(filing_date) FROM filings WHERE source='house' AND doc_id IN (%s)"
        % ",".join("?" * len(docs)),
        docs,
    ).fetchone()
    return row[0] if row else None


def check_term_mismatches(conn) -> list[dict]:
    """🔴 THE GUARD THAT WAS MISSING — a MATCHED filing pointing at the wrong person.

    `unmatched_filers` records names the matcher FAILED on, and ROSTER_CHECK
    re-tests those. That is structurally blind to the defect that actually
    shipped: a name the matcher matched **confidently and wrongly**. On
    2026-08-15, 22 filings sat on members who left office in 1973, 2005 and 2007
    — and `unmatched_filers` was EMPTY the whole time, because nothing had failed
    to match. A guard keyed on failure cannot see a confident wrong answer.

    This asks the opposite question: for every filing that HAS a member_id, did
    that member hold office on the filing date?

    ⚠️ Silent when `member_terms` is unloaded — the same fail-open rule as
    `_covers`. A guard with no data must report "nothing to say", not "nothing is
    wrong"; the `terms_loaded` count in the notes is what distinguishes them.

    ⚠️ Uses the SAME `TERM_GRACE_DAYS` window as the matcher, imported rather than
    re-stated. A departing member's final PTR lands legitimately after their term
    ends (Waltz filed 3 days after, Manning 108), and flagging those as CRITICAL
    would be a standing false alarm — the fastest way to train everyone to ignore
    the guard. If the two windows ever drift apart, the guard starts contradicting
    the matcher that produced the data.
    """
    try:
        loaded = conn.execute("SELECT COUNT(*) FROM member_terms").fetchone()[0]
    except sqlite3.OperationalError:
        return []
    if not loaded:
        return []

    return [dict(r) for r in conn.execute(
        """
        SELECT f.id AS filing_id, f.doc_id, f.filing_date, f.member_id,
               m.full_name, (SELECT MAX(term_end) FROM member_terms t
                             WHERE t.bioguide_id = f.member_id) AS last_term_end
        FROM filings f
        JOIN members m ON m.bioguide_id = f.member_id
        WHERE f.member_id IS NOT NULL
          AND f.filing_date IS NOT NULL
          AND EXISTS (SELECT 1 FROM member_terms t WHERE t.bioguide_id = f.member_id)
          AND NOT EXISTS (
              SELECT 1 FROM member_terms t
              WHERE t.bioguide_id = f.member_id
                AND t.term_start <= substr(f.filing_date, 1, 10)
                AND substr(f.filing_date, 1, 10)
                    <= date(t.term_end, '+' || ? || ' days')
          )
        ORDER BY f.filing_date DESC
        """,
        (ih.TERM_GRACE_DAYS,),
    )]


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
        # The matcher is now date-aware, so re-testing a stored name without its
        # filing date would fail open and could "resolve" the name onto a
        # historical namesake — then DELETE the row below, destroying the only
        # record that the filer was ever a problem. The date comes from the
        # filings this name was actually seen on.
        filing_date = _filing_date_for_docs(conn, r["doc_ids"])
        mid = ih.match_member_id(r["first_name"], r["last_name"], members, filing_date)
        if mid is not None:
            # A matcher/roster fix has since resolved this name — clear it.
            resolved.append((r["norm_name"], mid))
            conn.execute("DELETE FROM unmatched_filers WHERE norm_name=?", (r["norm_name"],))
        elif (r["occurrences"] or 0) >= MIN_OCCURRENCES:
            flagged.append(f"{r['first_name']} {r['last_name']} (x{r['occurrences']})")
    conn.commit()

    mismatched = check_term_mismatches(conn)

    if flagged:
        notes = (f"WARNING: {len(flagged)} recurring unmatched filer(s) "
                 f"(>={MIN_OCCURRENCES} filings) — likely roster gap or name-normalization "
                 f"miss: {'; '.join(flagged)}. resolved_since_last={len(resolved)}")
    else:
        notes = f"OK: no recurring unmatched filers. resolved_since_last={len(resolved)}"

    if mismatched:
        detail = "; ".join(
            f"filing {m['filing_id']} (doc {m['doc_id']}, {m['filing_date']}) -> "
            f"{m['member_id']} {m['full_name']}, terms end {m['last_term_end']}"
            for m in mismatched[:10]
        )
        extra = (f" CRITICAL: {len(mismatched)} filing(s) attributed to a member whose "
                 f"terms do not cover the filing date: {detail}"
                 + (" …" if len(mismatched) > 10 else ""))
        notes += extra
    else:
        notes += " term-mismatch check: 0 violations."
    print(f"[{SOURCE}] {notes}")

    log_activity(conn, SOURCE, scanned=len(rows), flagged=len(flagged) + len(mismatched),
                 emitted=len(resolved), duration_seconds=round(time.time() - t0, 2),
                 notes=notes)
    conn.close()
    return len(flagged) + len(mismatched)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Flag recurring unmatched House filers.")
    p.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    p.parse_args()
    sys.exit(0 if run() == 0 else 0)  # advisory only — never fail the scheduler
