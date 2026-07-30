#!/usr/bin/env python3
"""A CONTRACT'S WEIGHT IS RELATIVE TO WHO WON IT.

`RULE_11`'s severity ladder is absolute — `>=$500M` is CRITICAL for everybody — so a
routine award to a prime contractor and a company-making award to a micro-cap arrive at the
gate as the same size of fact. The idea here is the same one behind the insider leg:
**surprise relative to the entity's own baseline**. $50M is a Tuesday for Lockheed and
transformative for a defence micro-cap.

Two properties carry this file:

  1. `test_the_weight_NEVER_moves_the_instrument_count` — the weight reaches
     `evidence_confidence` and nothing else. `calculate_evidence_confidence` steps on an
     INTEGER (>=3 -> 40, >=4 -> 60, >=5 -> 75); a fractional count of 2.7 would fall below
     the first tier and score base 0, which is the 6.0-vs-20.0 regression this project has
     already fixed once.

  2. `test_a_TOKEN_MATCHED_recipient_can_never_be_boosted` — the asymmetry. A wrong ticker
     plus a plausible market cap produces a confident wrong ratio that no plausibility
     check can catch, because both inputs are individually fine. So the weight may fall on
     any resolved ticker (under-weighting a mis-attributed award is harmless) but may only
     RISE where the recipient came from the curated table.

Caps below are the real values measured on 2026-07-30, injected rather than fetched so this
is a test of the arithmetic and the asymmetry, not of SEC's uptime.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                                      # noqa: E402
from jpt_common import (CONTRACT_WEIGHT_MATERIAL, CONTRACT_WEIGHT_NEUTRAL,  # noqa: E402
                        CONTRACT_WEIGHT_ROUTINE, calculate_evidence_confidence,
                        contract_leg_weight, contractor_attribution_is_exact,
                        db_connection)

# Real caps, measured 2026-07-30 via SEC shares-outstanding x Yahoo last close.
REAL_CAPS = {
    "RTX":  290_104_940_496,
    "LMT":  131_366_099_424,
    "HII":   11_048_889_491,
    "OSK":    8_905_014_408,
    "PSN":    4_313_413_566,   # recovered by the companyfacts fallback; was `unknown`
    "SPCX":            None,   # all four SEC share concepts 404 — it is not a filer
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Caps are injected. Nothing here reaches SEC or Yahoo.

    ⚠️ A `ticker_meta` ROW IS ALSO SEEDED, because `contract_leg_weight` deliberately refuses
    to consult a cap unless one has already been resolved by someone else — the gate must not
    initiate a cold live lookup inside the scheduler's budget. Stubbing `market_cap` alone
    would therefore return neutral for everything and every assertion below would be vacuous.
    """
    import scripts.rule_reddit_collector as rc
    monkeypatch.setattr(rc, "market_cap",
                        lambda conn, ticker, cache=True: REAL_CAPS.get(ticker))
    sys.modules["rule_reddit_collector"] = rc

    conn = db_connection()
    for sym, cap in REAL_CAPS.items():
        conn.execute("INSERT OR REPLACE INTO ticker_meta (symbol, market_cap, cap_updated) "
                     "VALUES (?, ?, datetime('now'))", (sym, cap))
    conn.commit()
    conn.close()


def test_the_gate_never_initiates_a_COLD_cap_lookup():
    """⚠️ RULE_10 MADE ZERO NETWORK REQUESTS BEFORE THIS FEATURE. A ticker nobody has priced
    yet must read as "no opinion" rather than triggering a live SEC + Yahoo resolution inside
    the 300s scheduler budget. Proven by making any cap resolution explode: if the guard is
    removed this raises instead of returning neutral.
    """
    import scripts.rule_reddit_collector as rc

    def _boom(conn, ticker, cache=True):
        raise AssertionError("the gate initiated a live cap lookup for an unpriced ticker")

    conn = db_connection()
    conn.execute("DELETE FROM ticker_meta WHERE symbol = 'ZCOLD'")
    key = _award(conn, "LOCKHEED MARTIN CORP", "ZCOLD", 500_000_000, key="cold")
    conn.commit()
    real, rc.market_cap = rc.market_cap, _boom
    try:
        assert contract_leg_weight(conn, "ZCOLD", key) == CONTRACT_WEIGHT_NEUTRAL
    finally:
        rc.market_cap = real
        conn.close()


