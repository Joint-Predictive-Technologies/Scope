#!/usr/bin/env python3
"""
Tests for the deterministic daily morning brief.

Runs under pytest or standalone:  python3 tests/test_morning_brief.py
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_spec = importlib.util.spec_from_file_location(
    "morning_brief", os.path.join(os.path.dirname(__file__), "..", "scripts", "morning_brief.py"))
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)

import jpt_common  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("GROQ_API_KEY", "")  # force deterministic-only (no preamble)
    conn = jpt_common.db_connection()
    conn.commit()
    conn.close()
    yield


def _seed_alert(rule, ticker, severity, headline, opp=50, ev=20, tags=None):
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, why_matters, tags, "
        "opportunity_score, evidence_confidence, created_at) "
        "VALUES (?,?,?,?,?,?,?,?, datetime('now'))",
        (rule, ticker, severity, headline, "because reasons", tags, opp, ev),
    )
    conn.commit()
    conn.close()


def test_brief_with_alerts_renders_headline():
    _seed_alert("RULE_06", "NVDA", "HIGH", "Insider buying NVDA", opp=80, ev=40)
    _seed_alert("RULE_08", "XOM", "HIGH", "Reg filing XOM", opp=30, ev=20)
    out = mb.generate(date_str="2026-07-20", force=True, use_llm=False)
    # highest opportunity_score wins the headline
    assert "Insider buying NVDA" in out["html"]
    assert "Insider buying NVDA" in out["text"]
    assert out["meta"]["headline_rule"] == "RULE_06"
    assert out["meta"]["sections_populated"] >= 1
    assert 'id="headline"' in out["html"]        # permalink anchor
    assert "SYSTEM HEALTH" in out["text"]         # health always present


def test_empty_state_no_crash_omits_sections():
    out = mb.generate(date_str="2026-07-20", force=True, use_llm=False)
    # no alerts/theses/clusters -> those sections omitted, but page still renders
    assert "<html" in out["html"]
    assert 'id="headline"' not in out["html"]     # omitted, not empty-rendered
    assert 'id="clusters"' not in out["html"]
    assert "System Health".upper() in out["text"] or "SYSTEM HEALTH" in out["text"]
    assert "No activity to report" in out["html"] or "id=\"health\"" in out["html"]


def test_text_version_no_html_bleed():
    _seed_alert("RULE_CLUSTER", "SPCX", "HIGH", "3 members bought SPCX in 72h",
                tags='{"direction":"consensus_buy","fingerprint":"CLUSTER::A+B+C::SPCX::consensus_buy"}')
    out = mb.generate(date_str="2026-07-20", force=True, use_llm=False)
    txt = out["text"]
    assert "<section" not in txt and "<div" not in txt and "</" not in txt
    assert "SPCX" in txt


def test_cache_hit_and_miss():
    _seed_alert("RULE_06", "AAPL", "HIGH", "Insider AAPL")
    first = mb.generate(date_str="2026-07-20", force=True, use_llm=False)
    assert first["cache"] == "miss"
    second = mb.generate(date_str="2026-07-20", use_llm=False)   # no force -> cache
    assert second["cache"] == "hit"
    assert second["html"] == first["html"]


def test_activity_log_written():
    _seed_alert("RULE_06", "AAPL", "HIGH", "Insider AAPL")
    mb.generate(date_str="2026-07-20", force=True, use_llm=False)
    conn = jpt_common.db_connection()
    row = conn.execute(
        "SELECT notes FROM activity_log WHERE source='DAILY_BRIEF' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row and "sections_populated=" in row["notes"] and "headline_rule=" in row["notes"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
