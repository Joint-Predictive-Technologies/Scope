"""RULE_09 IS CONTEXT, NOT A GATE LEG — pinned by CONSEQUENCE, across every consumer.

════════════════════════════════════════════════════════════════════════════════════
WHY THIS FILE EXISTS, AND WHY IT IS NOT AN EXTENSION OF THE BASKET-CLASS TEST
════════════════════════════════════════════════════════════════════════════════════

RULE_08 was excluded for being BASKET-KEYED, and `tests/test_basket_rule_gate_class.py`
holds it out structurally: an AST sweep finds every rule whose ticker comes from a hardcoded
lookup table, and asserts none of them contributes a gate instrument.

⚠️ RULE_09 IS NOT IN THAT CLASS. `basket_shape.scan_repo()` returns RULE_08, RULE_12,
RULE_ADSB, RULE_OSINT and RULE_TELEGRAM_OSINT — **not RULE_09**, whose ticker comes from a
real name resolution against the `tickers` table. So "extend the basket-class test" would add
an assertion that never evaluates RULE_09 at all: it would stay green whether or not the
exclusion existed. That is a test passing for the wrong reason, which is precisely the failure
this project keeps meeting.

RULE_09 is excluded for a different reason — it is CONTEXT. Lobbying spend measures influence
on GOVERNMENT; it is not a claim about a company's securities, so it is context around a
thesis rather than an independent instrument confirming one. There is no AST signature for
"this is context", so the honest pin is by CONSEQUENCE: RULE_09 must contribute no instrument,
and must do so through every path that reads gate eligibility.

This fails if someone deletes RULE_09 from `RULE_10_EXCLUDED`, if a second eligibility source
appears that still admits it, if `rule10_instruments` is refactored so the exclusion stops
being consulted, or if the SQL candidate filter is rebuilt from a different list. All four were
attempted by a verification pass and all four were caught.

⚠️ WHAT IT DOES **NOT** SURVIVE, STATED PLAINLY: the mechanism is keyed on the rule's NAME, so
**re-labelling the lobbying rule re-admits it** with this whole file green —

    RULE_09B / RULE_LOBBY  ->  not in RULE_10_EXCLUDED, not in RULE_10_INSTRUMENTS
                           ->  ['RULE_09B','congressional','insider']  gate TRUE

and it comes back as its own PHANTOM instrument, because `rule10_instruments` resolves
`.get(rule, rule)`. That is inherent to the exclusion mechanism as a whole — RULE_08 and every
retired rule share it — and is not introduced here. It is recorded so "rot-proof" is not read
as more than it is: proof against deletion, a second source, a refactor and a rebuilt filter —
**not** against renaming the source.

════════════════════════════════════════════════════════════════════════════════════
THE MEASURED BEFORE/AFTER
════════════════════════════════════════════════════════════════════════════════════

    ['RULE_01B','RULE_06']            -> 2 instruments, gate FALSE
    ['RULE_01B','RULE_06','RULE_09']  -> 3 instruments, gate TRUE      <- before
                                      -> 2 instruments, gate FALSE     <- after

So a real leg was removed. The justification is the Q20 context decision on its own.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import jpt_common                                       # noqa: E402
from jpt_common import (RULE_10_EXCLUDED, RULE_10_INSTRUMENTS,   # noqa: E402
                        RULE_10_MIN_INSTRUMENTS,
                        rule10_instruments, rule10_is_valid)
from scripts import rule_10_corroboration as r10        # noqa: E402

# The exact triple that used to fire, and the eligible control that still must.
WAS_FIRING = ["RULE_01B", "RULE_06", "RULE_09"]
STILL_FIRES = ["RULE_01B", "RULE_06", "RULE_11"]


def test_the_triple_that_used_to_fire_no_longer_does():
    """THE headline consequence. Before the exclusion this reached 3 instruments."""
    assert rule10_instruments(WAS_FIRING) == ["congressional", "insider"]
    assert len(rule10_instruments(WAS_FIRING)) < RULE_10_MIN_INSTRUMENTS
    assert not rule10_is_valid(WAS_FIRING)


def test_an_otherwise_identical_set_with_a_real_third_leg_still_fires():
    """The positive control. Without it the test above could pass because the gate is
    broken outright rather than because RULE_09 stopped counting."""
    assert len(rule10_instruments(STILL_FIRES)) >= RULE_10_MIN_INSTRUMENTS
    assert rule10_is_valid(STILL_FIRES)


def test_rule09_contributes_nothing_on_its_own_or_alongside_anything():
    """Additivity: RULE_09 must never change an instrument set, in either direction."""
    assert rule10_instruments(["RULE_09"]) == []
    for base in (["RULE_06"], ["RULE_01B", "RULE_06"], STILL_FIRES):
        assert rule10_instruments(base + ["RULE_09"]) == rule10_instruments(base), base


def test_rule09_keeps_its_instrument_MAPPING_so_it_cannot_become_a_phantom():
    """⚠️ The mapping is deliberately NOT deleted. `rule10_instruments` resolves
    `.get(rule, rule)`, so an eligible-but-unmapped rule falls back to its own NAME and
    becomes its own phantom instrument — the trap that let three 'retired' rules count as
    three legs. Exclusion is the fix; removing the mapping would be the bug."""
    assert RULE_10_INSTRUMENTS.get("RULE_09") == "senate-lda"
    assert "RULE_09" in RULE_10_EXCLUDED


def test_the_senate_lda_instrument_is_now_unreachable():
    """RULE_12 (retired) was the only other member, so the instrument has no eligible rule.
    Stated explicitly because it is a real consequence of this change, not an accident."""
    eligible = [r for r in RULE_10_INSTRUMENTS if r not in RULE_10_EXCLUDED]
    assert [r for r in eligible if RULE_10_INSTRUMENTS[r] == "senate-lda"] == []


@pytest.mark.parametrize("variant", ["rule_09", "Rule_09", "rUlE_09"])
def test_the_exclusion_survives_casing(variant):
    """The gate case-folds rule names; a lowercase RULE_09 must not sneak back in."""
    assert rule10_instruments(["RULE_01B", "RULE_06", variant]) == \
           rule10_instruments(["RULE_01B", "RULE_06"])
    assert not rule10_is_valid(["RULE_01B", "RULE_06", variant])


# ---------------------------------------------------------------------------
# no second source of truth may re-admit it
# ---------------------------------------------------------------------------

def test_every_eligibility_consumer_agrees_rule09_is_excluded():
    """The 'no second list' guarantee, asserted against the consumers themselves rather
    than by reading `RULE_10_EXCLUDED` twice."""
    assert "RULE_09" in RULE_10_EXCLUDED                      # instrument counting
    assert "RULE_09" in r10.EXCLUDED_FROM_CORROBORATION       # the SQL candidate filter
    assert not jpt_common.rule10_is_valid(["RULE_09"])


def test_rule09_cannot_reach_the_candidate_pool_END_TO_END():
    """⚠️ THE FIRST VERSION OF THIS TEST WAS A TAUTOLOGY AND A VERIFIER CAUGHT IT.

    It read:

        excluded_sql = ",".join(f"'{r}'" for r in sorted(r10.EXCLUDED_FROM_CORROBORATION))
        assert "'RULE_09'" in excluded_sql

    — which re-derives the join from the same set it claims to be checking and **never calls
    `_candidate_alerts` at all**, while its docstring promised "if the filter is ever rebuilt
    from a different list, this catches it". It would not. The verifier simulated exactly that
    rot (`... for r in EXCLUDED_FROM_CORROBORATION - {"RULE_09"}`) and the assertion stayed
    green while a RULE_09 alert entered the pool: the instrument COUNT stayed correct at 3,
    but `tags.rules` and `theme_signals` picked up a phantom FOURTH leg — the same defect
    already fixed once for RULE_12/13/14, where stored leg lists silently moved
    `evidence_confidence` 6.0 -> 81.0 at read time.

    So this now drives the real SQL, mirroring
    `test_exclusion_single_source.py::test_retired_rules_cannot_reach_the_candidate_pool_end_to_end`.
    """
    conn = jpt_common.db_connection()
    for rule in ("RULE_06", "RULE_01B", "RULE_11", "RULE_09"):
        conn.execute("INSERT INTO alerts (rule, ticker, headline, severity) "
                     "VALUES (?,?,?,'HIGH')", (rule, "CTXTEST", f"{rule} on CTXTEST"))
    conn.commit()
    rows = r10._candidate_alerts(conn, window_hours=24 * r10.CONVERGENCE_WINDOW_DAYS)
    conn.close()

    picked = {r["rule"] for r in rows if r["ticker"] == "CTXTEST"}
    assert "RULE_09" not in picked, (
        "RULE_09 reached the corroboration candidate pool — it would appear in tags.rules "
        f"and theme_signals as a phantom leg. picked={sorted(picked)}"
    )
    # The pool must still be populated, or the assertion above passes vacuously.
    assert picked == {"RULE_06", "RULE_01B", "RULE_11"}, sorted(picked)


def test_a_freshly_added_rule_is_governed_by_the_same_mechanism():
    """The class check: exclusion is driven by membership of ONE set, not by anything
    specific to RULE_09. Planting a name in that set must have the identical effect —
    which is what makes this a mechanism rather than a special case.
    """
    import importlib
    planted = "RULE_ZZ_CONTEXT_PROBE"
    original = set(jpt_common.RULE_10_EXCLUDED)
    try:
        jpt_common.RULE_10_EXCLUDED.add(planted)
        reloaded = importlib.reload(r10)
        assert planted in reloaded.EXCLUDED_FROM_CORROBORATION, (
            "EXCLUDED_FROM_CORROBORATION no longer derives from RULE_10_EXCLUDED"
        )
        assert rule10_instruments([planted]) == []
    finally:
        jpt_common.RULE_10_EXCLUDED.clear()
        jpt_common.RULE_10_EXCLUDED.update(original)
        importlib.reload(r10)
    assert "RULE_09" in r10.EXCLUDED_FROM_CORROBORATION       # restored cleanly
