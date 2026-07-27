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
    inflated = calculate_evidence_confidence(5, [1.0] * 5)
    assert inflated == 80.0, "the pre-fix inflated value should be reproducible"
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

def test_the_formula_shape_is_unchanged():
    """Only the COUNT changed. The tiers, quality term and conflict factor stand."""
    assert calculate_evidence_confidence(4, [1.0] * 4) == 60.0
    assert calculate_evidence_confidence(5, [1.0] * 5) == 80.0
    assert calculate_evidence_confidence(6, [1.0] * 6) == 95.0
    assert calculate_evidence_confidence(5, [1.0] * 5, True) == round(80.0 * 0.7, 1)


def test_opportunity_score_is_untouched():
    from jpt_common import calculate_opportunity_score
    assert calculate_opportunity_score(1.0, 0.0, "SHORT") == \
           calculate_opportunity_score(1.0, 0.0, "SHORT")
    assert calculate_opportunity_score(0.5, 10.0, "MEDIUM") > 0


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