def _award(conn, recipient, ticker, amount, key="k"):
    conn.execute("DELETE FROM contracts WHERE award_id = ?", (key,))
    conn.execute(
        """INSERT INTO contracts (recipient_name, ticker, amount, award_id, award_date)
           VALUES (?, ?, ?, ?, '2026-07-19')""", (recipient, ticker, amount, key))
    conn.commit()
    return key


# ── 1. the bands, on real recipients ────────────────────────────────────────

@pytest.mark.parametrize("recipient,ticker,amount,expected,why", [
    # A genuine new award, post-repair. The audit measured RTX's real in-window award at
    # ~$4.69M against a $290B cap — 0.0016% of the company.
    ("RAYTHEON COMPANY", "RTX", 4_690_000, CONTRACT_WEIGHT_ROUTINE,
     "a $4.7M award to a $290B prime is noise"),
    ("LOCKHEED MARTIN CORP", "LMT", 500_000_000, CONTRACT_WEIGHT_ROUTINE,
     "half a billion is still <1% of Lockheed"),
    # >=10% of the company — materially large, and curated attribution
    ("HUNTINGTON INGALLS INDUSTRIES", "HII", 2_000_000_000, CONTRACT_WEIGHT_MATERIAL,
     "18% of HII's market cap is a company-scale event"),
    ("OSHKOSH CORP", "OSK", 1_000_000_000, CONTRACT_WEIGHT_MATERIAL,
     "11% of Oshkosh"),
])
def test_the_weight_tracks_the_real_cap_ratio(recipient, ticker, amount, expected, why):
    conn = db_connection()
    key = _award(conn, recipient, ticker, amount, key=ticker)
    weight = contract_leg_weight(conn, ticker, key)
    conn.close()
    assert weight == expected, f"{why}: ratio={amount / REAL_CAPS[ticker]:.4f} -> {weight}"


def test_the_middle_band_interpolates_and_never_passes_neutral():
    conn = db_connection()
    key = _award(conn, "LOCKHEED MARTIN CORP", "LMT", int(0.03 * REAL_CAPS["LMT"]))
    weight = contract_leg_weight(conn, "LMT", key)
    conn.close()
    assert CONTRACT_WEIGHT_ROUTINE < weight < CONTRACT_WEIGHT_NEUTRAL, weight


def test_a_mega_cap_award_is_LOWERED_not_DROPPED():
    """The instruction was explicit: mega-cap contracts still count. A weight of 0 would
    delete the leg's contribution to confidence, which is not the same as calling it
    routine — and `contracts` must remain a counted instrument either way."""
    conn = db_connection()
    key = _award(conn, "LOCKHEED MARTIN CORP", "LMT", 500_000_000)
    weight = contract_leg_weight(conn, "LMT", key)
    conn.close()
    assert weight > 0, "a routine award must be quieter, not silent"
    assert jpt_common.rule10_instruments(["RULE_11"]) == ["contracts"], (
        "RULE_11 stopped being the contracts instrument — weighting is not exclusion")


# ── 2. the asymmetry — the integrity property ───────────────────────────────

