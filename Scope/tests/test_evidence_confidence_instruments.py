"""evidence_confidence counts distinct INSTRUMENTS, not rule names.

D1 made the GATE count instruments — several rules can read one source. The evidence
path kept counting rule NAMES, so three views of the congressional `transactions` feed
(RULE_01B + RULE_02 + RULE_CLUSTER) inflated a corroboration's confidence as though
three independent sources agreed.

Measured before the fix: trio + contracts + insider scored **80.0** on 5 rule names,
where 3 instruments is the truth.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common
from jpt_common import (_distinct_rule_count, calculate_evidence_confidence,
                        rule10_instruments)

TRIO_PLUS = ["RULE_01B", "RULE_02", "RULE_CLUSTER", "RULE_11", "RULE_06"]
GENUINE_3 = ["RULE_06", "RULE_11", "RULE_09"]


def _tags(rules): return json.dumps({"rules": rules})


def _ec(rules):
    n, w = _distinct_rule_count("RULE_10", _tags(rules))
    return n, calculate_evidence_confidence(n, w)


# --- the crafted fixture the brief asks for ------------------------------

def test_congressional_trio_counts_three_instruments_not_five_rules():
    n, _ = _ec(TRIO_PLUS)
    assert len(set(TRIO_PLUS)) == 5, "fixture must have 5 distinct rule NAMES"
    assert n == 3, f"expected 3 instruments (congressional, contracts, insider), got {n}"
    assert rule10_instruments(TRIO_PLUS) == ["congressional", "contracts", "insider"]


def test_the_inflated_value_drops_to_the_honest_one():
    """80.0 was the inflated number. The honest 3-corroborator value is what remains."""
    _, honest = _ec(TRIO_PLUS)
    inflated = calculate_evidence_confidence(5, [1.0] * 5)   # 5 rule NAMES, as before
    assert inflated == 95.0, "the pre-fix inflated value should be reproducible"
    assert honest == 60.0, f"expected the honest 3-instrument value, got {honest}"
    assert honest < inflated, f"confidence did not drop: {honest} vs {inflated}"


def test_a_genuine_three_instrument_fire_is_unchanged():
    """Three genuinely different sources must score exactly as before."""
    n, ec = _ec(GENUINE_3)
    assert n == 3
    assert len(rule10_instruments(GENUINE_3)) == 3
    assert ec == calculate_evidence_confidence(3, [1.0] * 3)


def test_more_instruments_still_scores_higher():
    """The formula's gradient must survive — this is a count change, not a flattening."""
    a, _ = _distinct_rule_count("RULE_10", _tags(GENUINE_3))
    b, _ = _distinct_rule_count("RULE_10", _tags(GENUINE_3 + ["RULE_08", "RULE_15"]))
    assert b > a
    assert calculate_evidence_confidence(b, [1.0] * b) > \
           calculate_evidence_confidence(a, [1.0] * a)


# --- the divergence guard ------------------------------------------------

def test_evidence_count_and_gate_count_are_one_source(monkeypatch):
    """Mutating the gate's instrument map must move the EVIDENCE count too.

    Same failure class as the two exclusion sets: two places answering
    "how many independent things corroborate this?" and drifting apart.
    """
    before, _ = _distinct_rule_count("RULE_10", _tags(TRIO_PLUS))
    gate_before = len(rule10_instruments(TRIO_PLUS))
    assert before == gate_before, "evidence and gate already disagree"

    # collapse contracts into congressional -> the gate sees one fewer instrument
    monkeypatch.setitem(jpt_common.RULE_10_INSTRUMENTS, "RULE_11", "congressional")
    after, _ = _distinct_rule_count("RULE_10", _tags(TRIO_PLUS))
    gate_after = len(rule10_instruments(TRIO_PLUS))

    assert gate_after == gate_before - 1, "the gate's own count did not move"
    assert after == gate_after, \
        f"evidence count ({after}) diverged from the gate's ({gate_after})"


def test_evidence_path_does_not_reimplement_the_instrument_map():
    """It must CALL rule10_instruments, not copy the mapping."""
    import ast, inspect
    src = inspect.getsource(_distinct_rule_count)
    assert "rule10_instruments" in src, "it must CALL the gate's authority"
    # strip the docstring and comments — they legitimately NAME instruments while
    # explaining the fix, so a raw substring check trips on the explanation itself
    fn = ast.parse(src.lstrip()).body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]
    code = ast.dump(fn)
    for name in ("congressional", "insider", "senate-lda", "contracts"):
        assert name not in code, f"the instrument map appears copied inline: {name}"


# --- scope: nothing else moved -------------------------------------------

