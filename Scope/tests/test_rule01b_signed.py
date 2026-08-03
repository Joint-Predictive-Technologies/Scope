#!/usr/bin/env python3
"""
RULE_01B is SIGNED — the gate reads its directional verdict instead of assuming.

Runs under pytest:  pytest tests/test_rule01b_signed.py

Until now `alert_corroborates` short-circuited for every rule outside `SIGNED_RULES`, so a
RULE_01B **sale** corroborated a bullish thesis exactly as well as a purchase. That is the
RTX failure mode, and RULE_01B was deliberately unsigned until its attribution was repaired:
~46% of its sales were described as opens.

All five of its 2026-07-28 audit defects are now fixed, and the verdict was proven correct
PER ROW before signing — 153 of 153 non-retracted stored alerts match a verdict recomputed
independently from tx_type, 0 mismatches.

⚠️ Everything here drives the gate's REAL `alert_corroborates` and `instruments_for`. A
paraphrase of the rule would pass while the gate did something else.
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO = os.path.join(os.path.dirname(__file__), "..")
_GATE_PATH = os.path.join(_REPO, "scripts", "rule_10_corroboration.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load(_GATE_PATH, "rule_10_signed")

import jpt_common  # noqa: E402


def _leg(rule="RULE_01B", corroborates=None, note=None, ticker="ABT"):
    """A gate candidate row. `_row_value` accepts plain dicts, as its docstring states."""
    return {"rule": rule, "ticker": ticker, "severity": "HIGH",
            "corroborates": corroborates, "corroboration_note": note}


# ─────────────────────────────────────────────────────────────────────────────
# 1. THE VERDICT NOW GOVERNS
# ─────────────────────────────────────────────────────────────────────────────

def test_rule01b_is_signed():
    assert "RULE_01B" in jpt_common.SIGNED_RULES
    assert "RULE_06" in jpt_common.SIGNED_RULES, "signing RULE_01B must not unsign RULE_06"


def test_a_purchase_leg_still_corroborates():
    verdict, reason = gate.alert_corroborates(_leg(corroborates=1))
    assert verdict is True
    assert reason == ""


@pytest.mark.parametrize("note", [
    "disposal — bearish direction",
    "partial disposal — bearish direction",
])
def test_a_disposal_leg_no_longer_corroborates(note):
    """The whole point. A disclosed sale is not evidence for a bullish thesis."""
    verdict, reason = gate.alert_corroborates(_leg(corroborates=0, note=note))
    assert verdict is False
    assert reason == note, "the rejection reason must surface, not be swallowed"


def test_an_exchange_leg_does_not_corroborate():
    """Neutral means NOT a corroborating leg — it does not mean 'counts anyway'."""
    verdict, _ = gate.alert_corroborates(
        _leg(corroborates=0, note="exchange — directionally neutral"))
    assert verdict is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. FAIL CLOSED
# ─────────────────────────────────────────────────────────────────────────────

def test_a_null_verdict_fails_CLOSED():
    """Unknown tx_type writes NULL. Absence of evidence is not evidence of a buy."""
    verdict, reason = gate.alert_corroborates(_leg(corroborates=None))
    assert verdict is False
    assert "no direction on record" in reason


def test_a_missing_corroborates_column_fails_CLOSED():
    """`_row_value` tolerates an absent column; it must not soften the verdict.

    Diagnostics and tests pass plain dicts without the column at all.
    """
    verdict, _ = gate.alert_corroborates({"rule": "RULE_01B", "ticker": "ABT"})
    assert verdict is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. THE BLAST RADIUS IS EXACTLY `SIGNED_RULES`
# ─────────────────────────────────────────────────────────────────────────────

def test_rule06_behaviour_is_untouched():
    assert gate.alert_corroborates(_leg(rule="RULE_06", corroborates=1))[0] is True
    assert gate.alert_corroborates(_leg(rule="RULE_06", corroborates=0))[0] is False
    assert gate.alert_corroborates(_leg(rule="RULE_06", corroborates=None))[0] is False


@pytest.mark.parametrize("rule", ["RULE_02", "RULE_CLUSTER", "RULE_11", "RULE_15", "RULE_16"])
def test_unsigned_rules_still_corroborate_unconditionally(rule):
    """Signing RULE_01B must not quietly interrogate anything else.

    An unsigned rule returns True BEFORE the column is read, so even an explicit 0 is
    ignored — that is what makes "the untouched instruments behave identically" provable.
    """
    assert gate.alert_corroborates(_leg(rule=rule, corroborates=0))[0] is True
    assert gate.alert_corroborates(_leg(rule=rule, corroborates=None))[0] is True


# ─────────────────────────────────────────────────────────────────────────────
# 4. IT MOVES THE COUNT, NOT JUST A BOOLEAN
# ─────────────────────────────────────────────────────────────────────────────

def test_instruments_for_drops_the_disposal_leg():
    """`instruments_for` is the choke point every consumer of the gate reaches.

    A boolean flipping somewhere is irrelevant unless the INSTRUMENT COUNT changes —
    that is what decides whether a convergence fires.
    """
    legs = [_leg(corroborates=1), _leg(rule="RULE_06", corroborates=1),
            _leg(rule="RULE_11", corroborates=None)]
    assert sorted(gate.instruments_for(legs)) == ["congressional", "contracts", "insider"]

    legs_with_sale = [_leg(corroborates=0), _leg(rule="RULE_06", corroborates=1),
                      _leg(rule="RULE_11", corroborates=None)]
    got = sorted(gate.instruments_for(legs_with_sale))
    assert "congressional" not in got, (
        f"a RULE_01B sale still contributes the congressional instrument: {got}"
    )
    assert got == ["contracts", "insider"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. THE DEPLOY PRECONDITION, PINNED
# ─────────────────────────────────────────────────────────────────────────────

def test_signing_is_inert_until_the_direction_remap_has_run():
    """⚠️ The one operational risk, encoded so it cannot be forgotten.

    `corroborates` is populated by `scripts/remap_rule01b_direction.py`, which is PREPARED
    AND NOT RUN. Until it runs on a given database EVERY stored RULE_01B alert holds NULL,
    and NULL fails closed. Measured on the 2026-07-28 snapshot:

        remaps applied  -> 16 RULE_01B gate candidates, 11 corroborate   (the intent)
        remaps NOT run  -> 26 RULE_01B gate candidates,  0 corroborate   (a blackout)

    Fail-safe either way, never fail-open — but deploying this before that remap silently
    removes RULE_01B from corroboration entirely. This test asserts the blackout so the
    cost is visible in the suite rather than discovered on prod.
    """
    unremapped = [_leg(corroborates=None) for _ in range(26)]
    assert sum(1 for a in unremapped if gate.alert_corroborates(a)[0]) == 0
    assert gate.instruments_for(unremapped) == [], (
        "un-remapped RULE_01B alerts still contribute an instrument"
    )


def test_a_retracted_row_cannot_count_as_a_leg():
    """Signing closes the gate-honours-retraction gap for RULE_01B, without touching the gate.

    The direction remap deliberately skips retracted rows, so they keep `corroborates=NULL`.
    The gate's candidate query still does not read `lifecycle_stage` — but a retracted
    RULE_01B row now fails the per-alert check instead, which is the same outcome by a
    different route. It does NOT close the gap for any other rule.
    """
    assert gate.alert_corroborates(_leg(corroborates=None))[0] is False
    assert gate.alert_corroborates(_leg(rule="RULE_02", corroborates=None))[0] is True, (
        "the retraction gap is closed for RULE_01B only — do not overclaim it"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5b. END TO END — a REAL emitted row, through the REAL gate
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠️ These exist because a verification pass found the gap. Everything above is dict
# fixtures, and the one DB-level exercise of RULE_01B against the per-alert verdict —
# the parametrised case in test_gate_redesign.py — was removed by this session because
# RULE_01B is no longer an UNSIGNED instrument. The `assert verdict is True` in
# test_rule01b_direction.py should have been INVERTED rather than deleted. Net effect
# was that "a real RULE_01B row in the database is rejected by the gate" was asserted
# nowhere, in the very change whose purpose is that rejection. Closed here.

@pytest.fixture
def seeded_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO members (bioguide_id, full_name, party, state, chamber, is_current) "
        "VALUES ('A000001','Alpha, Aaron','Democratic','NJ','House of Representatives',1)")
    conn.execute("INSERT INTO tickers (symbol, company_name) VALUES ('ABT','Abbott')")
    conn.commit()
    conn.close()
    yield


def _emit_real_first_touch(tx_type):
    """Drive the ACTUAL rule so the verdict is written by the emit path, not by hand."""
    from datetime import date, timedelta
    rule = _load(os.path.join(_REPO, "scripts", "rule_01b_first_touch.py"), "r01b_e2e")
    today = date.today()
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO transactions (id, member_id, raw_ticker_string, transaction_type, "
        "amount_band, transaction_date, filing_date) VALUES "
        "(100,'A000001','ABT',?,'$15,001 - $50,000',?,?)",
        (tx_type, (today - timedelta(days=10)).isoformat(), (today - timedelta(days=3)).isoformat()))
    conn.commit()
    conn.close()
    rule.run(emit=True)


def _gate_view():
    conn = jpt_common.db_connection()
    rows = gate._candidate_alerts(conn, gate.CONVERGENCE_WINDOW_DAYS * 24)
    conn.close()
    r01b = [a for a in rows if a["rule"] == "RULE_01B"]
    return r01b, gate.instruments_for(r01b)


def test_a_real_emitted_SALE_is_rejected_by_the_real_gate(seeded_db):
    """The end-to-end property this whole session exists for."""
    _emit_real_first_touch("sale")

    legs, instruments = _gate_view()
    assert len(legs) == 1, "the sale did not reach the gate's candidate set at all"
    assert legs[0]["corroborates"] == 0, "the emit path did not record the bearish verdict"
    assert gate.alert_corroborates(legs[0])[0] is False
    assert instruments == [], (
        f"a real RULE_01B sale still contributes {instruments} — it is corroborating a "
        "bullish thesis, which is the RTX failure mode this signing exists to stop"
    )


def test_a_real_emitted_PURCHASE_still_corroborates_end_to_end(seeded_db):
    """The other half — signing must not silence the legs that should count."""
    _emit_real_first_touch("purchase")

    legs, instruments = _gate_view()
    assert len(legs) == 1
    assert legs[0]["corroborates"] == 1
    assert gate.alert_corroborates(legs[0])[0] is True
    assert instruments == ["congressional"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. MUTATION
# ─────────────────────────────────────────────────────────────────────────────

def test_unsigning_rule01b_reverts_the_disposal_leg(monkeypatch):
    """Remove RULE_01B from SIGNED_RULES and the sale corroborates again.

    Proves these fixtures discriminate: without the signing they all pass vacuously,
    because an unsigned rule short-circuits to True before the verdict is read.
    """
    monkeypatch.setattr(gate, "SIGNED_RULES", frozenset({"RULE_06"}))

    assert gate.alert_corroborates(_leg(corroborates=0))[0] is True, (
        "unsigning did not revert the sale leg — the fixtures are not testing the signing"
    )
    assert gate.alert_corroborates(_leg(corroborates=None))[0] is True
    assert gate.instruments_for([_leg(corroborates=0)]) == ["congressional"]
