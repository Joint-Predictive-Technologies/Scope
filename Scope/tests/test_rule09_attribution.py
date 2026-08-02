"""RULE_09 ATTRIBUTION — identity must come from an authoritative mapping, never from name
similarity.

════════════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS
════════════════════════════════════════════════════════════════════════════════════

RULE_09 mapped a lobbying client NAME to a ticker with
`difflib.get_close_matches(..., cutoff=0.7)` against `tickers.company_name`. Measured on the
working DB, **31.0%-45.8% of the 216 stored RULE_09 alerts that carry a ticker were
misattributed** — a BAND, not a point: the figure moves with how generic a token must be to
count as identity. (An earlier draft called 39.8% a "strict lower bound"; a verification pass
showed that is method-dependent — a like-for-like ground truth gives 45.8%, an
ultra-conservative one 31.0%.) The true rate exceeds all of them, because sharing a token does
not make an attribution right (`COHERUS BIOSCIENCES` -> `$RCUS`, which is *Arcus* Biosciences).
Authority confirms only 77 of 216 (35.6%).

    IBM CORPORATION        -> $VIRC   (VIRCO MFG CORPORATION)
    HEXCEL CORPORATION     -> $HBIA   (HILLS BANCORPORATION)
    RELX INC               -> $ARDX   (ARDELYX, INC.)
    WIKIMEDIA FOUNDATION   -> $IVDA   (Iveda Solutions, Inc.)
    SAMSUNG SDI AMERICA    -> $LMFA   (LM FUNDING AMERICA, INC.)

RULE_09 is corroboration-eligible, so this is not cosmetic: a fabricated ticker can build a
FALSE convergence on the wrong company. The gate has never fired, so no fake convergence has
shipped — a first fire on that data would have been one.

════════════════════════════════════════════════════════════════════════════════════
WHAT THESE TESTS PIN
════════════════════════════════════════════════════════════════════════════════════

The load-bearing property is **the absence of a fallback**, not a better threshold. The bug
class is "identity reconstructed from a fuzzy projection", and swapping difflib for another
similarity measure would leave it intact. So the tests below assert, for several shapes of
unresolvable name, that the answer is **None** — never a plausible guess.

`test_no_similarity_fallback_survives_anywhere` is the one that must never be weakened: it
feeds names that are *deliberately near-misses* of real companies. Under the old code every
one of them resolved to something.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import jpt_common                        # noqa: E402
import rule_09_lobbying as rule          # noqa: E402


# A controlled stand-in for the `tickers` table: real company_name spellings, including the
# SEC `/DE/` state marker and two genuine ambiguities.
TICKERS: list[tuple[str, str]] = [
    ("IBM",   "INTERNATIONAL BUSINESS MACHINES CORP"),
    ("HXL",   "HEXCEL CORP /DE/"),
    ("RELX",  "RELX PLC"),
    ("RLXXF", "RELX PLC"),                       # ADR vs foreign ordinary -> ambiguous
    ("GOOG",  "Alphabet Inc."),
    ("GOOGL", "Alphabet Inc."),                  # share classes -> ambiguous
    ("AAPL",  "Apple Inc."),
    ("LMT",   "Lockheed Martin Corporation"),
    # The companies difflib actually picked, so a regression re-selects them from this table.
    ("VIRC",  "VIRCO MFG CORPORATION"),
    ("HBIA",  "HILLS BANCORPORATION"),
    ("ARDX",  "ARDELYX, INC."),
    ("IVDA",  "Iveda Solutions, Inc."),
    ("LMFA",  "LM FUNDING AMERICA, INC."),
    ("RCUS",  "Arcus Biosciences, Inc."),
]


# ---------------------------------------------------------------------------
# the three named cases — measured, live, in the working DB
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("client, fabricated", [
    ("IBM CORPORATION",           "VIRC"),
    ("HEXCEL CORPORATION",        "HBIA"),
    ("RELX INC",                  "ARDX"),
    ("WIKIMEDIA FOUNDATION, INC.", "IVDA"),
    ("SAMSUNG SDI AMERICA, INC.", "LMFA"),
    ("COHERUS BIOSCIENCES, INC.", "RCUS"),
])
def test_the_measured_misattributions_can_no_longer_happen(client, fabricated):
    """Each of these is a real (client -> ticker) pair sitting in the working DB today.

    The assertion is deliberately "never the fabricated one", not "always the right one":
    for IBM and RELX the honest answer is a BLANK, and demanding the correct ticker would
    smuggle a coverage requirement into a correctness test.
    """
    assert rule.match_ticker(client, TICKERS) != fabricated


def test_hexcel_resolves_correctly_because_the_SEC_state_marker_is_stripped():
    """HXL is stored as "HEXCEL CORP /DE/". That marker alone is why an otherwise-exact
    name failed to match, and difflib filled the gap with HILLS BANCORPORATION."""
    assert rule.match_ticker("HEXCEL CORPORATION", TICKERS) == "HXL"


# ---------------------------------------------------------------------------
# THE load-bearing property: no fallback of any kind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("client", [
    "IBM CORPORATION",                  # real, listed — but its name is nothing like IBM's
    "WIKIMEDIA FOUNDATION, INC.",       # a non-profit; no ticker exists
    "TL MANAGEMENT",                    # private
    "COIN CENTER INC.",                 # non-profit
    "FAY LAW GROUP",                    # a law firm
    "ZZZ NONEXISTENT PRIVATE HOLDINGS", # matches nothing at all
    "APPLE ORCHARDS OF VERMONT LLC",    # a near-miss of a real company, on purpose
    "LOCKHEED PLUMBING SUPPLY",         # contains a curated key's word, is not that company
])
def test_no_similarity_fallback_survives_anywhere(client):
    """⚠️ DO NOT WEAKEN. The fix must BAN the method, not retune it.

    Every name here is unresolvable by an authoritative mapping. Each must yield None. The
    last two are deliberate near-misses: any similarity- or token-based fallback would
    return AAPL and LMT respectively, which is exactly the failure being guarded against.
    """
    assert rule.match_ticker(client, TICKERS) is None


def test_ambiguity_refuses_rather_than_picking_one():
    """1541 normalized names in the live tickers table map to 2+ symbols. Picking one would
    be a guess wearing an authoritative costume, so ambiguity resolves to None."""
    assert rule.match_ticker("RELX INC", TICKERS) is None       # RELX / RLXXF
    assert rule.match_ticker("ALPHABET INC", TICKERS) is None   # GOOG / GOOGL


# ---------------------------------------------------------------------------
# it still resolves what it legitimately can
# ---------------------------------------------------------------------------

def test_a_clean_exact_name_still_resolves():
    assert rule.match_ticker("Apple Inc.", TICKERS) == "AAPL"
    assert rule.match_ticker("APPLE INCORPORATED", TICKERS) == "AAPL"


def test_the_curated_tier_still_resolves():
    """`jpt_common.CONTRACTOR_OVERRIDES` — the one tier `contractor_attribution_is_exact`
    blesses. Note this must win even against a tickers table that does not list the name."""
    assert rule.match_ticker("LOCKHEED MARTIN CORPORATION", TICKERS) == "LMT"
    assert rule.match_ticker("Lockheed Martin Corp", []) == "LMT"


def test_a_name_the_token_tier_resolves_by_PARTIAL_match_is_refused_here():
    """`resolve_contractor`'s second tier resolves on partial token containment at conf=90.
    RULE_09 must not inherit that behaviour: a name that merely *contains* a company's tokens
    is not that company."""
    partial = "SPACE EXPLORATION TECHNOLOGIES CORP OF DELAWARE"
    table = [("SPCX", "SPACE EXPLORATION TECHNOLOGIES CORP")]
    token_tier = jpt_common.resolve_contractor(partial, table)
    assert token_tier[0] == "SPCX" and token_tier[2] >= 85, (
        "precondition: the token tier resolves this partial name"
    )
    assert rule.match_ticker(partial, table) is None


# ---------------------------------------------------------------------------
# the curated tier must not fire on a DIFFERENT entity that merely says the name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("client, tempting", [
    ("BOEING EMPLOYEES' CREDIT UNION", "BA"),    # ⚠️ REAL client in this corpus
    ("CACIQUE, INC.",                  "CACI"),  # sub-token: CACI is inside CACIQUE
    ("JACOBSEN CONSTRUCTION COMPANY",  "J"),     # sub-token: JACOBS inside JACOBSEN
    ("KBRIDGE PARTNERS LLC",           "KBR"),
    ("TEXTRONICS INC",                 "TXT"),
    ("BOEINGTON FARMS",                "BA"),
])
def test_a_curated_key_does_not_fire_on_a_different_entity(client, tempting):
    """⚠️ EVERY ONE OF THESE RESOLVED TO `tempting` IN THE FIRST VERSION OF THIS FIX.

    Tier 1 used character-level containment, so any name *containing* a single-token curated
    key (20 of the 45 are single tokens: boeing, caci, kbr, jacobs, textron, parsons…) was
    attributed to it. Two distinct defects, both closed:

      * sub-token matches — CACI inside CACIQUE, JACOBS inside JACOBSEN. Fixed by matching
        whole tokens.
      * whole-token matches on a DIFFERENT entity — `BOEING EMPLOYEES' CREDIT UNION` really
        does contain the token BOEING. Token matching alone does NOT fix this; it is fixed by
        refusing when identity-bearing tokens are left over (EMPLOYEES, CREDIT, UNION).

    BECU is not hypothetical: it is a real client in `lobbying_filings` (2024 Q1 $20K ->
    2025 Q1 $40K, +100% YoY). It clears `MIN_YOY_PCT` and is held back only by `MIN_SPEND` —
    a spend floor, not attribution. Found by a verification pass, not by me.
    """
    assert rule.match_ticker(client, TICKERS + [("BA", "BOEING CO"), ("CACI", "CACI INTERNATIONAL INC /DE/")]) != tempting


@pytest.mark.parametrize("client, expected", [
    ("THE BOEING COMPANY",                              "BA"),
    ("RTX CORPORATION AND AFFILIATES",                  "RTX"),
    ("RTX CORPORATION AND AFFILIATES FKA RTX CORPORATION", "RTX"),
    ("LOCKHEED MARTIN CORPORATION",                     "LMT"),
    ("NORTHROP GRUMMAN CORP",                           "NOC"),
])
def test_the_leftover_rule_does_not_break_legitimate_curated_names(client, expected):
    """The leftover rule must reject a different entity without rejecting corporate
    scaffolding. All five are real client spellings in `lobbying_filings`."""
    assert rule.match_ticker(client, TICKERS) == expected


# ---------------------------------------------------------------------------
# the blank reaches the database honestly
# ---------------------------------------------------------------------------

def test_an_unresolved_client_stores_a_blank_ticker_not_a_guess():
    """End to end through RULE_09's real local `insert_alert` against the isolated DB."""
    conn = jpt_common.db_connection()
    client = "WIKIMEDIA FOUNDATION, INC."
    ticker = rule.match_ticker(client, TICKERS)
    assert ticker is None

    label = f"${ticker}" if ticker else client[:30]
    rule.insert_alert(conn, f"Lobbying spike: {label} spend up 150% YoY",
                      ticker, "HIGH", f"{client}, SOME REGISTRANT, Defense, $20K→$50K")
    conn.commit()

    row = conn.execute(
        "SELECT ticker, headline FROM alerts WHERE rule='RULE_09' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert not row["ticker"], f"a guess reached the column: {row['ticker']!r}"
    # The honest form the rule already had: the client's own name, not a fabricated symbol.
    assert "WIKIMEDIA FOUNDATION" in row["headline"]
    assert "$" not in row["headline"].split("spend up")[0]


def test_normalize_company_is_deterministic_and_order_independent():
    """A canonicalizer, not a scorer: equal inputs give equal outputs and nothing depends on
    which row of the tickers table happens to be visited first."""
    assert rule.normalize_company("HEXCEL CORP /DE/") == rule.normalize_company("Hexcel Corporation")
    assert rule.normalize_company("AT&T INC.") == rule.normalize_company("AT AND T")
    assert rule.normalize_company("The Boeing Company") == "BOEING"
    assert rule.normalize_company("") == ""
    assert rule.normalize_company(None) == ""
    forward = rule.match_ticker("Apple Inc.", TICKERS)
    assert forward == rule.match_ticker("Apple Inc.", list(reversed(TICKERS)))
