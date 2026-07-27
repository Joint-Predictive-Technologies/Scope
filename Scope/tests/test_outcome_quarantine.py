"""The 9 fabricated RULE_15 alerts must not become RULE_15's calibration sample.

`alert_outcomes` is the seed corpus for per-rule realized win rate — the reserved
`historical_win_rate` input to calculate_opportunity_score. RULE_15's nine production
alerts all predate its repair and are fabricated: Boeing scored from *BA Credit Card
Trust* filings (+109%), XOM +637%, RTX +2557%. They measure the wrong company.

RULE_15 has no other alerts, so those nine would BE its win rate, and a +637% "win"
would promote the rule on the strength of signals it never produced.

The alerts themselves stay — they are the evidence of what the bug did. These tests
pin that their OUTCOMES are excluded, which is a different object.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection  # noqa: E402
from scripts import label_outcomes as lo  # noqa: E402


def _seed(conn, rule, created_at, ticker="BA"):
    cur = conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, created_at) "
        "VALUES (?, ?, 'HIGH', 'x', ?)", (rule, ticker, created_at))
    conn.commit()
    return cur.lastrowid


# ── the predicate ────────────────────────────────────────────────────────────

def test_pre_repair_rule15_is_quarantined():
    assert lo._quarantined("RULE_15", "2026-07-14 09:00:00")


def test_post_repair_rule15_labels_normally():
    """Forward-only. The repair works; its output is real data."""
    assert not lo._quarantined("RULE_15", "2026-07-28 09:00:00")


def test_other_rules_are_never_quarantined():
    """The control — otherwise `_quarantined` could just be always-True."""
    for rule in ("RULE_06", "RULE_10", "RULE_16", "RULE_CLUSTER", None):
        assert not lo._quarantined(rule, "2026-07-14 09:00:00")


def test_the_epoch_matches_the_rules_own_quarantine():
    """Two places answer 'which rows are pre-repair?'. They must not drift."""
    from scripts import rule_15_earnings_nlp as r15
    assert lo.QUARANTINED_BEFORE["RULE_15"] == r15.REPAIR_EPOCH


# ── the behaviour ────────────────────────────────────────────────────────────

def test_a_fabricated_alert_is_excluded_and_never_priced(monkeypatch):
    """If it were priced, a fabricated +637% would enter the corpus as a real return."""
    called = []
    monkeypatch.setattr(lo, "_fetch_closes", lambda *a, **k: called.append(a) or [])

    conn = db_connection()
    aid = _seed(conn, "RULE_15", "2026-05-14 09:00:00")
    conn.close()

    lo.run()

    conn = db_connection()
    row = conn.execute(
        "SELECT status, note, return_20d FROM alert_outcomes WHERE alert_id=?",
        (aid,)).fetchone()
    # the alert itself must survive — it is the record of what the bug did
    still_there = conn.execute("SELECT COUNT(*) FROM alerts WHERE id=?", (aid,)).fetchone()[0]
    conn.close()

    assert row is not None and row["status"] == "excluded"
    assert "fabricated" in (row["note"] or "")
    assert row["return_20d"] is None, "a quarantined alert must carry no return"
    assert not called, "a quarantined alert was sent for pricing"
    assert still_there == 1, "the ALERT must be preserved; only its outcome is excluded"


def test_an_already_complete_fabricated_row_is_swept(monkeypatch):
    """The one the eligible-query alone would MISS.

    `run()` only picks up rows where the outcome is NULL or 'pending', so a fabricated
    row already labelled `complete` — which is the actual state of the nine in prod, if
    labeling has run — would keep its fabricated return forever. The sweep exists for
    exactly those.
    """
    monkeypatch.setattr(lo, "_fetch_closes", lambda *a, **k: [])
    conn = db_connection()
    aid = _seed(conn, "RULE_15", "2026-05-11 09:00:00", ticker="XOM")
    lo.ensure_table(conn)
    conn.execute(
        "INSERT INTO alert_outcomes (alert_id, ticker, status, return_20d) "
        "VALUES (?, 'XOM', 'complete', 6.37)", (aid,))
    conn.commit(); conn.close()

    lo.run()

    conn = db_connection()
    row = conn.execute(
        "SELECT status, note FROM alert_outcomes WHERE alert_id=?", (aid,)).fetchone()
    conn.close()
    assert row["status"] == "excluded", (
        "an already-labelled fabricated row was left in the calibration corpus")
    assert "fabricated" in (row["note"] or "")


def test_the_sweep_is_idempotent_and_leaves_real_outcomes_alone(monkeypatch):
    """Running twice must not churn, and a genuine outcome must be untouched."""
    monkeypatch.setattr(lo, "_fetch_closes", lambda *a, **k: [])
    conn = db_connection()
    good = _seed(conn, "RULE_06", "2026-05-11 09:00:00", ticker="MSFT")
    lo.ensure_table(conn)
    conn.execute(
        "INSERT INTO alert_outcomes (alert_id, ticker, status, return_20d) "
        "VALUES (?, 'MSFT', 'complete', 0.11)", (good,))
    conn.commit(); conn.close()

    first = lo.run()["excluded"]
    second = lo.run()["excluded"]

    conn = db_connection()
    row = conn.execute(
        "SELECT status, return_20d FROM alert_outcomes WHERE alert_id=?",
        (good,)).fetchone()
    conn.close()
    assert second == 0, f"sweep re-marked rows on a second run ({second}) — not idempotent"
    assert row["status"] == "complete" and row["return_20d"] == 0.11, (
        "a genuine outcome was swept")
    assert first >= 0
