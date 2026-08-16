#!/usr/bin/env python3
"""
The close half of the ledger: append-only, keyed on id, and never a P&L without its
benchmark and its n.

The weighting here mirrors the entry suite's: the failure-mode and presentation-honesty
coverage is larger than the happy-path coverage, because a close that fails loudly is
visible and a flattering number that quietly omits its denominator is not.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.routers import position_close as pc  # noqa: E402
from api.routers import positions             # noqa: E402

SECRET = "close-test-secret"
CHAT = "555000111"
HDR = {"X-Telegram-Bot-Api-Secret-Token": SECRET}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(positions.router, prefix="/api")
    return TestClient(app)


def _conn():
    from jpt_common import db_connection
    conn = db_connection()
    positions.ensure_table(conn)
    pc.ensure_close_table(conn)
    return conn


def _seed(ticker="MSFT", entry=450.0, entry_at="2026-08-11T14:30:00+00:00",
          decision="buy", cb="cb-seed"):
    conn = _conn()
    try:
        cur = conn.execute(
            """INSERT INTO positions (decision, callback_query_id, chat_id, ticker,
               entry_price, entry_at, quote_source, status)
               VALUES (?,?,?,?,?,?,'yahoo',?)""",
            (decision, cb, CHAT, ticker, entry, entry_at,
             "open" if decision == "buy" else "passed"))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _stub_quote(monkeypatch, price, mtime_offset=0):
    import time

    class _R:
        ok = True
        status_code = 200

        def json(self):
            meta = {"regularMarketPrice": price}
            if mtime_offset is not None:
                meta["regularMarketTime"] = int(time.time()) + mtime_offset
            # The benchmark path reads this same shape; give it a usable series too.
            return {"chart": {"result": [{
                "meta": meta,
                "timestamp": [int(time.time()) - 5 * 86400, int(time.time()) - 86400],
                "indicators": {"quote": [{"close": [500.0, 505.0]}]},
            }]}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())


def _closes():
    conn = _conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM position_closes")]
    finally:
        conn.close()


# ── forward-only ───────────────────────────────────────────────────────────────

def test_the_ledger_modules_contain_no_UPDATE_or_DELETE():
    """The property that makes 'a pass row can never become a buy' structural rather
    than a promise. Closing appends; it does not mutate."""
    import re
    for mod in ("positions.py", "position_close.py"):
        src = open(os.path.join(os.path.dirname(__file__), "..", "api", "routers",
                                mod), encoding="utf-8").read()
        # Strip comments/docstring prose so the word "update" in English does not trip it.
        sql = " ".join(re.findall(r'"""(?:[^"]|"(?!""))*"""|\'[^\']*\'|"[^"]*"', src))
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", sql, re.I), f"{mod} has an UPDATE"
        assert not re.search(r"\bDELETE\s+FROM\b", sql, re.I), f"{mod} has a DELETE"


def test_open_state_is_derived_by_join_not_by_the_status_column(monkeypatch):
    """`positions.status` is the value at INSERT and nothing moves it, so a closed
    position still reads 'open' there. Liveness comes from the LEFT JOIN."""
    pid = _seed(cb="derive")
    _stub_quote(monkeypatch, 495.4)
    conn = _conn()
    try:
        assert [r["id"] for r in pc.open_positions(conn)] == [pid]
    finally:
        conn.close()
    assert pc.close_position(pid)["closed"] is True
    conn = _conn()
    try:
        assert pc.open_positions(conn) == []                      # derived: closed
        row = conn.execute("SELECT status FROM positions WHERE id=?", (pid,)).fetchone()
        assert row["status"] == "open"    # untouched, by design — the JOIN is authority
    finally:
        conn.close()


# ── identity: id, never ticker ─────────────────────────────────────────────────

def test_ambiguous_ticker_is_refused_with_candidates_listed(client, monkeypatch):
    _seed(ticker="AAPL", cb="amb-1")
    _seed(ticker="AAPL", cb="amb-2", entry=460.0)
    reply = pc.handle_command("/sell AAPL")
    assert "ambiguous" in reply.lower()
    assert "id 1" in reply and "id 2" in reply, "candidates must be listed with ids"
    assert _closes() == [], "an ambiguous command must close nothing"


def test_unknown_and_already_closed_ids_are_refused_clearly(monkeypatch):
    _stub_quote(monkeypatch, 495.4)
    pid = _seed(cb="ac-1")
    assert "no position with id 999" in pc.close_position(999)["reason"]
    assert pc.close_position(pid)["closed"] is True
    second = pc.close_position(pid)
    assert second["closed"] is False and "already closed" in second["reason"]
    assert len(_closes()) == 1


def test_a_pass_row_cannot_be_closed(monkeypatch):
    pid = _seed(decision="pass", cb="p-1")
    r = pc.close_position(pid)
    assert r["closed"] is False and "not an open position" in r["reason"]


# ── the exit quote: the shared validator must actually cover this path ─────────

@pytest.mark.parametrize("raw,label", [
    (float("inf"), "+inf"), (float("nan"), "NaN"), (True, "bool true"),
    (1e300, "1e300"), ("495.40", "numeric string"), (0, "zero"),
])
def test_exit_quote_rejects_the_same_numeric_traps_as_entry(monkeypatch, raw, label):
    """The +inf/bool family, re-run against the EXIT path specifically. The fix lives in
    `validate_price`; this proves exit is actually wired into it rather than assuming
    the shared helper reaches here."""
    pid = _seed(cb=f"nf-{label}")
    _stub_quote(monkeypatch, raw)
    r = pc.close_position(pid)
    assert r["closed"] is False, label
    assert _closes() == [], f"{label} leaked a close row"


def test_quote_failure_writes_no_row(monkeypatch):
    pid = _seed(cb="qf")
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("boom")))
    r = pc.close_position(pid)
    assert r["closed"] is False
    assert _closes() == []


def test_every_stored_exit_price_is_finite(monkeypatch):
    import math
    _stub_quote(monkeypatch, 495.4)
    pc.close_position(_seed(cb="fin"))
    for row in _closes():
        assert math.isfinite(row["exit_price"])


# ── the arithmetic ─────────────────────────────────────────────────────────────

def test_pnl_is_computed_only_from_the_two_stored_prices(monkeypatch):
    _stub_quote(monkeypatch, 495.4)
    pid = _seed(entry=450.0, cb="math")
    r = pc.close_position(pid)
    assert r["realized_pnl_abs"] == pytest.approx(495.4 - 450.0)
    assert r["realized_pnl_pct"] == pytest.approx((495.4 - 450.0) / 450.0 * 100)
    stored = _closes()[0]
    assert stored["realized_pnl_pct"] == pytest.approx(r["realized_pnl_pct"])


def test_benchmark_basis_names_the_actual_dates_used(monkeypatch):
    """`holding_days` and the benchmark span differ whenever the market was shut at
    either end, so the dates actually used must be recoverable from the row."""
    _stub_quote(monkeypatch, 495.4)
    pc.close_position(_seed(cb="basis"))
    basis = _closes()[0]["benchmark_basis"]
    assert "->" in basis and "daily close" in basis
    assert "spanning" in basis, "the benchmark's own span must be stated, not implied"


# ── idempotency, by concurrency ────────────────────────────────────────────────

def test_concurrent_double_close_writes_exactly_one_row(monkeypatch):
    """Proven by threads rather than sequential replay: a read-then-write can be raced,
    a UNIQUE constraint cannot."""
    _stub_quote(monkeypatch, 495.4)
    pid = _seed(cb="conc")
    barrier = threading.Barrier(8)
    out = []

    def go():
        barrier.wait()
        try:
            out.append(pc.close_position(pid))
        except Exception as exc:                     # noqa: BLE001
            out.append({"closed": False, "reason": f"raised {type(exc).__name__}"})

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(_closes()) == 1, "a concurrent double-close wrote two rows"
    assert sum(1 for r in out if r.get("closed")) == 1


# ── auth parity with the callback path ─────────────────────────────────────────

def test_sell_command_requires_the_same_secret_as_the_callback_path(client):
    _seed(cb="auth-1")
    body = {"message": {"message_id": 1, "chat": {"id": CHAT}, "text": "/sell 1"}}
    assert client.post("/api/telegram/callback", json=body).status_code == 403
    assert client.post("/api/telegram/callback", json=body,
                       headers={"X-Telegram-Bot-Api-Secret-Token": "no"}).status_code == 403
    assert _closes() == []


def test_sell_command_rejects_a_foreign_chat_id_and_logs_it(client):
    _seed(cb="auth-2")
    body = {"message": {"message_id": 1, "chat": {"id": "999999"}, "text": "/sell 1"}}
    assert client.post("/api/telegram/callback", json=body,
                       headers=HDR).status_code == 403
    assert _closes() == []
    conn = _conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM activity_log "
                         "WHERE source='POSITION_LEDGER_CHAT_DENIED'").fetchone()[0]
    finally:
        conn.close()
    assert n >= 1


def test_sell_over_the_wire_closes_and_a_retry_does_not_double_close(client, monkeypatch):
    _stub_quote(monkeypatch, 495.4)
    _seed(cb="wire")
    body = {"message": {"message_id": 1, "chat": {"id": CHAT}, "text": "/sell 1"}}
    for _ in range(3):                                   # Telegram redelivers on retry
        assert client.post("/api/telegram/callback", json=body,
                           headers=HDR).status_code == 200
    assert len(_closes()) == 1


# ── presentation honesty ───────────────────────────────────────────────────────

def test_format_pnl_cannot_be_called_without_benchmark_and_n():
    """The signature is the enforcement: omitting either is a TypeError, not a lapse."""
    with pytest.raises(TypeError):
        pc.format_pnl(ticker="MSFT", pnl_pct=10.0, holding_days=5.0)      # no benchmark/n


BANNED = [
    "annualized", "annualised", "cagr", "win rate", "win-rate", "winrate",
    "sharpe", "sortino", "risk-adjusted", "risk adjusted", "track record",
    "outperform", "alpha generated", "beat the market", "consistently",
    "proven", "edge confirmed", "expected return", "projected",
]


def test_no_banned_metric_language_in_any_pnl_surface():
    """At the sample sizes this ledger will have for many months, every one of these is
    false precision rather than a hard feature."""
    text = pc.format_pnl(ticker="MSFT", pnl_pct=10.09, pnl_abs=45.4,
                         benchmark_pct=0.75, benchmark_basis="SPY daily close",
                         n_closed=1, holding_days=5.2).lower()
    for phrase in BANNED:
        assert phrase not in text, f"banned metric language: {phrase!r}"


def test_every_pnl_surface_carries_benchmark_n_and_the_quote_caveat():
    text = pc.format_pnl(ticker="MSFT", pnl_pct=10.09, pnl_abs=45.4,
                         benchmark_pct=0.75, benchmark_basis="SPY daily close",
                         n_closed=1, holding_days=5.2)
    assert "SPY" in text, "no P&L without its benchmark"
    assert "1 of 1 recorded" in text, "no P&L without its n"
    assert "No performance conclusion" in text
    assert "QUOTES, not fills" in text
    assert "per share" in text, "abs P&L is per-share; the ledger records no size"


def test_pnl_has_exactly_one_rendering_surface():
    """Tests the CLASS by structure rather than testing today's one surface.

    `close_position()` returns raw `realized_pnl_*` in a dict. That is fine while its
    only consumer is `handle_command`, which renders through `format_pnl` and therefore
    cannot omit the benchmark or the n. It stops being fine the moment a second consumer
    appears — an API route returning that dict, a UI panel, a line in the brief — because
    the dict carries no `n` and nothing would force one in.

    So this asserts the surface COUNT. A new consumer fails here and has to either route
    through `format_pnl` or justify itself, instead of quietly shipping a bare number.
    """
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    consumers = []
    # ⚠️ THE GLOB IS THE TEST. An earlier version scanned only `api/**` and `scripts/**`,
    # which a verifier pointed out would miss a new consumer in any of the 13 top-level
    # `Scope/*.py` files or the 41 `.html`/`.js` UI files — i.e. the test read as a
    # whole-tree guarantee while checking two subtrees. Everything a human could read a
    # number from is scanned now.
    paths = [p for pat in ("**/*.py", "**/*.html", "**/*.js")
             for p in root.glob(pat)
             if "tests" not in p.parts and "node_modules" not in p.parts
             and ".venv" not in p.parts and "scope_env" not in p.parts]
    for path in paths:
        if path.name in ("position_close.py",):
            continue
        src = path.read_text(encoding="utf-8")
        if re.search(r"\bclose_position\b|\brealized_pnl|\bposition_closes\b"
                     r"|\bposition_close\b|\bhandle_command\b|\bformat_pnl\b", src):
            consumers.append(path.relative_to(root).as_posix())
    assert consumers == ["api/routers/positions.py"], (
        f"P&L reached a new surface: {consumers}. Every surface must render through "
        f"format_pnl(), which cannot omit the benchmark or the sample size.")


@pytest.mark.parametrize("arg,label", [
    ("²", "superscript two — isdigit() True but int() raises"),
    ("٢", "Arabic-Indic two — isdigit() AND int() both succeed"),
    ("१", "Devanagari one"),
    ("99999999999999999999", "id larger than SQLite's INTEGER"),
])
def test_exotic_numeric_arguments_are_refused_not_crashed(monkeypatch, arg, label):
    """Regression: `str.isdigit()` is not a number check.

    `"²".isdigit()` is True while `int("²")` raises — an unhandled 500 that Telegram
    retries. Far worse, `"٢".isdigit()` is True AND `int("٢") == 2`, so an Arabic-Indic
    digit silently resolved to a real position id and CLOSED it. `isdecimal()` does not
    help (also True for `٢`); only the ASCII restriction does.
    """
    _stub_quote(monkeypatch, 495.4)
    _seed(ticker="AAPL", cb=f"exotic-{label}")
    reply = pc.handle_command(f"/sell {arg}")          # must not raise
    assert _closes() == [], f"{label} closed a position"
    assert reply, "a refusal must say something, not be silent"


def test_a_rejected_benchmark_bar_is_disclosed_and_not_blamed_on_a_holiday(monkeypatch):
    """Regression: a corrupt SPY bar was dropped silently, shortening the benchmark
    window (measured 1.7964% -> 0.3992%, a 1.40pp error) while the row's own basis
    string blamed the market being shut — on a day the market was open. A wrong cause
    printed beside a wrong number is worse than the number alone: it tells the reader
    not to look."""
    import time

    class _R:
        ok = True
        status_code = 200

        def json(self):
            now = int(time.time())
            return {"chart": {"result": [{
                "meta": {"regularMarketPrice": 495.4, "regularMarketTime": now},
                "timestamp": [now - 5 * 86400, now - 2 * 86400, now - 86400],
                # the newest bar is corrupt and will be rejected
                "indicators": {"quote": [{"close": [500.0, 503.0, float("inf")]}]},
            }]}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    pc.close_position(_seed(cb="skipbar"))
    basis = _closes()[0]["benchmark_basis"]
    assert "REJECTED as corrupt" in basis, "a dropped bar must be disclosed"
    # Assert the unconditional BLAME SENTENCE is gone — not merely that the words
    # "market was shut" never occur, since the corrective wording legitimately says
    # "...and NOT because the market was shut."
    assert "Where this span differs from holding_days, the market was shut" not in basis, \
        "must not assert a holiday as the cause when a bar was rejected as corrupt"


def test_replied_is_not_claimed_when_no_message_was_sent(client, monkeypatch):
    """Regression: the handler returned `replied: True` unconditionally, so with no bot
    token a close was recorded and nobody was told, while the response said otherwise."""
    _stub_quote(monkeypatch, 495.4)
    _seed(cb="reply")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    body = {"message": {"message_id": 1, "chat": {"id": CHAT}, "text": "/sell 1"}}
    r = client.post("/api/telegram/callback", json=body, headers=HDR)
    assert r.json()["replied"] is False
    assert len(_closes()) == 1, "the close is still recorded — only the claim changes"


def test_an_unavailable_benchmark_is_stated_not_omitted():
    text = pc.format_pnl(ticker="MSFT", pnl_pct=10.09, benchmark_pct=None,
                         benchmark_basis="UNAVAILABLE — no closes",
                         n_closed=1, holding_days=5.2)
    assert "UNAVAILABLE" in text
    assert "SPY" in text, "the benchmark line must still appear, saying it is missing"
