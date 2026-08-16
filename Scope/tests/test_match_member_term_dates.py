"""Term-date disambiguation in `match_member_id`.

The defect these tests pin: a 2026 PTR matched a member who left office in 1973.
Not because the anchor path chose wrongly, but because it found TWO candidates,
declared ambiguity, and handed the decision to `_difflib_match` — which resolves
by string similarity and therefore prefers the SHORTER, less specific name. The
historical namesake won *because* the serving member carries a middle initial.

Every test here calls the matcher directly and asserts on its OUTPUT. Row counts
are deliberately not used as evidence: the "bug class CLOSED" claim was made
twice on row counts alone and was wrong both times, because counts can look
correct by coincidence while the producer is still broken.
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ingest_house_index as ih  # noqa: E402


def _roster(rows, terms):
    """Build the member dicts `match_member_id` consumes, without a DB."""
    members = []
    for bioguide, full_name in rows:
        last_tokens, first_tokens = ih.split_member_name(full_name)
        members.append({
            "bioguide_id": bioguide,
            "full_name": full_name,
            "normalized_name": ih.normalize_name(full_name),
            "last_tokens": last_tokens,
            "first_tokens": first_tokens,
            "state": None,
            "chamber": None,
            "terms": terms.get(bioguide, []),
        })
    return members


# The real production shape, reduced to what matters.
ROWS = [
    ("B000315", "Begich, Nicholas"),      # Sr. — AK, 1971-1973
    ("B001323", "Begich, Nicholas J."),   # III — AK, serving
    ("G000545", "Green, Mark"),           # WI-8, 1999-2007
    ("G000590", "Green, Mark E."),        # TN-7, 2025-01-03..2025-07-21
    ("G000553", "Green, Al"),             # TX, serving
    ("C000640", "Collins, Mac"),          # GA-3, 1993-2005
    ("C001129", "Collins, Mike"),         # GA-10, serving
    ("G000579", "Gallagher, Mike"),       # WI, resigned 2024
    ("G000607", "Gallagher, James"),      # CA, serving from 2026-06-10
    ("N000127", "Nolan, Richard"),        # genuine 32-year gap
]
TERMS = {
    "B000315": [("1971-01-21", "1973-01-03")],
    "B001323": [("2025-01-03", "2027-01-03")],
    "G000545": [("1999-01-06", "2007-01-03")],
    "G000590": [("2025-01-03", "2025-07-21")],
    "G000553": [("2005-01-04", "2027-01-03")],
    "C000640": [("1993-01-05", "2005-01-03")],
    "C001129": [("2023-01-03", "2027-01-03")],
    "G000579": [("2017-01-03", "2024-04-24")],
    "G000607": [("2026-06-10", "2027-01-03")],
    # non-contiguous: served, left for 32 years, returned
    "N000127": [("1975-01-14", "1981-01-03"), ("2013-01-03", "2015-01-03")],
}
MEMBERS = _roster(ROWS, TERMS)
UNDATED = _roster(ROWS, {})   # member_terms not loaded


def test_the_two_defect_cases_now_resolve_to_the_serving_member():
    """Stage 0's reproduction cases, which are the whole reason for this change."""
    assert ih.match_member_id("Nicholas", "Begich", MEMBERS, "2026-06-12") == "B001323"
    assert ih.match_member_id("Mark", "Green", MEMBERS, "2025-06-24") == "G000590"


def test_the_already_correct_collins_pair_does_not_regress():
    """🔴 Mac and Mike Collins are two REAL, DISTINCT people and both were already
    matched correctly. Breaking them would be a new bug of the same shape as the
    one being fixed."""
    assert ih.match_member_id("Michael", "Collins", MEMBERS, "2026-01-20") == "C001129"
    # Mac Collins filing inside his OWN term still resolves to Mac.
    assert ih.match_member_id("Mac", "Collins", MEMBERS, "2001-05-01") == "C000640"


def test_a_lone_candidate_who_was_out_of_office_is_rejected():
    """The Gallagher case: exactly ONE 'James Gallagher'-ish match exists in the
    roster and it is the wrong one (resigned 2024). A uniqueness test alone
    cannot catch this — only the date can."""
    assert ih.match_member_id("Mike", "Gallagher", MEMBERS, "2026-07-01") is None
    # and inside his real term he still matches
    assert ih.match_member_id("Mike", "Gallagher", MEMBERS, "2019-03-01") == "G000579"


def test_currently_serving_member_still_matches():
    assert ih.match_member_id("Al", "Green", MEMBERS, "2026-07-01") == "G000553"