def test_a_TOKEN_MATCHED_recipient_can_never_be_boosted():
    """⚠️ THE LIVE FALSE POSITIVE THIS GUARDS. `resolve_contractor`'s token fallback maps
    "SPACE EXPLORATION TECHNOLOGIES CORP" to `SPCX` at a hardcoded confidence of 90 — but
    SpaceX is private and `SPCX` is an unrelated listed vehicle. The same path mapped
    RAYTHEON to HNST once before, which is why migrations m003/m004 exist.

    Give it a cap that makes the ratio look material and it must STILL land on neutral. It
    may fail to be boosted; it may never be boosted wrongly.
    """
    assert not contractor_attribution_is_exact("SPACE EXPLORATION TECHNOLOGIES CORP")
    conn = db_connection()
    key = _award(conn, "SPACE EXPLORATION TECHNOLOGIES CORP", "HII", 2_000_000_000)
    weight = contract_leg_weight(conn, "HII", key)
    conn.close()
    assert weight == CONTRACT_WEIGHT_NEUTRAL, (
        f"a token-matched recipient was boosted to {weight} — a mis-attributed ticker can "
        f"now inflate a convergence's confidence")


def test_the_SAME_ratio_IS_boosted_for_a_curated_recipient():
    """The control. Without it the test above would pass against a weight function that
    never boosts anything, and the asymmetry would be vacuous — which an earlier draft of
    this change actually was, because `material` was set equal to `neutral`."""
    assert contractor_attribution_is_exact("HUNTINGTON INGALLS INDUSTRIES")
    conn = db_connection()
    key = _award(conn, "HUNTINGTON INGALLS INDUSTRIES", "HII", 2_000_000_000)
    weight = contract_leg_weight(conn, "HII", key)
    conn.close()
    assert weight == CONTRACT_WEIGHT_MATERIAL > CONTRACT_WEIGHT_NEUTRAL, weight


def test_LOWERING_needs_no_proof_of_attribution():
    """Deliberately asymmetric: under-weighting a mis-attributed award cannot manufacture a
    signal, so the conservative direction is allowed on any resolved ticker."""
    conn = db_connection()
    key = _award(conn, "SPACE EXPLORATION TECHNOLOGIES CORP", "RTX", 4_690_000)
    weight = contract_leg_weight(conn, "RTX", key)
    conn.close()
    assert weight == CONTRACT_WEIGHT_ROUTINE, weight


# ── 3. fail closed to NEUTRAL, never to zero and never fabricated ───────────

@pytest.mark.parametrize("label,ticker,key,setup", [
    ("unknown cap (SPCX is not an SEC filer)", "SPCX", "SPCX", True),
    ("no award_key on the alert", "LMT", None, False),
    ("award_key matches no contracts row", "LMT", "missing", False),
    ("no ticker resolved at all", None, "k", False),
])
def test_every_failure_mode_lands_on_NEUTRAL(label, ticker, key, setup):
    conn = db_connection()
    if setup:
        _award(conn, "SPACE EXPLORATION TECHNOLOGIES CORP", "SPCX", 3_047_851_483,
               key="SPCX")
    weight = contract_leg_weight(conn, ticker, key)
    conn.close()
    assert weight == CONTRACT_WEIGHT_NEUTRAL, (
        f"{label} produced {weight} — an unknown cap must mean NO OPINION, not a "
        f"fabricated ratio and not a silently deleted leg")


@pytest.mark.parametrize("amount", [0, -1, -10_000_000_000])
def test_a_zero_or_negative_amount_is_not_a_ratio(amount):
    """⚠️ PARAMETRIZED BECAUSE THE SINGLE-CASE VERSION WAS MISNAMED. With `amount=0` the
    `if not row or not row[0]` guard fires first, so the `amount <= 0` line was dead for
    that fixture and the NEGATIVE branch — the one the test's name promises — was never
    exercised at all. A verification pass showed `amount < -10**18` survived it."""
    conn = db_connection()
    key = _award(conn, "LOCKHEED MARTIN CORP", "LMT", amount)
    weight = contract_leg_weight(conn, "LMT", key)
    conn.close()
    assert weight == CONTRACT_WEIGHT_NEUTRAL


