"""ONE source of truth for "may this rule participate in corroboration at all?".

Two sets used to answer that question independently and had silently DIVERGED:
`RULE_10_EXCLUDED` (drives instrument counting) retired RULE_12/13/14, but
`EXCLUDED_FROM_CORROBORATION` (the SQL candidate filter) still admitted them. So a
retired rule could not OPEN a corroboration yet still entered `theme_signals` and
inflated the corroboration's `evidence_confidence` — measured 6.0 -> 81.0 back when the
emitter passed rule NAMES. It now passes INSTRUMENTS, so the leak is smaller, but a
retired rule reaching `theme_signals` at all is still the defect these tests fence.

These tests exist to make that divergence impossible rather than merely fixed.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common
from jpt_common import RULE_10_EXCLUDED
from scripts import rule_10_corroboration as r10

RETIRED = ("RULE_12", "RULE_13", "RULE_14")
NOISE = ("RULE_07", "RULE_OSINT", "RULE_REDDIT", "RULE_ANOMALY", "RULE_10")
LIVE = ("RULE_01B", "RULE_02", "RULE_CLUSTER", "RULE_06", "RULE_08",
        "RULE_09", "RULE_11", "RULE_15", "RULE_16")


def test_the_two_sets_are_the_same_object_of_truth():
    assert r10.EXCLUDED_FROM_CORROBORATION == set(RULE_10_EXCLUDED)


def test_divergence_is_impossible_not_merely_absent(monkeypatch):
    """THE GUARD. Reimporting the gate script with a changed RULE_10_EXCLUDED must
    carry the change through — if someone re-hardcodes the set, this fails."""
    import importlib
    monkeypatch.setattr(jpt_common, "RULE_10_EXCLUDED",
                        set(RULE_10_EXCLUDED) | {"RULE_ZZ_TEST"}, raising=True)
    reloaded = importlib.reload(r10)
    try:
        assert "RULE_ZZ_TEST" in reloaded.EXCLUDED_FROM_CORROBORATION, (
            "EXCLUDED_FROM_CORROBORATION no longer derives from RULE_10_EXCLUDED — "
            "the two sets can diverge again")
    finally:
        monkeypatch.undo()
        importlib.reload(r10)


@pytest.mark.parametrize("rule", RETIRED)
def test_retired_rules_are_excluded_from_BOTH_counting_and_candidacy(rule):
    assert rule in RULE_10_EXCLUDED, f"{rule} can still be counted as an instrument"
    assert rule in r10.EXCLUDED_FROM_CORROBORATION, f"{rule} is still a SQL candidate"
    assert jpt_common.rule10_instruments([rule]) == []


def test_retired_rules_appear_in_the_generated_sql_filter():
    excluded = ",".join(f"'{r}'" for r in r10.EXCLUDED_FROM_CORROBORATION)
    for rule in RETIRED:
        assert f"'{rule}'" in excluded, f"{rule} missing from the SQL candidate filter"


@pytest.mark.parametrize("rule", NOISE)
def test_noise_rules_stay_excluded(rule):
    assert rule in RULE_10_EXCLUDED and rule in r10.EXCLUDED_FROM_CORROBORATION


@pytest.mark.parametrize("rule", LIVE)
def test_live_rules_stay_eligible_for_both(rule):
    assert rule not in RULE_10_EXCLUDED
    assert rule not in r10.EXCLUDED_FROM_CORROBORATION
    # NOT `rule10_instruments([rule])` — that resolves .get(rule, rule) and is therefore
    # non-empty for ANY non-excluded rule, so it can never fail. Assert the MAPPING.
    assert jpt_common.RULE_10_INSTRUMENTS.get(rule), \
        f"{rule} lost its instrument mapping and would become a phantom instrument"


def test_rule10_threshold_window_and_dedup_are_untouched():
    assert jpt_common.RULE_10_MIN_INSTRUMENTS == 3
    assert r10.CONVERGENCE_WINDOW_DAYS == 14
    assert r10.DEDUP_WINDOW_DAYS == 7
    assert r10.MIN_DISTINCT_INSTRUMENTS == jpt_common.RULE_10_MIN_INSTRUMENTS


def test_retired_rules_cannot_reach_the_candidate_pool_end_to_end():
    """The measured defect: retired alerts entering the pool inflated
    evidence_confidence 6.0 -> 81.0. They must not be selected at all now."""
    conn = jpt_common.db_connection()
    for rule in ("RULE_06", "RULE_01B", "RULE_11", *RETIRED):
        conn.execute("INSERT INTO alerts (rule, ticker, headline, severity) "
                     "VALUES (?,?,?,'HIGH')", (rule, "EXCLTEST", f"{rule} on EXCLTEST"))
    conn.commit()
    rows = r10._candidate_alerts(conn, window_hours=24 * r10.CONVERGENCE_WINDOW_DAYS)
    conn.close()
    picked = {r["rule"] for r in rows if r["ticker"] == "EXCLTEST"}
    assert picked == {"RULE_06", "RULE_01B", "RULE_11"}, \
        f"retired rules reached the corroboration candidate pool: {picked}"
