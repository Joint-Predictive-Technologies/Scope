#!/usr/bin/env python3
"""THE SIGNED INSIDER LEG — an insider corroborates only when they actually BOUGHT.

The gate used to ask one question: is this rule NAME eligible. So every leg was equally
and directionlessly corroborating, and it shipped a false convergence — RTX at exactly 3
instruments where the insider leg was an **exercise-and-sell**, an officer reducing
exposure, counted as bullish confirmation. `rule_06_form4.py` computed the direction,
persisted it, and `rule_10_corroboration.py` never selected `tags`.

Two properties are worth more than the rest of this file:

  1. `test_the_two_buy_definitions_are_EQUIVALENT` — the buy rule now exists twice (SQL
     over `form4_transactions` for the insider-cluster surface, Python for the gate),
     because the gate cannot join to a table that exists in neither prod nor local. Two
     copies of one rule is the exact failure this project has been burned by twice, so
     equivalence is PROVEN over an exhaustive matrix rather than asserted in a comment.

  2. `test_the_RTX_exercise_and_sell_does_NOT_corroborate` — the regression, from the real
     filing's real rows.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                                    # noqa: E402
from jpt_common import (FORM4_SENTINEL_TICKERS, SIGNED_RULES,        # noqa: E402
                        is_genuine_open_market_buy)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import insider_clusters as ic                                        # noqa: E402
import ingest_form4_transactions as ingest                           # noqa: E402


# ── 1. the equivalence proof ────────────────────────────────────────────────
#
# Every transaction code that appears in a real Form 4, both directions, both derivative
# states, and all three 10b5-1 states including the NULL that means "not disclosed".
_CODES = ("P", "S", "M", "A", "F", "G", "D", "C")
_ADS = ("A", "D")
_DERIV = (0, 1)
_PLAN = (None, 0, 1)


def _matrix():
    for code in _CODES:
        for ad in _ADS:
            for deriv in _DERIV:
                for plan in _PLAN:
                    yield code, ad, deriv, plan


def test_the_two_buy_definitions_are_EQUIVALENT():
    """96 combinations through the REAL SQL predicate, compared to the Python twin.

    ⚠️ THIS RUNS `_buy_predicate` ITSELF, not a copy of its text. If someone edits either
    definition — tightens the SQL, forgets a clause in the Python, coerces the tri-state
    NULL — the two disagree on at least one row and this names it. That is the whole
    reason a second definition is tolerable at all.

    `horizon=False` drops only the date clause; dates are not what is under test here and
    a horizon would silently exclude every row for an unrelated reason.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ingest.ensure_tables(conn)

    rows = list(_matrix())
    for i, (code, ad, deriv, plan) in enumerate(rows):
        conn.execute(
            """INSERT INTO form4_transactions
                 (accession, owner_seq, txn_index, ticker, txn_code,
                  acquired_disposed, is_derivative, is_10b5_1, txn_date, shares, price)
               VALUES ('acc', 0, ?, 'ZZTEST', ?, ?, ?, ?, '2026-07-20', 100, 10)""",
            (i, code, ad, deriv, plan))
    conn.commit()

    sql_accepted = {
        r["txn_index"] for r in conn.execute(
            f"SELECT txn_index FROM form4_transactions WHERE "
            f"{ic._buy_predicate(0, horizon=False)}", ic._SENTINEL_TICKERS)}
    conn.close()

    py_accepted = {i for i, (code, ad, deriv, plan) in enumerate(rows)
                   if is_genuine_open_market_buy(code, ad, deriv, plan, "ZZTEST")}

    only_sql = sorted(rows[i] for i in sql_accepted - py_accepted)
    only_py = sorted(rows[i] for i in py_accepted - sql_accepted)
    assert (only_sql, only_py) == ([], []), (
        f"the two buy definitions disagree — SQL-only: {only_sql}; Python-only: {only_py}")
    # and the matrix must actually contain buys, or the equality above is vacuous
    assert py_accepted, "no combination qualified — this test is not testing anything"


def test_the_sentinel_ticker_lists_are_the_same():
    """A present-but-meaningless symbol ("NA") once formed a pseudo-cluster. Both
    definitions must reject the same set, or the gate and the surface disagree."""
    assert tuple(FORM4_SENTINEL_TICKERS) == tuple(ic._SENTINEL_TICKERS)


@pytest.mark.parametrize("ticker", FORM4_SENTINEL_TICKERS)
def test_a_sentinel_ticker_is_not_a_buy(ticker):
    assert not is_genuine_open_market_buy("P", "A", 0, None, ticker)


# ── 2. what is and is NOT a qualifying buy ──────────────────────────────────

