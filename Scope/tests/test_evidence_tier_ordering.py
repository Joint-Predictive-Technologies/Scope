"""The tiers are in the GATE's units, and the overwatch feed proves why it matters.

`calculate_evidence_confidence` stepped at >=4/>=5/>=6, denominated in rule NAMES from
when the gate needed that many. D1 moved the gate to 3 distinct INSTRUMENTS and left the
tiers behind, so every minimum-strength convergence fell BELOW the first tier, took
base=0, and persisted 6.0 — against a lone RULE_06's 20.0.

That is not an abstract scoring nit. `api/routers/alerts.py` `mode=overwatch` is
`ORDER BY a.evidence_confidence DESC, datetime(a.created_at) DESC` — the "best-supported
theses first" view. So the corroboration engine's own output, the moat's flagship, sorted
BELOW the ordinary single alerts it was built from, in the one view built to rank by
support. These tests prove the flip through the real query, not through the formula.

Rescaled to >=3/>=4/>=5 on 2026-07-27 under human sign-off.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402

import jpt_common  # noqa: E402
from api.main import app  # noqa: E402
from jpt_common import (calculate_evidence_confidence, db_connection,  # noqa: E402
                        RULE_10_MIN_INSTRUMENTS)

_c = TestClient(app)

# What a minimum fire persists, at RULE_10's own source quality ('Derived', weight
# 0.3 — the WORST case, and the one the emitter actually uses).
#
# DERIVED FROM THE LIVE FORMULA ON PURPOSE, not hardcoded to 46.0. If it were a
# literal, reverting the tiers would leave every ordering test below still green —
# they would be proving "46.0 outranks 20.0", which is arithmetic, not the fix.
# Derived, a tier revert drags this to 6.0 and the ordering assertions go red, which
# is the only way they can be said to pass for the right reason. The literal pin
# lives in test_the_tier_bases_are_the_gates_units, where it belongs.
MIN_FIRE_NEW = calculate_evidence_confidence(RULE_10_MIN_INSTRUMENTS, [0.3])
MIN_FIRE_OLD = 6.0     # base  0 + 0.3*20, what the OLD tiers persisted <- below a lone alert
LONE_ALERT = 20.0      # base  0 + 1.0*20   (single Primary-quality rule)


def _seed_pair(conn, corroboration_ec):
    """A corroboration and a lone alert, with the corroboration deliberately OLDER.

    The tiebreak is `datetime(a.created_at) DESC`, so making the corroboration the
    older row means ONLY evidence_confidence can put it first. If the column were
    ignored entirely, the lone alert would win on recency — which is exactly what
    makes this a test of the column and not of insertion order.
    """
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, evidence_confidence, "
        "opportunity_score, time_horizon, created_at) VALUES "
        "('RULE_10','ORDERTEST','HIGH','[CORROBORATION] ORDERTEST: 3 independent "
        "instruments in 14d', ?, 50, 'MEDIUM', datetime('now','-6 hours'))",
        (corroboration_ec,))
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, evidence_confidence, "
        "opportunity_score, time_horizon, created_at) VALUES "
        "('RULE_06','ORDERTEST','HIGH','Insider buy at ORDERTEST', ?, 50, 'MEDIUM', "
        "datetime('now','-1 hours'))",
        (LONE_ALERT,))
    conn.commit()


def _overwatch_rules():
    """The REAL endpoint, real mode, real ORDER BY — not a reimplemented query."""
    rows = _c.get("/alerts?mode=overwatch&days=30&limit=50").json()
    return [r["rule"] for r in rows if r.get("ticker") == "ORDERTEST"]


# ── the arithmetic, first ────────────────────────────────────────────────────

def test_the_tier_bases_are_the_gates_units():
    """Bases at 40/60/75 begin AT the gate's threshold, not one above it."""
    assert RULE_10_MIN_INSTRUMENTS == 3

    # Isolate the base by passing a zero-weight quality list: base + 0.0*20 == base.
    assert calculate_evidence_confidence(2, [0.0]) == 0.0
    assert calculate_evidence_confidence(3, [0.0]) == 40.0
    assert calculate_evidence_confidence(4, [0.0]) == 60.0
    assert calculate_evidence_confidence(5, [0.0]) == 75.0
    assert calculate_evidence_confidence(9, [0.0]) == 75.0     # top tier, no runaway

    # The SCORES are base + avg_quality*20 — the quality term always adds, so a
    # minimum fire scores 46.0/60.0, never a bare 40. Recording that here because a
    # brief once specified "3-instrument fire scores 40", which is the BASE.
    assert calculate_evidence_confidence(3, [0.3]) == MIN_FIRE_NEW == 46.0
    assert calculate_evidence_confidence(3, [1.0]) == 60.0


