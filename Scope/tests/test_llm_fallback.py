#!/usr/bin/env python3
"""
Tests for jpt_common.generate_narrative — the shared Groq primary/fallback
retry wrapper — and that each narrative call site degrades gracefully (no
narrative, never a broken page, never fabricated placeholder text) when every
provider is exhausted.

Runs under pytest or standalone:  python3 tests/test_llm_fallback.py
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common  # noqa: E402


class _FakeResponse:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})]


class _FakeGroqClient:
    """Records which api_key instantiated it; chat.completions.create() behavior
    is driven by a per-key script the test supplies via _SCRIPTS."""
    _SCRIPTS: dict[str, list] = {}   # api_key -> list of callables/exceptions, consumed in order
    calls: list[str] = []            # api_keys used to instantiate a client, in order

    def __init__(self, api_key):
        self.api_key = api_key
        _FakeGroqClient.calls.append(api_key)
        self.chat = type("Chat", (), {})()
        self.chat.completions = type("Completions", (), {})()
        self.chat.completions.create = self._create

    def _create(self, **kwargs):
        script = _FakeGroqClient._SCRIPTS.get(self.api_key, [])
        if not script:
            raise RuntimeError(f"no script left for key {self.api_key}")
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _FakeResponse(step)


@pytest.fixture(autouse=True)
def fake_groq(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    jpt_common.db_connection().close()
    _FakeGroqClient._SCRIPTS = {}
    _FakeGroqClient.calls = []
    monkeypatch.setattr("groq.Groq", _FakeGroqClient)
    monkeypatch.setattr(time, "sleep", lambda s: None)  # don't slow tests with real backoff
    yield


def _last_llm_note():
    conn = jpt_common.db_connection()
    row = conn.execute(
        "SELECT notes, events_flagged, alerts_emitted FROM activity_log "
        "WHERE source='LLM_NARRATIVE' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row


def test_primary_success_first_try_no_fallback_touched(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "PRIMARY_KEY")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "FALLBACK_KEY")
    _FakeGroqClient._SCRIPTS["PRIMARY_KEY"] = ["hello from primary"]

    text = jpt_common.generate_narrative([{"role": "user", "content": "hi"}])

    assert text == "hello from primary"
    assert _FakeGroqClient.calls == ["PRIMARY_KEY"]     # fallback never instantiated
    row = _last_llm_note()
    assert "provider=primary" in row["notes"]
    assert row["events_flagged"] == 0                    # clean first-shot, no retry needed


def test_primary_fails_retries_then_falls_back(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "PRIMARY_KEY")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "FALLBACK_KEY")
    _FakeGroqClient._SCRIPTS["PRIMARY_KEY"] = [RuntimeError("boom"), RuntimeError("boom again")]
    _FakeGroqClient._SCRIPTS["FALLBACK_KEY"] = ["hello from fallback"]

    text = jpt_common.generate_narrative([{"role": "user", "content": "hi"}])

    assert text == "hello from fallback"
    # primary retried twice (2 attempts) before moving to fallback
    assert _FakeGroqClient.calls == ["PRIMARY_KEY", "PRIMARY_KEY", "FALLBACK_KEY"]
    row = _last_llm_note()
    assert "provider=fallback" in row["notes"]
    assert row["events_flagged"] == 1                    # needed retry/fallback


def test_both_providers_exhausted_returns_none(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "PRIMARY_KEY")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "FALLBACK_KEY")
    _FakeGroqClient._SCRIPTS["PRIMARY_KEY"] = [RuntimeError("p1"), RuntimeError("p2")]
    _FakeGroqClient._SCRIPTS["FALLBACK_KEY"] = [RuntimeError("f1"), RuntimeError("f2")]

    text = jpt_common.generate_narrative([{"role": "user", "content": "hi"}])

    assert text is None
    assert _FakeGroqClient.calls == ["PRIMARY_KEY", "PRIMARY_KEY", "FALLBACK_KEY", "FALLBACK_KEY"]
    row = _last_llm_note()
    assert "provider=none" in row["notes"]
    assert row["alerts_emitted"] == 0


def test_no_keys_configured_returns_none_without_calling_groq(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_FALLBACK", raising=False)

    text = jpt_common.generate_narrative([{"role": "user", "content": "hi"}])

    assert text is None
    assert _FakeGroqClient.calls == []
    row = _last_llm_note()
    assert "no keys configured" in row["notes"] or "no_key" in row["notes"]


def test_missing_primary_key_still_tries_fallback(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "FALLBACK_KEY")
    _FakeGroqClient._SCRIPTS["FALLBACK_KEY"] = ["fallback saves the day"]

    text = jpt_common.generate_narrative([{"role": "user", "content": "hi"}])

    assert text == "fallback saves the day"
    assert _FakeGroqClient.calls == ["FALLBACK_KEY"]


# ── call-site degradation: page/section still renders without narrative ──────

def test_rule10_narrative_uses_deterministic_fallback_when_llm_down(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "PRIMARY_KEY")
    monkeypatch.delenv("GROQ_API_KEY_FALLBACK", raising=False)
    _FakeGroqClient._SCRIPTS["PRIMARY_KEY"] = [RuntimeError("down"), RuntimeError("down")]

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rule_10_corroboration",
        os.path.join(os.path.dirname(__file__), "..", "scripts", "rule_10_corroboration.py"))
    r10 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(r10)

    text = r10._build_narrative("AAPL", [{"headline": "x fired"}], "RULE_06,RULE_08,RULE_11,RULE_13")
    # never None, never fabricated — the rule's own deterministic sentence
    assert "converged on AAPL" in text
    assert "See individual rule alerts" in text


def test_morning_brief_preamble_omitted_when_llm_down(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "PRIMARY_KEY")
    monkeypatch.delenv("GROQ_API_KEY_FALLBACK", raising=False)
    _FakeGroqClient._SCRIPTS["PRIMARY_KEY"] = [RuntimeError("down"), RuntimeError("down")]

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "morning_brief", os.path.join(os.path.dirname(__file__), "..", "scripts", "morning_brief.py"))
    mb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mb)

    d = {"headline": None, "theses": [], "clusters": [], "overnight": [], "earnings": [],
         "congress": {"rows": [], "total_txns": 0},
         "health": {"rules_ran": 0, "rules_total": 19, "stalls": [], "roster": []}}
    assert mb._preamble(d) is None
    # the brief must still render fully without it
    html = mb.render_html(d, "2026-07-21", preamble=None)
    assert "<html" in html
    assert "generated summary" not in html.lower()   # the labeled-preamble tag never appears


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
