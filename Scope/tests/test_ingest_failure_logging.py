#!/usr/bin/env python3
"""
Tests that House-ingestion failures ALWAYS write an activity_log row instead of
dying silently via sys.exit(). Simulates each failure mode (non-200, 404,
network exception, bad ZIP, bad XML) against a temp DB.

Runs under pytest or standalone:  python3 tests/test_ingest_failure_logging.py
"""
import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests  # noqa: E402
import jpt_common  # noqa: E402
import ingest_house_index as ih  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(sys, "argv", ["ingest_house_index.py", "--year", "2026"])
    conn = jpt_common.db_connection()
    conn.commit()
    conn.close()
    yield


def _last_ingest_note():
    conn = jpt_common.db_connection()
    row = conn.execute(
        "SELECT notes FROM activity_log WHERE source='INGEST_HOUSE_INDEX' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row["notes"] if row else None


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content


def _run_main_expecting_exit():
    with pytest.raises(SystemExit):
        ih.main()


def test_non_200_logs_status_and_url(monkeypatch):
    monkeypatch.setattr(ih.requests, "get", lambda *a, **k: _Resp(503))
    _run_main_expecting_exit()
    note = _last_ingest_note()
    assert note and note.startswith("ERROR")
    assert "HTTP 503" in note and "FD.zip" in note


def test_404_is_distinct_and_actionable(monkeypatch):
    monkeypatch.setattr(ih.requests, "get", lambda *a, **k: _Resp(404))
    _run_main_expecting_exit()
    note = _last_ingest_note()
    assert note and "HTTP 404" in note
    assert "source URL may have changed" in note


def test_network_exception_logs_type(monkeypatch):
    def _boom(*a, **k):
        raise requests.exceptions.ConnectTimeout("timed out")
    monkeypatch.setattr(ih.requests, "get", _boom)
    _run_main_expecting_exit()
    note = _last_ingest_note()
    assert note and "network ConnectTimeout" in note and "FD.zip" in note


def test_bad_zip_logs(monkeypatch):
    monkeypatch.setattr(ih.requests, "get", lambda *a, **k: _Resp(200, b"not a zip"))
    _run_main_expecting_exit()
    note = _last_ingest_note()
    assert note and "not a valid ZIP" in note


def test_consecutive_failure_flagged(monkeypatch):
    # First failure, then a second — the second note flags the consecutive break.
    monkeypatch.setattr(ih.requests, "get", lambda *a, **k: _Resp(404))
    _run_main_expecting_exit()
    _run_main_expecting_exit()
    note = _last_ingest_note()
    assert "consecutive failure" in note


def test_success_still_logs_and_no_error(monkeypatch):
    # Build a minimal valid FD.zip with one XML PTR entry.
    xml = (b"<FinancialDisclosures><Member><DocID>90000001</DocID>"
           b"<FilingType>P</FilingType><First>Test</First><Last>Person</Last>"
           b"<FilingDate>1/5/2026</FilingDate></Member></FinancialDisclosures>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("2026FD.xml", xml)
    zip_bytes = buf.getvalue()
    monkeypatch.setattr(ih.requests, "get", lambda *a, **k: _Resp(200, zip_bytes))
    ih.main()  # should NOT raise SystemExit
    note = _last_ingest_note()
    assert note and not note.startswith(("ERROR", "CRITICAL"))
    assert "PTRs found" in note


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