@pytest.mark.parametrize("ticker,key", [(None, "k"), ("LMT", None)])
def test_BOTH_halves_of_the_missing_input_guard_are_load_bearing(ticker, key):
    """Deleting either half of `if not ticker or not award_key` passed the parametrized
    failure-mode test above, because its cases never isolated them."""
    conn = db_connection()
    _award(conn, "LOCKHEED MARTIN CORP", "LMT", 500_000_000, key="k")
    weight = contract_leg_weight(conn, ticker, key)
    conn.close()
    assert weight == CONTRACT_WEIGHT_NEUTRAL


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_NON_FINITE_stored_weight_means_NO_OPINION_not_maximum_boost(bad):
    """⚠️ `min(CEILING, inf)` IS THE CEILING, WHICH IS THE OPPOSITE OF FAILING CLOSED.
    Python's `json` accepts bare `NaN`/`Infinity`, so a hand-edited tag reached the maximum
    boost; and every comparison with `nan` is False, so it slipped clamps silently. Both were
    found by a verification pass — the docstring claimed malformed meant 1.0 and it did not.
    """
    weights = jpt_common._leg_weights_from_tags(
        json.dumps({"rules": ["RULE_11"], "leg_weights": {"RULE_11": bad}},
                   allow_nan=True))
    assert weights == {}, f"{bad!r} survived as {weights}"


# ── 4. where the weight is allowed to land ──────────────────────────────────

def test_the_weight_NEVER_moves_the_instrument_count():
    """⚠️ THE CONSTRAINT THAT SHAPED THIS DESIGN. The tier table takes an int; a fractional
    count silently scores base 0. So the weight travels in the per-leg quality average and
    the count stays exactly as many instruments as the gate found."""
    rules = ["RULE_01B", "RULE_06", "RULE_11"]
    seen = {}
    for label, legs in (("none", None),
                        ("routine", {"RULE_11": CONTRACT_WEIGHT_ROUTINE}),
                        ("material", {"RULE_11": CONTRACT_WEIGHT_MATERIAL})):
        tags = {"rules": rules}
        if legs:
            tags["leg_weights"] = legs
        count, weights = jpt_common._distinct_rule_count("RULE_10", json.dumps(tags))
        assert count == 3, f"{label}: the weight moved the COUNT to {count}"
        seen[label] = calculate_evidence_confidence(count, weights)

    assert seen["routine"] < seen["none"] < seen["material"], seen
    # ...and the first tier is still reached, i.e. a weighted-down fire is still a fire
    assert seen["routine"] >= 40.0, (
        f"a routine contract dropped a genuine 3-instrument fire below the first tier "
        f"({seen['routine']}) — that is the 6.0-vs-20.0 regression returning")


def test_a_CORRUPTED_leg_weight_cannot_inflate_past_the_ceiling():
    """The stored tag is data, and data can be wrong or hand-edited. The bound on how far
    one leg can move a score must not depend on the writer having behaved."""
    tags = json.dumps({"rules": ["RULE_01B", "RULE_06", "RULE_11"],
                       "leg_weights": {"RULE_11": 10_000}})
    _, weights = jpt_common._distinct_rule_count("RULE_10", tags)
    assert max(weights) <= CONTRACT_WEIGHT_MATERIAL, weights


@pytest.mark.parametrize("bad", ['{"rules":["RULE_11"],"leg_weights":"nope"}',
                                 '{"rules":["RULE_11"],"leg_weights":{"RULE_11":"abc"}}',
                                 '{"rules":["RULE_11"],"leg_weights":[1.25]}',
                                 '{"rules":["RULE_11"]}', "not json", ""])
def test_a_MISSING_or_malformed_weight_means_NO_OPINION(bad):
    """Every alert emitted before leg weights existed has no such key. Absent must mean
    1.0, never 0 — otherwise this change would silently rewrite historical confidence.

    ⚠️ ASSERTS `== {}` OUTRIGHT. The first version was `X == {} or all(... for v in X)`, a
    disjunction whose second clause is trivially true when `X` is empty — so for four of the
    five parameters the real assertion was the first clause and the rest was decoration. A
    verification pass called it weak rather than vacuous; asserting the empty dict directly
    is what was meant, and an empty dict is exactly what "no opinion for any leg" is.
    """
    assert jpt_common._leg_weights_from_tags(bad) == {}
    # ...and an absent weight must read as NEUTRAL, not as zero, where it is consumed
    count, weights = jpt_common._distinct_rule_count(
        "RULE_10", '{"rules":["RULE_01B","RULE_06","RULE_11"]}')
    assert weights == [1.0, 1.0, 1.0], weights


