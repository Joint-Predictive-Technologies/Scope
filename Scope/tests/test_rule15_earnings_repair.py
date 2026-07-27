"""RULE_15 repair — attribution by CIK, real earnings only, and a safe emit path.

The defect this file exists for: `q=<ticker>` is a BARE full-text search, and every hit
was stored under the queried ticker without checking the `ciks` field the response
already carries. Boeing's political score was computed from *BA Credit Card Trust*
filings — 0 of 4 correct. Attribution is now by CIK, which the response supplies.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection
from scripts import rule_15_earnings_nlp as r15

BOEING_CIK, BOFA_TRUST_CIK, GD_CIK, GDC_CIK = "0000012927", "0001128250", "0000040533", "0001641398"


def _hit(ciks, items=("2.02",), adsh="0000000000-26-000001"):
    return {"_source": {"ciks": list(ciks), "items": list(items), "adsh": adsh,
                        "file_date": "2026-05-01", "period_ending": "2026-03-31"}}


def _seed_tickers(rows):
    conn = db_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS tickers "
                 "(symbol TEXT PRIMARY KEY, company_name TEXT, cik TEXT)")
    conn.executemany("INSERT OR REPLACE INTO tickers(symbol,company_name,cik) VALUES (?,?,?)",
                     rows)
    conn.commit()
    return conn


# --- Stage 3: attribution -------------------------------------------------

def test_boeing_drops_bank_of_america_credit_card_trust():
    """The headline failure: q=BA returned BA Credit Card Trust and it was stored as Boeing."""
    assert r15.hit_matches_issuer(_hit([BOEING_CIK]), BOEING_CIK) is True
    assert r15.hit_matches_issuer(_hit([BOFA_TRUST_CIK]), BOEING_CIK) is False


def test_gd_drops_gd_culture_group():
    assert r15.hit_matches_issuer(_hit([GD_CIK]), GD_CIK) is True
    assert r15.hit_matches_issuer(_hit([GDC_CIK]), GD_CIK) is False


def test_cik_match_is_zero_padding_insensitive():
    assert r15.hit_matches_issuer(_hit(["12927"]), BOEING_CIK) is True
    assert r15.hit_matches_issuer(_hit(["0000012927"]), "12927".zfill(10)) is True


def test_unknown_issuer_cik_drops_rather_than_guesses():
    """No CIK means no attribution — never fall back to the name."""
    assert r15.hit_matches_issuer(_hit([BOEING_CIK]), "") is False
    conn = _seed_tickers([("ZZZZ", "Unknown Co", None)])
    assert r15.issuer_cik(conn, "ZZZZ") == ""
    assert r15.issuer_cik(conn, "NOPE") == ""
    conn.close()


def test_issuer_cik_resolves_and_normalises():
    conn = _seed_tickers([("BA", "Boeing Co", "12927")])
    assert r15.issuer_cik(conn, "BA") == BOEING_CIK
    conn.close()


def test_attribution_rate_is_100pct_on_cik_matched_rows():
    """Re-measured: mixed feed of right/wrong-CIK hits keeps only the right ones."""
    feed = [_hit([BOEING_CIK]), _hit([BOFA_TRUST_CIK]), _hit([BOEING_CIK]),
            _hit(["0000012345"]), _hit([BOFA_TRUST_CIK])]
    kept = [h for h in feed if r15.hit_matches_issuer(h, BOEING_CIK)]
    assert len(kept) == 2
    assert all(BOEING_CIK in h["_source"]["ciks"] for h in kept), "a wrong-CIK hit survived"


# --- Stage 4: real earnings only -----------------------------------------

@pytest.mark.parametrize("items,is_earnings", [
    (["2.02"], True), (["2.02", "9.01"], True),
    (["5.02"], False),          # officer departure
    (["5.07"], False),          # shareholder vote
    (["7.01"], False),          # Reg FD
    (["9.01"], False),          # exhibits only
    ([], False),
])
def test_only_item_202_counts_as_earnings(items, is_earnings):
    assert r15.is_earnings_8k(_hit([BOEING_CIK], items=items)) is is_earnings


# --- Stage 5: denominator, no poison -------------------------------------

def test_word_denominator_ignores_markup_and_base64():
    """The score was 'per 1000 whitespace runs' over raw .txt including tags and
    base64 exhibits, so it moved with attachment size rather than language."""
    prose = "the regulatory environment in washington affects congress " * 5
    # real EDGAR exhibits are line-wrapped base64, so the raw .split() denominator
    # inflates with attachment size — that is the bug
    blob = "<XML><TYPE>EX-99.1</TYPE>" + \
        " ".join(["QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5"] * 200) + "</XML>"
    assert len(r15._visible_words(prose + blob)) < len((prose + blob).split())
    clean, dirty = r15._political_score(prose)[0], r15._political_score(prose + blob)[0]
    assert dirty == pytest.approx(clean, rel=0.25), \
        f"score moved with exhibit size: {clean:.3f} vs {dirty:.3f}"


def test_no_zero_placeholder_is_written_on_a_failed_fetch():
    """A 0.0 row under a UNIQUE accession + INSERT OR IGNORE is permanent — a
    transient failure would poison that filing forever."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "scripts",
                            "rule_15_earnings_nlp.py"), encoding="utf-8").read()
    body = src[src.index("if not text:"):src.index("score, counts = _political_score")]
    # strip comments — the explanatory comment legitimately NAMES "INSERT OR IGNORE"
    code = "\n".join(ln.split("#")[0] for ln in body.splitlines())
    assert "INSERT" not in code.upper(), "a placeholder row is still written on fetch failure"
    assert "RETRY" in body.upper() or "retry" in body


# --- successor CIKs (the XOM case) ---------------------------------------

def test_xom_accepts_its_predecessor_cik():
    """A reorganisation gives a new CIK; older filings stay under the predecessor.
    SEC lists only the current one, so a strict match silently zeroed XOM."""
    conn = _seed_tickers([("XOM", "ExxonMobil Holdings Corp", "2115436")])
    ciks = r15.issuer_ciks(conn, "XOM")
    conn.close()
    assert "0002115436" in ciks, "current CIK missing"
    assert "0000034088" in ciks, "predecessor Exxon Mobil Corp CIK missing"
    assert r15.hit_matches_issuer(_hit(["0000034088"]), ciks) is True
    assert r15.hit_matches_issuer(_hit(["0002115436"]), ciks) is True
    # and an unrelated filer is still dropped
    assert r15.hit_matches_issuer(_hit([BOFA_TRUST_CIK]), ciks) is False


def test_aliases_do_not_loosen_other_tickers():
    conn = _seed_tickers([("BA", "Boeing Co", "12927")])
    ciks = r15.issuer_ciks(conn, "BA")
    conn.close()
    assert ciks == {BOEING_CIK}, f"BA gained an unexpected alias: {ciks}"
    assert r15.hit_matches_issuer(_hit([BOFA_TRUST_CIK]), ciks) is False


def test_hit_matches_issuer_still_accepts_a_plain_string():
    assert r15.hit_matches_issuer(_hit([BOEING_CIK]), BOEING_CIK) is True
    assert r15.hit_matches_issuer(_hit([BOEING_CIK]), "") is False
    assert r15.hit_matches_issuer(_hit([BOEING_CIK]), set()) is False