def test_a_minimum_fire_outranks_a_lone_alert_on_the_worst_case():
    """Worst-case corroboration vs best-case single. Was 6.0 vs 20.0."""
    assert MIN_FIRE_NEW > LONE_ALERT
    assert MIN_FIRE_OLD < LONE_ALERT, "the pre-rescale inversion must stay reproducible"


# ── the ordering, which is what the column actually drives ───────────────────

def test_overwatch_sorts_the_corroboration_above_the_single_alert():
    conn = db_connection()
    _seed_pair(conn, MIN_FIRE_NEW)
    conn.close()

    order = _overwatch_rules()
    assert order[:2] == ["RULE_10", "RULE_06"], (
        f"corroboration did not sort first: {order}. It is the OLDER row, so this is "
        "the evidence column doing the work.")


def test_negative_control_the_old_tiers_invert_that_order():
    """The control that makes the test above mean something.

    Same rows, same query, only the corroboration's detection-time score changed to
    what the OLD tiers persisted. If the order does NOT invert here, the assertion
    above is passing for some reason other than evidence_confidence.
    """
    conn = db_connection()
    _seed_pair(conn, MIN_FIRE_OLD)
    conn.close()

    order = _overwatch_rules()
    assert order[:2] == ["RULE_06", "RULE_10"], (
        f"expected the pre-rescale inversion, got {order}")


def test_the_ordering_is_driven_by_evidence_not_recency_or_rule():
    """Rule out the two confounders directly: flip the ages, order must not change."""
    conn = db_connection()
    # corroboration NEWER this time — if recency drove it, this proves nothing new;
    # if evidence drives it, the corroboration leads in BOTH age arrangements.
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, evidence_confidence, "
        "opportunity_score, time_horizon, created_at) VALUES "
        "('RULE_10','ORDERTEST','HIGH','c', ?, 50, 'MEDIUM', datetime('now','-1 hours'))",
        (MIN_FIRE_NEW,))
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, evidence_confidence, "
        "opportunity_score, time_horizon, created_at) VALUES "
        "('RULE_06','ORDERTEST','HIGH','s', ?, 50, 'MEDIUM', datetime('now','-6 hours'))",
        (LONE_ALERT,))
    conn.commit(); conn.close()

    assert _overwatch_rules()[:2] == ["RULE_10", "RULE_06"]


def test_scanner_mode_is_untouched_by_the_rescale():
    """The OTHER mode orders by opportunity_score — the rescale must not reach it."""
    conn = db_connection()
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, evidence_confidence, "
        "opportunity_score, time_horizon, created_at) VALUES "
        "('RULE_10','SCANTEST','HIGH','c', ?, 10, 'SHORT', datetime('now','-1 hours'))",
        (MIN_FIRE_NEW,))
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, evidence_confidence, "
        "opportunity_score, time_horizon, created_at) VALUES "
        "('RULE_06','SCANTEST','HIGH','s', ?, 90, 'SHORT', datetime('now','-1 hours'))",
        (LONE_ALERT,))
    conn.commit(); conn.close()

    rows = _c.get("/alerts?mode=scanner&days=30&limit=50").json()
    order = [r["rule"] for r in rows if r.get("ticker") == "SCANTEST"]
    # High evidence, LOW opportunity -> must sort second here. Scanner ignores evidence.
    assert order[:2] == ["RULE_06", "RULE_10"], (
        f"scanner ordering moved with the evidence rescale: {order}")


# ── forward-only ─────────────────────────────────────────────────────────────

def test_the_rescale_rewrites_no_historical_score():
    """A row scored under the OLD tiers must keep its value through enrichment.

    Detection-time scores are immutable. `enrich_alert_scores(only_unscored=True)`
    fills gaps; it must not recompute a row that already carries a score, or every
    pre-rescale corroboration would silently jump 6.0 -> 46.0.
    """
    from jpt_common import enrich_alert_scores
    conn = db_connection()
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, evidence_confidence, "
        "opportunity_score, time_horizon) VALUES "
        "('RULE_10','HISTORIC','HIGH','old-tier corroboration', ?, 50, 'MEDIUM')",
        (MIN_FIRE_OLD,))
    conn.commit()

    enrich_alert_scores(conn, only_unscored=True)

    after = conn.execute(
        "SELECT evidence_confidence FROM alerts WHERE ticker='HISTORIC'").fetchone()[0]
    conn.close()
    assert after == MIN_FIRE_OLD == 6.0, (
        f"a pre-rescale score was rewritten to {after} — detection-time scores are "
        "immutable and the rescale is forward-only")