# ── 5. THE JOIN — the emitter must actually WRITE the weight it computed ────
#
# ⚠️ THIS SECTION EXISTS BECAUSE ITS ABSENCE HID A DEAD FEATURE. Everything above tests
# `contract_leg_weight` on one side and `_leg_weights_from_tags` / `_distinct_rule_count` on
# the other. Nothing tested the seam between them — so when `_leg_weights` clamped every
# weight to <=1.0 via `min(weights.get("RULE_11", 1.0), w)`, the entire above-neutral path
# was dead in the product and the whole suite stayed green. A curated 18%-of-cap award and
# the token-matched SPCX false positive emitted byte-identical output, making the attribution
# asymmetry a no-op on every persisted score. Two mutations proved the gap: replacing the
# emitted weights with `{}`, and flipping `min` to `max`, both passed.

def _emit(conn, recipient, ticker, amount):
    """Run the REAL emitter on a genuine 3-instrument convergence including a contracts leg."""
    from scripts import rule_10_corroboration as r10
    _award(conn, recipient, ticker, amount, key="AK")
    for rule, key in (("RULE_11", "AK"), ("RULE_15", None), ("RULE_16", None)):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at, award_key)
               VALUES (?, ?, 'HIGH', ?, datetime('now','-2 days'), ?)""",
            (rule, ticker, f"{rule} on {ticker}", key))
    conn.commit()
    r10.run(dry_run=False, window_hours=r10.CONVERGENCE_WINDOW_DAYS * 24)
    row = conn.execute("SELECT tags, evidence_confidence FROM alerts "
                       "WHERE rule='RULE_10' AND ticker=?", (ticker,)).fetchone()
    assert row is not None, "nothing emitted — the fixture no longer reaches the gate"
    return json.loads(row["tags"]), row["evidence_confidence"]


def test_the_emitter_WRITES_the_weight_it_computed():
    """A routine mega-cap award must reach `tags.leg_weights`, not be dropped on the way."""
    conn = db_connection()
    tags, _ = _emit(conn, "LOCKHEED MARTIN CORP", "LMT", 500_000_000)
    conn.close()
    assert tags["leg_weights"] == {"RULE_11": CONTRACT_WEIGHT_ROUTINE}, tags["leg_weights"]


def test_the_emitter_can_write_a_weight_ABOVE_neutral():
    """⚠️ THE REGRESSION TEST FOR THE DEAD BOOST. If the accumulator ever re-seeds from a
    1.0 default, `CONTRACT_WEIGHT_MATERIAL` becomes unreachable and this fails — which is
    the only way to notice, since `_leg_weights` is the sole writer of that key."""
    conn = db_connection()
    tags, _ = _emit(conn, "HUNTINGTON INGALLS INDUSTRIES", "HII", 2_000_000_000)
    conn.close()
    assert tags["leg_weights"] == {"RULE_11": CONTRACT_WEIGHT_MATERIAL}, (
        f"the material boost never reached the emitted alert: {tags['leg_weights']} — "
        f"the attribution asymmetry is a no-op on every persisted score")


def test_the_asymmetry_is_OBSERVABLE_END_TO_END_not_just_in_the_helper():
    """The same ratio, the same cap, two recipients — the emitted alerts must DIFFER.

    This is the assertion whose absence let the bug ship: at the unit level the asymmetry
    was pinned, while end to end the two recipients were indistinguishable.
    """
    conn = db_connection()
    curated, _ = _emit(conn, "HUNTINGTON INGALLS INDUSTRIES", "HII", 2_000_000_000)
    token, _ = _emit(conn, "SPACE EXPLORATION TECHNOLOGIES CORP", "OSK", 1_000_000_000)
    conn.close()
    assert curated["leg_weights"]["RULE_11"] > token["leg_weights"]["RULE_11"], (
        f"curated {curated['leg_weights']} vs token-matched {token['leg_weights']} — a "
        f"mis-attributed recipient is being scored identically to a verified one")
    assert token["leg_weights"]["RULE_11"] == CONTRACT_WEIGHT_NEUTRAL


def test_when_TWO_awards_back_one_leg_the_MOST_CONSERVATIVE_wins():
    """⚠️ THE ONLY SHAPE THAT DISCRIMINATES `min` FROM `max`. With a single award per ticker
    the two are identical, so flipping the accumulator's comparison passed every other test
    here — a verification pass proved it. A leg backed by one routine and one material award
    must take the ROUTINE weight: a single company-scale award must not let a pile of
    Tuesday-sized ones look surprising, and the conservative direction is the one that is
    safe on shaky attribution.
    """
    from scripts import rule_10_corroboration as r10
    conn = db_connection()
    # two awards to the same curated recipient: one routine (<1% of cap), one material (>=10%)
    _award(conn, "HUNTINGTON INGALLS INDUSTRIES", "HII", 20_000_000, key="AK-routine")
    _award(conn, "HUNTINGTON INGALLS INDUSTRIES", "HII", 2_000_000_000, key="AK-material")
    for rule, key in (("RULE_11", "AK-routine"), ("RULE_11", "AK-material"),
                      ("RULE_15", None), ("RULE_16", None)):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at, award_key)
               VALUES (?, 'HII', 'HIGH', ?, datetime('now','-2 days'), ?)""",
            (rule, f"{rule} {key}", key))
    conn.commit()
    r10.run(dry_run=False, window_hours=r10.CONVERGENCE_WINDOW_DAYS * 24)
    row = conn.execute("SELECT tags FROM alerts WHERE rule='RULE_10' AND ticker='HII'"
                       ).fetchone()
    conn.close()
    assert row is not None, "nothing emitted"
    weights = json.loads(row["tags"])["leg_weights"]
    assert weights == {"RULE_11": CONTRACT_WEIGHT_ROUTINE}, (
        f"the accumulator did not take the most conservative award: {weights}")


