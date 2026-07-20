#!/usr/bin/env python3
"""
ingest_senate must never fail silently: each distinct failure class writes a
distinct, greppable activity_log source instead of a bare sys.exit().

Runs under pytest or standalone:  python3 tests/test_ingest_senate_hardening.py
"""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common  # noqa: E402
import ingest_senate as isen  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    # Don't let a real .env repopulate the API key mid-test.
    monkeypatch.setattr(isen, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["ingest_senate.py", "--since", "2026-01-01"])
    jpt_common.db_connection().close()
    yield


def _last_source():
    conn = jpt_common.db_connection()
    row = conn.execute(
        "SELECT source, notes FROM activity_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return (row["source"], row["notes"]) if row else (None, None)


class _Resp:
    def __init__(self, *, raise_exc=None, json_exc=None, json_value=None):
        self._raise_exc, self._json_exc, self._json_value = raise_exc, json_exc, json_value

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc

    def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._json_value


def test_missing_key_logs_source(monkeypatch):
    monkeypatch.delenv("QUIVER_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        isen.main()
    src, notes = _last_source()
    assert src == "INGEST_SENATE_MISSING_KEY"
    assert "QUIVER_API_KEY" in notes


def test_http_error_logs_source(monkeypatch):
    monkeypatch.setenv("QUIVER_API_KEY", "test-key")
    err = requests.exceptions.HTTPError()
    err.response = type("R", (), {"status_code": 429})()
    monkeypatch.setattr(isen.requests, "get", lambda *a, **k: _Resp(raise_exc=err))
    with pytest.raises(SystemExit):
        isen.main()
    src, notes = _last_source()
    assert src == "INGEST_SENATE_HTTP_ERROR"
    assert "429" in notes


def test_network_error_logs_source(monkeypatch):
    monkeypatch.setenv("QUIVER_API_KEY", "test-key")
    def _boom(*a, **k):
        raise requests.exceptions.ConnectTimeout("read timed out")
    monkeypatch.setattr(isen.requests, "get", _boom)
    with pytest.raises(SystemExit):
        isen.main()
    src, _ = _last_source()
    assert src == "INGEST_SENATE_NETWORK_ERROR"


def test_bad_json_logs_source(monkeypatch):
    monkeypatch.setenv("QUIVER_API_KEY", "test-key")
    monkeypatch.setattr(isen.requests, "get",
                        lambda *a, **k: _Resp(json_exc=ValueError("no json")))
    with pytest.raises(SystemExit):
        isen.main()
    src, _ = _last_source()
    assert src == "INGEST_SENATE_BAD_JSON"


def test_bad_shape_logs_source(monkeypatch):
    monkeypatch.setenv("QUIVER_API_KEY", "test-key")
    monkeypatch.setattr(isen.requests, "get",
                        lambda *a, **k: _Resp(json_value={"not": "a list"}))
    with pytest.raises(SystemExit):
        isen.main()
    src, _ = _last_source()
    assert src == "INGEST_SENATE_BAD_SHAPE"


def test_bad_since_arg_logs_source(monkeypatch):
    monkeypatch.setenv("QUIVER_API_KEY", "test-key")
    monkeypatch.setattr(sys, "argv", ["ingest_senate.py", "--since", "not-a-date"])
    with pytest.raises(SystemExit):
        isen.main()
    src, _ = _last_source()
    assert src == "INGEST_SENATE_BAD_ARGS"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
