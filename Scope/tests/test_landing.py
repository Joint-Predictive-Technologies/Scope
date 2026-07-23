#!/usr/bin/env python3
"""Phase 2 — brief-as-default-landing. Tests the cache-only landing resolver and
the routing behavior (today → brief; today-missing → yesterday+notice; none →
feed+notice; feed still reachable). Offline; never generates a brief.

Run: python3 tests/test_landing.py  (unit only if fastapi absent) or pytest.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.landing import resolve_landing, inject_brief_header  # noqa: E402


def _db(rows=()):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE briefs (date TEXT PRIMARY KEY, html TEXT)")
    conn.executemany("INSERT INTO briefs (date, html) VALUES (?,?)", rows)
    conn.commit()
    return conn


TODAY, YDAY = "2026-07-23", "2026-07-22"


# --- unit: resolver ---------------------------------------------------------

def test_today_brief_served_when_present():
    conn = _db([(TODAY, "<html><body>TODAY BRIEF</body></html>")])
    mode, html, notice = resolve_landing(conn, TODAY, YDAY)
    assert mode == "brief" and "TODAY BRIEF" in html and notice is None


def test_falls_back_to_yesterday_with_notice():
    conn = _db([(YDAY, "<html><body>YESTERDAY BRIEF</body></html>")])
    mode, html, notice = resolve_landing(conn, TODAY, YDAY)
    assert mode == "brief" and "YESTERDAY BRIEF" in html
    assert notice and "06:30 UTC" in notice and YDAY in notice


def test_falls_back_to_feed_when_no_brief():
    mode, html, notice = resolve_landing(_db([]), TODAY, YDAY)
    assert mode == "feed" and html is None


def test_empty_html_row_is_not_a_brief():
    conn = _db([(TODAY, "")])  # row exists but html empty → not usable
    mode, _, _ = resolve_landing(conn, TODAY, YDAY)
    assert mode == "feed"


def test_missing_briefs_table_degrades_to_feed():
    conn = sqlite3.connect(":memory:")  # no briefs table at all
    mode, _, _ = resolve_landing(conn, TODAY, YDAY)
    assert mode == "feed"


def test_header_injection_adds_feed_link_and_notice():
    out = inject_brief_header("<html><body>X</body></html>", "note here")
    assert "See raw feed" in out and "note here" in out
    assert out.index("scope-brief-bar") < out.index("X")  # bar before content


# --- integration: the actual routing behavior -------------------------------

def _run_integration():
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.testclient import TestClient

    state = {"conn": _db([])}

    app = FastAPI()

    @app.get("/")
    def home():  # mirrors api/main.home() using the same helpers
        mode, html, notice = resolve_landing(state["conn"], TODAY, YDAY)
        if mode == "brief" and html:
            return HTMLResponse(inject_brief_header(html, notice))
        return RedirectResponse(url="/feed?notice=nobrief", status_code=302)

    @app.get("/feed")
    def feed():
        return HTMLResponse("<html><body>RAW FEED</body></html>")

    c = TestClient(app)

    # 1) today's brief present → served at /
    state["conn"] = _db([(TODAY, "<html><body>TODAY BRIEF</body></html>")])
    r = c.get("/")
    assert r.status_code == 200 and "TODAY BRIEF" in r.text and "See raw feed" in r.text

    # 2) today missing, yesterday present → yesterday + notice
    state["conn"] = _db([(YDAY, "<html><body>YDAY BRIEF</body></html>")])
    r = c.get("/")
    assert r.status_code == 200 and "YDAY BRIEF" in r.text and "06:30 UTC" in r.text

    # 3) no brief → redirect to feed with notice
    state["conn"] = _db([])
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/feed?notice=nobrief"
    r = c.get("/")  # follow through
    assert "RAW FEED" in r.text

    # 4) feed reachable at its own URL regardless
    assert c.get("/feed").status_code == 200
    print("  ok  integration: routing today/yesterday/none + feed reachable")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"  ok  {name}"); passed += 1
    try:
        _run_integration(); passed += 1
    except ImportError:
        print("  skip integration (fastapi not installed in this interpreter)")
    print(f"\n{passed} passed")
