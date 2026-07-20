#!/usr/bin/env python3
"""
Tests for the standalone congressional digest view and its shared aggregation.

Runs under pytest or standalone:  python3 tests/test_congress_digest.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402

_c = TestClient(app)
DAY = "2026-05-15"
EMPTY_DAY = "2019-01-01"


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    conn = jpt_common.db_connection()
    conn.executemany(
        "INSERT INTO members (bioguide_id, full_name, is_current) VALUES (?,?,1)",
        [("M1", "Alpha, A"), ("M2", "Bravo, B"), ("M3", "Charlie, C")],
    )
    # 3 members trade ZDG, 1 member trades ONE — all disclosed (created_at) on DAY.
    txns = [
        ("M1", "ZDG", "purchase"), ("M2", "ZDG", "purchase"), ("M3", "ZDG", "sale"),
        ("M1", "ONE", "sale"),
    ]
    for mid, tkr, tt in txns:
        conn.execute(
            "INSERT INTO transactions (member_id, raw_ticker_string, transaction_type, "
            "transaction_date, created_at) VALUES (?,?,?,?, ?)",
            (mid, tkr, tt, DAY, f"{DAY} 10:00:00"),
        )
    conn.commit()
    conn.close()
    yield


def test_shared_function_day_vs_rolling():
    conn = jpt_common.db_connection()
    full = jpt_common.congress_day_digest(conn, day=DAY, limit=None)
    conn.close()
    assert full["total_txns"] == 4
    tickers = {r["ticker"]: r for r in full["rows"]}
    assert tickers["ZDG"]["members"] == 3
    assert tickers["ZDG"]["buys"] == 2 and tickers["ZDG"]["sells"] == 1
    # ZDG (3 members) ranks before ONE (1 member)
    assert full["rows"][0]["ticker"] == "ZDG"


def test_api_digest_with_transactions():
    d = _c.get(f"/api/congress-digest/{DAY}").json()
    assert d["date"] == DAY and d["total_txns"] == 4
    assert any(r["ticker"] == "ZDG" and r["members"] == 3 for r in d["rows"])
    # date navigation
    assert d["prev"] == "2026-05-14"
    assert d["next"] == "2026-05-16"      # DAY is in the past -> next exists


def test_api_digest_empty_day():
    d = _c.get(f"/api/congress-digest/{EMPTY_DAY}").json()
    assert d["total_txns"] == 0 and d["rows"] == []      # cleanly empty, no crash


def test_api_bad_date_404():
    assert _c.get("/api/congress-digest/not-a-date").status_code == 404


def test_digest_pages_serve():
    assert _c.get("/congress/digest").status_code == 200
    assert _c.get(f"/congress/digest/{DAY}").status_code == 200
    assert "Congressional Digest" in _c.get("/congress/digest").text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
