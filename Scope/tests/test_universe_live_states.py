#!/usr/bin/env python3
"""
`/universe` must tell the truth about WHY it is showing nothing.

This page's entire claim is that an empty universe is *early, not broken*. That
claim is only worth anything if a broken one looks different — otherwise the
reassuring copy ("the collector hasn't accumulated any names yet") is displayed
in exactly the case where it is false, and a dead endpoint reads as a healthy
one with no data.

There are four distinct states and they must stay four:

  * LIVE      — rows exist; the field is real collected coverage
  * EMPTY     — the query succeeded and the answer is genuinely zero
  * EARLY     — the table does not exist yet (the collector creates it on its
                first run), which is a species of empty and reports as such
  * FAULT     — the query could not be answered; this must NOT report as empty

The API half is pinned here. The page half (which panel renders) is asserted in
the same terms by the seam in `api/static/universe.html`.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.join(os.path.dirname(__file__), "..")
ROUTER = os.path.join(ROOT, "api", "routers", "universe.py")
PAGE = os.path.join(ROOT, "api", "static", "universe.html")

_COLS = """ticker TEXT PRIMARY KEY, first_collected_at TEXT, last_seen_at TEXT,
           times_seen INTEGER, market_cap INTEGER, cap_status TEXT, source TEXT"""


def _client(tmp_path, build):
    """A TestClient bound to a throwaway DB shaped by `build`."""
    db = str(tmp_path / "u.db")
    conn = sqlite3.connect(db)
    build(conn)
    conn.commit()
    conn.close()
    os.environ["DATABASE_PATH"] = db
    for mod in [m for m in list(sys.modules) if "universe" in m or m == "jpt_common"]:
        sys.modules.pop(mod, None)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import universe as u
    app = FastAPI()
    app.include_router(u.router)
    return TestClient(app, raise_server_exceptions=False)


def test_live_rows_are_returned_and_excluded_is_filtered(tmp_path):
    def build(c):
        c.execute(f"CREATE TABLE ticker_universe ({_COLS})")
        c.executemany("INSERT INTO ticker_universe VALUES (?,?,?,?,?,?,?)", [
            ("AAA", "2026-07-01 00:00:00", "2026-07-20 00:00:00", 4, 400_000_000, "small", "reddit"),
            ("BBB", "2026-07-02 00:00:00", "2026-07-21 00:00:00", 1, None, "unknown", "reddit"),
            ("HUGE", "2026-07-03 00:00:00", "2026-07-22 00:00:00", 9, 3_000_000_000_000, "excluded", "reddit"),
        ])
    r = _client(tmp_path, build).get("/api/universe")
    assert r.status_code == 200
    body = r.json()
    got = {t["ticker"] for t in body["tickers"]}
    assert got == {"AAA", "BBB"}, f"excluded must be filtered server-side, got {got}"
    assert body["count"] == 2


def test_genuinely_empty_reports_empty(tmp_path):
    def build(c):
        c.execute(f"CREATE TABLE ticker_universe ({_COLS})")
    r = _client(tmp_path, build).get("/api/universe")
    assert r.status_code == 200
    assert r.json()["tickers"] == []


def test_missing_table_is_EARLY_not_a_fault(tmp_path):
    """The collector creates the table on its first run, so its absence is a
    real, truthful zero — not something to alarm the user about."""
    r = _client(tmp_path, lambda c: None).get("/api/universe")
    assert r.status_code == 200, "a not-yet-created table is early, not broken"
    assert r.json()["tickers"] == []


def test_a_REAL_fault_must_not_be_reported_as_an_empty_universe(tmp_path):
    """The regression this file exists for.

    The handler used to `except Exception: rows = []`, so a corrupt, locked or
    structurally wrong table answered with a cheerful empty coverage list — the
    page then told the user "the collector hasn't accumulated any names yet",
    which is a statement it had no basis to make.
    """
    def build(c):
        c.execute("CREATE TABLE ticker_universe (ticker TEXT)")   # wrong shape
    r = _client(tmp_path, build).get("/api/universe")
    assert r.status_code != 200, (
        "a query that could not be answered must not return a 200 empty list — "
        "'I don't know' and 'the answer is zero' are different claims")


# ── the page half ───────────────────────────────────────────────────────────

def test_the_page_reads_the_live_endpoint():
    src = open(PAGE, encoding="utf-8").read()
    assert "/api/universe" in src, "the page is not wired to the coverage endpoint"
    assert "buildFixture(scenario)" in src
    i = src.index("async function loadUniverse")
    body = src[i:i + 900]
    assert "fetch(" in body, "loadUniverse must fetch, not return a fixture by default"


def test_the_page_separates_empty_from_broken():
    src = open(PAGE, encoding="utf-8").read()
    assert "id=\"error\"" in src, "no distinct fault panel"
    assert "kind:'error'" in src or 'kind:"error"' in src
    # the empty copy must be gated on NOT having failed
    assert "!failed && !rows.length" in src, (
        "the empty panel must not render on a failed fetch — that is the whole point")


def test_the_invented_fixture_labels_itself_and_is_opt_in():
    src = open(PAGE, encoding="utf-8").read()
    assert "INVENTED DEMO DATA" in src, "a demo field must say it is invented"
    assert "get('fixture')" in src
    # default (no param) must NOT select a fixture
    i = src.index("async function loadUniverse")
    body = src[i:i + 400]
    assert "|| 'empty'" not in body, (
        "the fixture must be opt-in; defaulting to one hides the real universe")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