def test_the_weight_reaches_the_ALERTS_OWN_evidence_confidence():
    """⚠️ IT REACHED ONLY THE THEME. The emitter passes the gate's instrument count to
    `insert_alert` explicitly, and that branch discarded `tags.leg_weights` — so a routine
    contract moved the theme's score while the alert stayed at its baseline. `mode=overwatch`
    sorts by the ALERT, so the downweight had no effect on what a user actually sees ranked.
    """
    conn = db_connection()
    _, routine_ec = _emit(conn, "LOCKHEED MARTIN CORP", "LMT", 500_000_000)
    conn.close()
    conn = db_connection()
    _, unweighted_ec = _emit(conn, "LOCKHEED MARTIN CORP", "ZZUN", 500_000_000)
    conn.close()
    # ZZUN has no cap injected -> unknown -> neutral, so it is the no-opinion control
    assert routine_ec < unweighted_ec, (
        f"a routine contract did not lower the alert's own evidence_confidence "
        f"({routine_ec} vs {unweighted_ec})")
    assert routine_ec >= 40.0, "a weighted-down genuine fire fell below the first tier"


def test_the_ratio_thresholds_are_ordered_and_the_weights_bracket_neutral():
    """A guard on the constants themselves: swap two of them and every test above still
    passes for the wrong reason."""
    assert 0 < jpt_common.CONTRACT_RATIO_ROUTINE < jpt_common.CONTRACT_RATIO_MATERIAL < 1
    assert 0 < CONTRACT_WEIGHT_ROUTINE < CONTRACT_WEIGHT_NEUTRAL < CONTRACT_WEIGHT_MATERIAL
    assert CONTRACT_WEIGHT_NEUTRAL == 1.0, "neutral must be the identity weight"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
