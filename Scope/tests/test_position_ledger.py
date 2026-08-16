#!/usr/bin/env python3
"""
The position ledger's load-bearing properties, pinned.

Every future validation Scope performs will be computed over the `positions` table.
That makes exactly one property non-negotiable: an `entry_price` may only ever come
from a live quote fetched at tap time. A missing row is recoverable; a wrong entry
price is not, because nothing downstream can distinguish it from a right one.

So the tests below are weighted accordingly — the failure-mode coverage is deliberately
larger than the happy-path coverage, because the happy path failing is visible and the
failure path silently succeeding is not.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.routers import positions  # noqa: E402

SECRET = "unit-test-secret"
CHAT = "555000111"
HDR = {"X-Telegram-Bot-Api-Secret-Token": SECRET}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    # Bare app: only this router, so main.py's lifespan (and the scheduler) never runs.
    app = FastAPI()
    app.include_router(positions.router, prefix="/api")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_telegram_calls(monkeypatch):
    """Capture operator notifications instead of posting them."""
    sent = []
    monkeypatch.setattr(positions, "_answer_callback",
                        lambda cid, text: sent.append(text))
    return sent


def _update(cb_id, data, chat=CHAT, msg_id=42):
    return {"callback_query": {"id": cb_id, "data": data,
                               "message": {"message_id": msg_id,
                                           "chat": {"id": chat}}}}


def _rows(where="1=1"):
    from jpt_common import db_connection
    conn = db_connection()
    try:
        positions.ensure_table(conn)
        return [dict(r) for r in
                conn.execute(f"SELECT * FROM positions WHERE {where} ORDER BY id")]
    finally:
        conn.close()


def _stub_quote(monkeypatch, price, mtime_offset=0):
    """A quote endpoint returning exactly one controlled response."""
    import time

    class _R:
        ok = True
        status_code = 200

        def json(self):
            meta = {"regularMarketPrice": price}
            if mtime_offset is not None:
                meta["regularMarketTime"] = int(time.time()) + mtime_offset
            return {"chart": {"result": [{"meta": meta}]}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())


# ── auth: this is the app's first inbound WRITE endpoint ────────────────────────

def test_missing_secret_is_rejected_and_writes_nothing(client):
    r = client.post("/api/telegram/callback", json=_update("a", "pos:buy:1:MSFT"))
    assert r.status_code == 403
    assert _rows() == []


def test_wrong_secret_is_rejected_and_writes_nothing(client):
    r = client.post("/api/telegram/callback", json=_update("a", "pos:buy:1:MSFT"),
                    headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert r.status_code == 403
    assert _rows() == []


def test_unset_secret_fails_CLOSED_rather_than_open(client, monkeypatch):
    """An endpoint that authenticates only when configured is an open write endpoint
    on any deploy where the env var was forgotten."""
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    r = client.post("/api/telegram/callback", json=_update("a", "pos:buy:1:MSFT"),
                    headers={"X-Telegram-Bot-Api-Secret-Token": ""})
    assert r.status_code == 403
    assert _rows() == []


def test_foreign_chat_id_is_rejected_even_with_a_valid_secret(client):
    r = client.post("/api/telegram/callback",
                    json=_update("a", "pos:buy:1:MSFT", chat="999999999"), headers=HDR)
    assert r.status_code == 403
    assert _rows() == []


def test_denials_are_logged(client):
    from jpt_common import db_connection
    client.post("/api/telegram/callback", json=_update("a", "pos:buy:1:MSFT"))
    conn = db_connection()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE source LIKE 'POSITION_LEDGER%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n >= 1, "a rejected write must leave a trace"


# ── the entry price ────────────────────────────────────────────────────────────

def test_entry_price_is_the_live_quote(client, monkeypatch):
    _stub_quote(monkeypatch, 495.4)
    r = client.post("/api/telegram/callback", json=_update("b", "pos:buy:7:MSFT"),
                    headers=HDR)
    assert r.status_code == 200 and r.json()["recorded"] is True
    row = _rows("decision='buy'")[0]
    assert row["entry_price"] == 495.4
    assert row["quote_source"] == positions.QUOTE_SOURCE
    assert row["quote_market_time"] is not None, "an undateable price is not evidence"


@pytest.mark.parametrize("name,setup", [
    ("network error", lambda mp: mp.setattr(requests, "get", lambda *a, **k: (
        _ for _ in ()).throw(requests.exceptions.ConnectionError("boom")))),
    ("no price field", lambda mp: _stub_quote(mp, None)),
    ("zero price", lambda mp: _stub_quote(mp, 0)),
    ("negative price", lambda mp: _stub_quote(mp, -5.0)),
    ("no trade time", lambda mp: _stub_quote(mp, 100.0, mtime_offset=None)),
    ("30-day-stale quote", lambda mp: _stub_quote(mp, 100.0, mtime_offset=-30 * 86400)),
])
def test_no_quote_means_NO_ROW(client, monkeypatch, name, setup, _no_telegram_calls):
    """The single most damaging silent failure available in this feature would be a
    buy row whose price did not come from a live fetch. Every way the fetch can fail
    must produce zero rows — not a partial row, not a placeholder, not a default."""
    setup(monkeypatch)
    r = client.post("/api/telegram/callback", json=_update("c", "pos:buy:7:MSFT"),
                    headers=HDR)
    assert r.status_code == 200
    assert r.json()["recorded"] is False, name
    assert _rows() == [], f"{name} leaked a row"
    assert _no_telegram_calls, f"{name}: the human was not told"
    assert "NOT recorded" in _no_telegram_calls[0]


def test_a_written_buy_row_can_never_have_a_null_price(client, monkeypatch):
    """The invariant that makes the table trustworthy, stated as a query."""
    _stub_quote(monkeypatch, 250.0)
    client.post("/api/telegram/callback", json=_update("d", "pos:buy:1:MSFT"), headers=HDR)
    monkeypatch.setattr(requests, "get", lambda *a, **k: (
        _ for _ in ()).throw(requests.exceptions.ConnectionError("boom")))
    client.post("/api/telegram/callback", json=_update("e", "pos:buy:2:AAPL"), headers=HDR)
    assert [r for r in _rows("decision='buy'") if r["entry_price"] is None] == []


# ── idempotency ────────────────────────────────────────────────────────────────

def test_double_tap_creates_exactly_one_row(client, monkeypatch):
    _stub_quote(monkeypatch, 495.4)
    for _ in range(4):
        client.post("/api/telegram/callback", json=_update("same-id", "pos:buy:7:MSFT"),
                    headers=HDR)
    assert len(_rows()) == 1


def test_callback_id_is_unique_at_the_DATABASE_level(client, monkeypatch):
    """Enforced by a UNIQUE constraint rather than a read-then-write, because two
    concurrent deliveries can both win a read-then-write race."""
    _stub_quote(monkeypatch, 495.4)
    client.post("/api/telegram/callback", json=_update("dup", "pos:buy:7:MSFT"),
                headers=HDR)
    from jpt_common import db_connection
    conn = db_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO positions (decision, callback_query_id, ticker, entry_at,"
                " status) VALUES ('buy','dup','MSFT','now','open')")
    finally:
        conn.close()


# ── declines ───────────────────────────────────────────────────────────────────

def test_a_pass_is_recorded_not_discarded(client, monkeypatch):
    _stub_quote(monkeypatch, 495.4)
    r = client.post("/api/telegram/callback", json=_update("p1", "pos:pass:7:MSFT"),
                    headers=HDR)
    assert r.json()["recorded"] is True
    row = _rows("decision='pass'")[0]
    assert row["status"] == "passed"


def test_a_pass_survives_a_quote_failure(client, monkeypatch):
    """Deliberate asymmetry with 'buy': the DECISION is the datum on a decline and the
    price is only context, so a dead quote endpoint must not discard the decision."""
    monkeypatch.setattr(requests, "get", lambda *a, **k: (
        _ for _ in ()).throw(requests.exceptions.ConnectionError("boom")))
    r = client.post("/api/telegram/callback", json=_update("p2", "pos:pass:7:MSFT"),
                    headers=HDR)
    assert r.json()["recorded"] is True
    row = _rows("decision='pass'")[0]
    assert row["entry_price"] is None
    assert row["quote_error"], "why there is no price must be recorded, not implied"


# ── the worthiness score is recorded, never asserted ───────────────────────────

def test_worthiness_is_null_when_no_score_table_exists(client, monkeypatch):
    """Today's expected state. NULL is the honest value — the score is not persisted
    anywhere in production, and recomputing one inline would fabricate a number no
    scheduled job ever produced."""
    _stub_quote(monkeypatch, 495.4)
    client.post("/api/telegram/callback", json=_update("w", "pos:buy:7:MSFT"), headers=HDR)
    row = _rows("decision='buy'")[0]
    assert row["worthiness_score_at_entry"] is None
    assert row["worthiness_score_version"] == positions.WORTHINESS_VERSION


def test_the_operator_message_makes_no_prediction():
    """The message is the surface where an unsupported number would be most readily
    believed, so the absence of forecast language is a test, not a style preference."""
    msg = positions.format_decision_prompt(
        {"id": 1, "ticker": "MSFT", "severity": "HIGH", "rule": "RULE_01B",
         "headline": "First Touch — a member opens a position"}, worthiness=91.4)
    lowered = msg.lower()
    for banned in ("strong buy", "will rise", "expected return", "projected",
                   "target price", "outperform", "recommend", "should buy",
                   "high confidence", "likely to"):
        assert banned not in lowered, f"predictive language leaked: {banned!r}"
    # If the score is shown at all, its status is shown in the same breath.
    assert "UNVALIDATED" in msg
