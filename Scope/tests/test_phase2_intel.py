#!/usr/bin/env python3
"""
Phase-2 intelligence layer tests: scoring engine, schema, mode toggle,
activity log, Reddit authenticity scoring.

Runs under pytest or standalone:  python3 tests/test_phase2_intel.py
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402
from jpt_common import (  # noqa: E402
    db_connection, calculate_evidence_confidence, calculate_opportunity_score,
    calculate_novelty_score, assign_time_horizon,
)

_c = TestClient(app)

_r = importlib.util.spec_from_file_location(
    "rule_reddit", os.path.join(os.path.dirname(__file__), "..", "scripts", "rule_reddit.py"))
reddit = importlib.util.module_from_spec(_r)
_r.loader.exec_module(reddit)


# ── Scoring engine (spec §7) ─────────────────────────────────────────────────
def test_evidence_confidence_thresholds():
    # Tiers are in the GATE's units: 3 distinct INSTRUMENTS is a fire, and a fire
    # must clear the first tier. Rescaled from 4/5/6 on 2026-07-27.
    assert calculate_evidence_confidence(2, [1.0]) < 40      # below the gate
    assert calculate_evidence_confidence(3, [1.0]) == 60.0   # 40 + 20 quality
    assert calculate_evidence_confidence(5, [1.0]) == 95.0
    # conflicting evidence penalizes
    assert calculate_evidence_confidence(4, [1.0], True) < calculate_evidence_confidence(4, [1.0])


def test_evidence_and_opportunity_are_independent():
    """A saturated theme keeps high evidence but near-zero opportunity."""
    ev = calculate_evidence_confidence(5, [1.0, 1.0])
    opp = calculate_opportunity_score(0.1, 85, "LONG")
    assert ev >= 60 and opp < 25


def test_opportunity_novelty_dominates():
    fresh = calculate_opportunity_score(1.0, 0, "IMMEDIATE")
    stale = calculate_opportunity_score(0.1, 90, "LONG")
    assert fresh > stale and 0 <= stale <= 100 and fresh <= 100


def test_novelty_first_time_is_one():
    conn = db_connection()
    assert calculate_novelty_score("RULE_OSINT", "Region-Never-Seen-999", conn) == 1.0
    conn.close()


def test_time_horizon_mapping():
    assert assign_time_horizon("RULE_07") == "IMMEDIATE"
    assert assign_time_horizon("RULE_12") == "LONG"
    assert assign_time_horizon("RULE_UNKNOWN") == "SHORT"


# ── Schema (spec §Priority 1) ────────────────────────────────────────────────
def test_phase2_tables_exist():
    conn = db_connection()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert {"themes", "theme_signals", "activity_log", "thesis_outcomes"} <= names


def test_alerts_has_scoring_columns():
    conn = db_connection()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    conn.close()
    assert {"novelty_score", "opportunity_score", "evidence_confidence",
            "time_horizon", "source_quality", "verify_url", "theme_id"} <= cols


# ── Mode toggle (spec §Priority 7) ───────────────────────────────────────────
def test_scanner_mode_only_near_term():
    rows = _c.get("/alerts?mode=scanner&days=30&limit=20").json()
    assert all(a.get("time_horizon") in ("IMMEDIATE", "SHORT") for a in rows)


def test_overwatch_mode_excludes_noise():
    rows = _c.get("/alerts?mode=overwatch&days=30&limit=20").json()
    assert all(a["rule"] not in ("RULE_07", "RULE_REDDIT") for a in rows)


# ── Activity log (spec §Priority 3) ──────────────────────────────────────────
def test_activity_log_endpoint():
    assert isinstance(_c.get("/api/activity-log").json(), list)


def test_record_activity_writes_row():
    """record_activity() persists a scanned/flagged/emitted/duration row."""
    from jpt_common import db_connection, record_activity
    record_activity("UNITTEST_SRC", scanned=7, flagged=3, emitted=2, duration_seconds=1.23)
    conn = db_connection()
    row = conn.execute(
        "SELECT events_scanned, events_flagged, alerts_emitted, duration_seconds "
        "FROM activity_log WHERE source='UNITTEST_SRC' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.execute("DELETE FROM activity_log WHERE source='UNITTEST_SRC'")
    conn.commit(); conn.close()
    assert row is not None
    assert (row[0], row[1], row[2], row[3]) == (7, 3, 2, 1.23)


# ── Reddit authenticity scoring (spec §9) ────────────────────────────────────
def test_reddit_organic_scores_high():
    p = {"title": "DD: $ABC 10-Q shows revenue up, my price target with analysis",
         "num_comments": 80, "upvote_ratio": 0.9, "author_post_count": 50}
    assert reddit.authenticity_score(p) >= 7


def test_reddit_pump_scores_low():
    p = {"title": "$XYZ to the moon 10x hidden gem don't miss going parabolic",
         "author_post_count": 1}
    s = reddit.authenticity_score(p)
    assert s < 4
    assert reddit.signal_type(s, 60, 200) == "POSSIBLE_PUMP"


def test_reddit_severity_weighting():
    assert reddit.reddit_severity("POSSIBLE_PUMP", 3.0, 200) == "HIGH"
    assert reddit.reddit_severity("ORGANIC_MOMENTUM", 1.5, 200) == "MEDIUM"


# ── Scoring actually runs (regression for the "all defaults" bug) ────────────
def test_scores_are_not_all_defaults():
    """After enrichment, alerts must carry computed (non-default) scores."""
    from jpt_common import enrich_alert_scores
    conn = db_connection()
    enrich_alert_scores(conn, only_unscored=True)
    total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    if total == 0:
        conn.close(); return
    distinct_opp = conn.execute("SELECT COUNT(DISTINCT opportunity_score) FROM alerts").fetchone()[0]
    default_ev = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE opportunity_score=0 AND evidence_confidence=0"
    ).fetchone()[0]
    conn.close()
    assert distinct_opp > 1          # varied, not one default value
    assert default_ev == 0           # nothing left at (0,0)


# Was a strict xfail from 2026-07-27: the tiers were 4/5/6 while the gate fired at 3
# instruments, so this invariant was genuinely broken and the marker held the seat until
# a human decided the rescale. They did; the tiers are 3/4/5; the marker cleared itself.
def test_rule10_evidence_exceeds_single_rule():
    from jpt_common import score_alert_fields
    import json as _j
    conn = db_connection()
    r10 = score_alert_fields(conn, "RULE_10", "TSM", "x",
                             _j.dumps({"rules": ["RULE_01B", "RULE_02", "RULE_06", "RULE_08"]}))
    single = score_alert_fields(conn, "RULE_06", "TSM", "x", "")
    conn.close()
    assert r10["evidence_confidence"] > single["evidence_confidence"]


def test_incremental_scoring_idempotent():
    from jpt_common import enrich_alert_scores
    conn = db_connection()
    conn.execute("INSERT INTO alerts (rule,ticker,severity,headline) VALUES ('RULE_06','QQTEST','HIGH','t')")
    conn.commit()
    first = enrich_alert_scores(conn, only_unscored=True)
    second = enrich_alert_scores(conn, only_unscored=True)
    conn.execute("DELETE FROM alerts WHERE ticker='QQTEST'"); conn.commit()
    conn.close()
    assert first >= 1 and second == 0


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