def test_the_tiers_are_in_the_gates_units():
    """THE COUPLING THAT BROKE: the first tier must BE the gate's threshold.

    The tiers were 4/5/6 while the gate fired at 3 instruments, so every minimum
    corroboration fell below the first tier and scored base=0. Rescaled to 3/4/5
    (human-signed-off 2026-07-27). This test fails if either side moves alone.
    """
    from jpt_common import RULE_10_MIN_INSTRUMENTS
    assert RULE_10_MIN_INSTRUMENTS == 3

    # a fire AT the gate's threshold must clear the first tier, not sit under it
    at_gate = calculate_evidence_confidence(RULE_10_MIN_INSTRUMENTS, [1.0])
    below   = calculate_evidence_confidence(RULE_10_MIN_INSTRUMENTS - 1, [1.0])
    assert at_gate >= 60.0 and below <= 20.0, (
        f"tier boundary is not at the gate: {below} -> {at_gate}. If "
        "RULE_10_MIN_INSTRUMENTS moved, move the first tier with it.")

    # the shape itself: three tiers, the quality term, the conflict factor, the clamp
    assert calculate_evidence_confidence(3, [1.0] * 3) == 60.0
    assert calculate_evidence_confidence(4, [1.0] * 4) == 80.0
    assert calculate_evidence_confidence(5, [1.0] * 5) == 95.0
    assert calculate_evidence_confidence(9, [1.0]) == 95.0          # clamped by tiers
    assert calculate_evidence_confidence(4, [1.0] * 4, True) == round(80.0 * 0.7, 1)
    assert calculate_evidence_confidence(2, []) == 10.0             # no quality -> 0.5


def test_a_corroboration_outranks_its_own_constituent_signals():
    """The invariant the rescale exists to restore.

    A minimum RULE_10 fire persists 3 instruments at RULE_10's own 'Derived' source
    weight (0.3) — the WORST case. It must still beat a lone Primary-quality alert.
    Before the rescale this was 6.0 vs 20.0: a corroboration ranked at one THIRD of
    the signals it was built from, and api/routers/alerts.py mode=overwatch orders
    on this column.
    """
    worst_corroboration = calculate_evidence_confidence(3, [0.3])
    best_single         = calculate_evidence_confidence(1, [1.0])
    assert worst_corroboration == 46.0 and best_single == 20.0
    assert worst_corroboration > best_single


def test_opportunity_score_is_untouched():
    """The OTHER axis must not move. Pinned to LITERALS, deliberately.

    This test used to assert f(x) == f(x), which is true for any deterministic
    function and therefore guarded nothing — a verifier mutated `novelty*40` to
    `novelty*41` and it still passed. Literals make the mutation red.
    """
    from jpt_common import calculate_opportunity_score as opp
    assert opp(1.0, 0.0, "SHORT") == 62.0
    assert opp(1.0, 0.0, "IMMEDIATE") == 65.0
    assert opp(0.5, 10.0, "MEDIUM") == 35.0
    assert opp(0.1, 90.0, "LONG") == 0.0        # clamped, not negative


def test_gate_firing_logic_is_untouched():
    from scripts import rule_10_corroboration as r10
    assert jpt_common.RULE_10_MIN_INSTRUMENTS == 3
    assert r10.CONVERGENCE_WINDOW_DAYS == 14
    assert r10.DEDUP_WINDOW_DAYS == 7


def test_forward_only_no_historical_score_rewrite():
    """Detection-time scores are immutable — no site may UPDATE evidence_confidence."""
    import re
    hits = []
    for root, _d, files in os.walk(os.path.join(os.path.dirname(__file__), "..")):
        if "/tests" in root or "__pycache__" in root or "/.git" in root:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            code = "\n".join(l.split("#")[0] for l in
                             open(path, encoding="utf-8").read().splitlines())
            # any UPDATE touching evidence_confidence, across newlines
            for m in re.finditer(r"UPDATE\s+alerts\s+SET([\s\S]{0,400}?)(?:\"\"\"|'''|\")", code, re.I):
                if "evidence_confidence" in m.group(1):
                    hits.append(os.path.relpath(path))
    # enrich_scores backfills UNSCORED rows only; that is the accepted path
    assert all("enrich" in h or "jpt_common" in h for h in hits), \
        f"a site rewrites historical evidence_confidence: {hits}"


