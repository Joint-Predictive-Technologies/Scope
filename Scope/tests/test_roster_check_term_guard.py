"""The guard that was missing: a MATCHED filing pointing at the wrong person.

`unmatched_filers` records names the matcher FAILED on, and ROSTER_CHECK re-tests
those. That is structurally blind to what actually shipped — a name matched
confidently and wrongly. On 2026-08-15, 22 filings sat on members who left office
in 1973, 2005 and 2007 while `unmatched_filers` was EMPTY the entire time.

⚠️ These tests deliberately assert the guard FIRES on a constructed bad case.
Confirming it is silent on clean data proves nothing on its own — a function that
returns [] unconditionally passes that check.
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import roster_check as rc  # noqa: E402


def _db(with_terms=True, filings=()):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE members (bioguide_id TEXT PRIMARY KEY, full_name TEXT)")
    conn.execute("""CREATE TABLE filings (id INTEGER PRIMARY KEY, source TEXT, doc_id TEXT,
                    member_id TEXT, filing_date TEXT)""")
    if with_terms:
        conn.execute("""CREATE TABLE member_terms (bioguide_id TEXT, term_start TEXT,
                        term_end TEXT, chamber TEXT, state TEXT, district TEXT,
                        source TEXT, updated_at TEXT)""")
        conn.executemany("INSERT INTO member_terms (bioguide_id, term_start, term_end) VALUES (?,?,?)", [
            ("B000315", "1971-01-21", "1973-01-03"),   # Begich Sr.
            ("B001323", "2025-01-03", "2027-01-03"),   # Begich III
            ("W000823", "2023-01-03", "2025-01-20"),   # Waltz — departed
        ])
    conn.executemany("INSERT INTO members VALUES (?,?)", [
        ("B000315", "Begich, Nicholas"),
        ("B001323", "Begich, Nicholas J."),
        ("W000823", "Waltz, Michael"),
    ])
    conn.executemany("INSERT INTO filings (id, source, doc_id, member_id, filing_date)"
                     " VALUES (?,?,?,?,?)", filings)
    return conn


def test_the_guard_FIRES_on_the_real_defect():
    """A 2026 filing attributed to a member who left in 1973 — the exact shape
    that went undetected for the whole of 2026-08-15."""
    conn = _db(filings=[(1, "house", "20020055", "B000315", "2026-06-12")])
    hits = rc.check_term_mismatches(conn)
    assert len(hits) == 1
    assert hits[0]["filing_id"] == 1
    assert hits[0]["member_id"] == "B000315"
    assert hits[0]["last_term_end"] == "1973-01-03"


def test_the_guard_is_silent_on_the_corrected_attribution():
    conn = _db(filings=[(1, "house", "20020055", "B001323", "2026-06-12")])
    assert rc.check_term_mismatches(conn) == []


def test_the_guard_does_not_cry_wolf_on_a_departing_members_final_ptr():
    """Waltz's term ended 2025-01-20; he filed 3 days later, legitimately. A
    guard that flags this every run teaches everyone to ignore it."""
    conn = _db(filings=[(1, "house", "20026639", "W000823", "2025-01-23")])
    assert rc.check_term_mismatches(conn) == []


def test_the_guard_uses_the_matchers_own_grace_window():
    """Same constant, imported — if the two drift apart the guard starts
    contradicting the matcher that produced the data."""
    import ingest_house_index as ih
    inside = (ih.TERM_GRACE_DAYS - 5)
    outside = (ih.TERM_GRACE_DAYS + 60)
    import datetime
    end = datetime.date(2025, 1, 20)
    ok = (end + datetime.timedelta(days=inside)).isoformat()
    bad = (end + datetime.timedelta(days=outside)).isoformat()
    assert rc.check_term_mismatches(_db(filings=[(1, "house", "d", "W000823", ok)])) == []
    assert len(rc.check_term_mismatches(_db(filings=[(1, "house", "d", "W000823", bad)]))) == 1


def test_the_guard_is_silent_when_member_terms_is_missing_or_empty():
    """Fail open: a guard with no data must say 'nothing to report', not
    'nothing is wrong' — and must not crash before m017 has run."""
    conn = _db(with_terms=False, filings=[(1, "house", "d", "B000315", "2026-06-12")])
    assert rc.check_term_mismatches(conn) == []
    conn2 = _db(filings=[(1, "house", "d", "B000315", "2026-06-12")])
    conn2.execute("DELETE FROM member_terms")
    assert rc.check_term_mismatches(conn2) == []


def test_the_guard_ignores_filings_with_no_member_or_no_date():
    conn = _db(filings=[(1, "house", "d", None, "2026-06-12"),
                        (2, "house", "e", "B000315", None)])
    assert rc.check_term_mismatches(conn) == []