def test_non_contiguous_terms_reject_the_gap_but_accept_both_terms():
    """A single min..max span would wrongly cover the 32-year gap. This is why
    terms are stored as intervals rather than two columns on `members`."""
    assert ih.match_member_id("Richard", "Nolan", MEMBERS, "1977-06-01") == "N000127"
    assert ih.match_member_id("Richard", "Nolan", MEMBERS, "2014-06-01") == "N000127"
    assert ih.match_member_id("Richard", "Nolan", MEMBERS, "1995-06-01") is None


def test_genuine_ambiguity_returns_none_and_does_not_guess():
    """Two people, same name, BOTH in office on the date -> flag, never pick."""
    rows = [("X000001", "Smith, John"), ("X000002", "Smith, John")]
    terms = {"X000001": [("2020-01-03", "2027-01-03")],
             "X000002": [("2020-01-03", "2027-01-03")]}
    both = _roster(rows, terms)
    assert ih.match_member_id("John", "Smith", both, "2026-01-01") is None


def test_fails_open_when_term_data_is_absent():
    """m017 unrun or the loader never run: behaviour must be EXACTLY as before,
    not mass-unmatching. This is what makes the change safe to deploy ahead of
    its data."""
    assert ih.match_member_id("Nicholas", "Begich", UNDATED, "2026-06-12") == "B000315"
    assert ih.match_member_id("Michael", "Collins", UNDATED, "2026-01-20") == "C001129"


def test_fails_open_when_the_filing_has_no_date():
    assert ih.match_member_id("Nicholas", "Begich", MEMBERS, None) == "B000315"


def test_difflib_fallback_is_also_date_filtered():
    """The fallback must not be a hole back to the same bug. 'Nick' is not an
    anchor match for 'Nicholas', so this goes through difflib."""
    assert ih.match_member_id("Nick", "Begich", MEMBERS, "2026-06-12") != "B000315"


def test_load_members_tolerates_a_missing_member_terms_table():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE members (bioguide_id TEXT, full_name TEXT, party TEXT,"
                 " state TEXT, chamber TEXT)")
    conn.execute("INSERT INTO members VALUES ('A000001','Doe, Jane','D','CA','House')")
    members = ih.load_members(conn)
    assert len(members) == 1 and members[0]["terms"] == []


def test_covers_is_inclusive_on_both_boundaries():
    m = {"terms": [("2025-01-03", "2025-07-21")]}
    assert ih._covers(m, "2025-01-03")
    assert ih._covers(m, "2025-07-21")
    assert not ih._covers(m, "2025-01-02")
    # just after the term end is inside the grace window, not outside the term
    assert ih._covers(m, "2025-07-22")
    assert not ih._covers(m, "2026-07-22")


def test_a_departing_member_may_still_file_a_final_ptr():
    """🔴 THE FALSE-POSITIVE CLASS THIS FIX WOULD OTHERWISE CREATE.
    A PTR is due 30-45 days after the trade, so a member who leaves office
    legitimately files afterwards. Waltz filed 3 days after his term ended and
    Manning 108 days after; both attributions are CORRECT. Rejecting them would
    unmatch real filings and make the guard cry wolf every run."""
    waltz = _roster([("W000823", "Waltz, Michael")],
                    {"W000823": [("2023-01-03", "2025-01-20")]})
    manning = _roster([("M001135", "Manning, Kathy E.")],
                      {"M001135": [("2021-01-03", "2025-01-03")]})
    assert ih.match_member_id("Michael", "Waltz", waltz, "2025-01-23") == "W000823"
    assert ih.match_member_id("Kathy", "Manning", manning, "2025-01-27") == "M001135"
    assert ih.match_member_id("Kathy", "Manning", manning, "2025-04-21") == "M001135"


def test_the_grace_window_still_rejects_the_namesake_errors():
    """The grace must not be so wide it re-admits the bug. The real errors are
    21 and 53 years out; the legitimate cases are at most 108 days."""
    assert ih.match_member_id("Nicholas", "Begich", MEMBERS, "2026-06-12") == "B001323"
    assert ih.match_member_id("Mark", "Green", MEMBERS, "2026-06-12") != "G000545"
    # C000640 left office 2005; a 2026 filing is far outside any grace
    assert not ih._covers({"terms": TERMS["C000640"]}, "2026-01-20")


def test_the_grace_window_is_one_sided():
    """No legitimate filing predates taking office, and a symmetric window would
    re-admit a predecessor namesake."""
    m = {"terms": [("2026-06-10", "2027-01-03")]}
    assert not ih._covers(m, "2026-06-01")
    assert ih._covers(m, "2026-06-10")