def test_rule_cluster_no_longer_passes_a_member_count_as_corroborators():
    """A 5-member cluster is ONE instrument, but passed n=5 and scored 80.0."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "scripts",
                            "rule_cluster.py"), encoding="utf-8").read()
    assert "distinct_rule_count=1," in src
    assert "distinct_rule_count=n," not in src


# --- BEHAVIOURAL guards over the two sites unit tests could not reach ----
#
# A verifier mutated the RULE_10 emitter back to `distinct_rule_count=rule_count`
# and the whole suite stayed GREEN (455 passed). Same for the evidence router's
# `min(len(rules) * 10, 60)`. The single most important line in the change — what
# the emitter actually PERSISTS — was covered by nothing. These two tests exercise
# the real code paths end to end so those mutations go red.

def _seed_trio_plus(conn, ticker="TRIOPLUS"):
    """5 rule names / 3 instruments, inside the convergence window."""
    for rule in TRIO_PLUS:
        conn.execute(
            "INSERT INTO alerts (rule, ticker, severity, headline, created_at) "
            "VALUES (?, ?, 'HIGH', ?, datetime('now','-1 hours'))",
            (rule, ticker, f"{rule} on {ticker}"))
    conn.commit()


def test_emitter_persists_the_instrument_count_not_the_rule_count():
    """The RULE_10 alert row itself — run the real gate, read what it wrote."""
    from scripts import rule_10_corroboration as r10
    conn = jpt_common.db_connection()
    _seed_trio_plus(conn)
    r10.run(dry_run=False)

    row = conn.execute(
        "SELECT evidence_confidence, tags FROM alerts "
        "WHERE rule='RULE_10' AND ticker='TRIOPLUS' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None, "the gate did not fire — fixture is not exercising the emitter"

    tags = json.loads(row[1])
    assert tags["rule_count"] == 5, "fixture must still carry 5 rule NAMES for provenance"
    assert tags["instrument_count"] == 3

    # 3 instruments at RULE_10's own 'Derived' source weight (0.3) -> 40 + 6 = 46.0.
    # Under the old rule-NAME count this row persisted 5 instruments' worth (81.0);
    # before the tier rescale the correct count scored 6.0, below a lone alert's 20.0.
    expected = calculate_evidence_confidence(3, [0.3])
    assert row[0] == expected == 46.0, (
        f"persisted {row[0]}, expected {expected}; 81.0 means the emitter reverted to "
        "counting rule names, 6.0 means the tiers slid back out of the gate's units")


def test_evidence_router_breakdown_counts_instruments():
    """api/routers/evidence.py — the drawer the homepage card must agree with."""
    from api.routers.evidence import _confidence_breakdown
    alert = {"rule": "RULE_10", "ticker": "TRIOPLUS", "severity": "HIGH",
             "created_at": "2026-07-27 12:00:00", "tags": _tags(TRIO_PLUS)}
    b = _confidence_breakdown(alert, [])
    # 3 instruments * 10 = 30. Five rule names would give 50.
    assert b["components"]["Distinct instrument count"] == 30, b["components"]
    assert "Eligible rule count" not in b["components"]


def test_single_rule_branch_does_not_pay_three_times_for_one_source():
    """The OTHER branch of the same function — missed on the first pass."""
    from api.routers.evidence import _confidence_breakdown
    alert = {"rule": "RULE_06", "ticker": "X", "severity": "HIGH",
             "created_at": "2026-07-27 12:00:00", "tags": ""}
    trio = [{"rule": r} for r in ("RULE_01B", "RULE_02", "RULE_CLUSTER")]
    b = _confidence_breakdown(alert, trio)
    # one instrument (congressional) * 8 = 8. Three rule names hit the 24 cap.
    assert b["components"]["Corroborating signals"] == 8, b["components"]


def test_receipt_calls_them_instruments_not_rule_names():
    """api/receipts.py said '5 independent signals' about 3 instruments."""
    from api import receipts
    alert = {"rule": "RULE_10", "ticker": "TRIOPLUS", "severity": "HIGH",
             "created_at": "2026-07-27 12:00:00",
             "tags": json.dumps({"rules": TRIO_PLUS, "rule_count": 5,
                                 "instruments": ["congressional", "contracts", "insider"],
                                 "instrument_count": 3})}
    r = receipts.build_receipt(alert) if hasattr(receipts, "build_receipt") else None
    if r is None:                                    # name-agnostic: find the builder
        fn = next(v for k, v in vars(receipts).items()
                  if callable(v) and "receipt" in k and not k.startswith("_"))
        r = fn(alert)
    assert r is not None and "3 independent signals" in r["summary"], r["summary"]


def test_homepage_hero_does_not_reimplement_the_instrument_map():
    """The browser must READ tags.instrument_count, never recount rule names.

    Pre-fix, index.html's corrConfidence() computed min(rules.length*10, 60) — the
    exact line the server fix removed — so the same alert read 100/100 on the
    homepage and 80/100 in the evidence drawer.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "api", "static", "index.html")).read()
    assert "tags.instrument_count" in src, "hero card is not reading the emitted count"
    assert "Math.min(rules.length * 10, 60)" not in src, \
        "hero card is recounting rule NAMES again"