def test_a_plain_open_market_purchase_IS_a_buy():
    assert is_genuine_open_market_buy("P", "A", 0, None, "RTX")
    assert is_genuine_open_market_buy("P", "A", 0, 0, "RTX")


@pytest.mark.parametrize("code,ad,deriv,plan,why", [
    ("S", "D", 0, 0,    "a SALE"),
    ("M", "A", 0, 0,    "an option EXERCISE — the RTX shape"),
    ("M", "D", 0, 0,    "the disposal half of an exercise"),
    ("A", "A", 0, 0,    "a GRANT, not an open-market buy"),
    ("G", "A", 0, 0,    "a GIFT"),
    ("F", "D", 0, 0,    "shares withheld for tax"),
    ("D", "D", 0, 0,    "a disposition back to the issuer"),
    ("P", "D", 0, 0,    "code P but DISPOSED — the acquired_disposed half"),
    ("P", "A", 1, 0,    "a DERIVATIVE right, not the security"),
    ("P", "A", 0, 1,    "a 10b5-1 PLANNED trade — no timing conviction"),
])
def test_what_is_NOT_a_qualifying_buy(code, ad, deriv, plan, why):
    assert not is_genuine_open_market_buy(code, ad, deriv, plan, "RTX"), why


def test_an_UNDISCLOSED_10b5_1_is_KEPT_not_treated_as_planned():
    """NULL means the filing did not say (pre-2022). Coercing it to "planned" would
    silently delete every older genuine buy; coercing it to 0 is what the SQL twin does
    via COALESCE, and the equivalence test above pins that they agree."""
    assert is_genuine_open_market_buy("P", "A", 0, None, "RTX")


@pytest.mark.parametrize("code,ad,deriv", [
    (None, "A", 0), ("P", None, 0), ("P", "A", None)])
def test_a_MISSING_field_fails_closed(code, ad, deriv):
    """SQL's `NULL = 'P'` is NULL, i.e. NOT TRUE, so the row is dropped. The Python twin
    must drop it too — absence of evidence is not evidence of a buy."""
    assert not is_genuine_open_market_buy(code, ad, deriv, 0, "RTX")


def test_a_MISSING_ticker_fails_closed():
    assert not is_genuine_open_market_buy("P", "A", 0, 0, None)


def test_lower_case_fails_closed_rather_than_being_normalised():
    """Deliberate: the SQL twin uses binary collation, so 'p' does not match there. If
    this normalised, the two definitions would diverge on exactly this input while the
    equivalence test (which uses the canonical upper-case domain) stayed green."""
    assert not is_genuine_open_market_buy("p", "A", 0, 0, "RTX")
    assert not is_genuine_open_market_buy("P", "a", 0, 0, "RTX")


# ── 3. the blast radius is exactly one rule ─────────────────────────────────

def test_only_RULE_06_and_RULE_01B_are_signed_so_far():
    """⚠️ THIS IS THE GUARD ON SCOPE CREEP, AND ON A REAL HAZARD.

    Signing a leg whose attribution is known-broken puts a confident sign on data known to
    be wrong — it makes a future false convergence look MORE credible. A rule joins only
    when its attribution repairs land, and this test is where that decision gets recorded.

    RULE_15 must NOT be here: it misattributed *rituximab* to RTX.

    RULE_01B JOINED 2026-08-02. It was blocked because ~46% of its sales were mislabelled
    as opens; all five of its 2026-07-28 audit defects are now repaired on main (chronology,
    ticker key, direction, severity, window). Its verdict was proven correct PER ROW before
    signing — 153 of 153 non-retracted stored alerts match a verdict recomputed
    independently from tx_type, 0 mismatches. Behaviour is pinned in
    tests/test_rule01b_signed.py.

    ⚠️ Signing RULE_01B has a DEPLOY precondition: `corroborates` is populated by
    scripts/remap_rule01b_direction.py, which is prepared-not-run. Until it runs, every
    stored RULE_01B alert is NULL and fails closed — measured 26 gate candidates -> 0
    corroborating. Fail-safe, but a blackout.
    """
    assert SIGNED_RULES == frozenset({"RULE_06", "RULE_01B"}), (
        "SIGNED_RULES changed — a rule may only be signed once its ATTRIBUTION is "
        "repaired, not merely once its direction is readable")
    for blocked in ("RULE_15", "RULE_09", "RULE_16", "RULE_02"):
        assert blocked not in SIGNED_RULES


def test_the_signed_rules_are_all_gate_eligible_instruments():
    """A signed rule that is excluded from the gate would be a no-op with a comment."""
    for rule in SIGNED_RULES:
        assert rule not in jpt_common.RULE_10_EXCLUDED
        assert jpt_common.rule10_instruments([rule]), f"{rule} contributes no instrument"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
