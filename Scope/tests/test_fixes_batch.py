#!/usr/bin/env python3
"""
Regression tests for the fix batch: Polymarket tiers + ticker validation (F6),
OSINT tiers (F5), chat never-500 (F1), feed NOISY filter (F9).

Runs under pytest or standalone:  python3 tests/test_fixes_batch.py
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402

_c = TestClient(app)

_s = importlib.util.spec_from_file_location(
    "r7", os.path.join(os.path.dirname(__file__), "..", "rule_07_polymarket.py"))
r7 = importlib.util.module_from_spec(_s)
_s.loader.exec_module(r7)

from scripts.rule_osint import _gdelt_severity  # noqa: E402


# ── F6 Polymarket ────────────────────────────────────────────────────────────
def test_polymarket_tiers():
    assert r7._polymarket_severity(25, 300_000) == "CRITICAL"
    assert r7._polymarket_severity(14, 80_000) == "HIGH"
    assert r7._polymarket_severity(9, 30_000) == "MEDIUM"
    assert r7._polymarket_severity(5, 10_000) is None      # below floor -> skip
    assert r7._polymarket_severity(25, 10_000) is None      # move ok, vol too low


def test_polymarket_ticker_validation():
    assert r7.VALID_TICKER.match("NVDA")
    assert not r7.VALID_TICKER.match("WILL TRUMP WIN")       # question, not a ticker
    assert not r7.VALID_TICKER.match("nvda")                 # must be uppercase symbol


# ── F5 OSINT tiers ───────────────────────────────────────────────────────────
def test_osint_tiers():
    assert _gdelt_severity(-9, 30) == "CRITICAL"
    assert _gdelt_severity(-7, 18) == "HIGH"
    assert _gdelt_severity(-5, 10) == "MEDIUM"


# ── F1 chat never 500 ────────────────────────────────────────────────────────
def test_chat_never_500():
    r = _c.post("/chat", json={"message": "what is moving in defense?", "days": 7})
    assert r.status_code == 200
    assert "answer" in r.json()


# ── F9 NOISY feed filter ─────────────────────────────────────────────────────
def test_feed_noisy_filter():
    rows = _c.get("/alerts?rule=NOISY&days=30&limit=25").json()
    assert all(a["rule"] in ("RULE_07", "RULE_OSINT", "RULE_REDDIT") for a in rows)


def test_feed_default_excludes_noise():
    rows = _c.get("/alerts?days=30&limit=25").json()
    assert all(a["rule"] not in ("RULE_07", "RULE_OSINT", "RULE_REDDIT") for a in rows)


# ── Fix: Polymarket json shadow (UnboundLocalError) ──────────────────────────
def test_polymarket_no_local_json_shadow():
    import ast
    src = open(os.path.join(os.path.dirname(__file__), "..", "rule_07_polymarket.py")).read()
    tree = ast.parse(src)
    # No function-scoped `import json` may exist (it shadows the module for the
    # whole function and breaks json.dumps on the other branch).
    local_json_imports = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Import) and any(a.name == "json" for a in n.names) and n.col_offset > 0
    ]
    assert local_json_imports == []
    assert getattr(r7, "json").__name__ == "json"   # module-level json intact


# ── Fix: OSINT event-type-aware ticker mapping ───────────────────────────────
def test_osint_event_category():
    from scripts.rule_osint import _event_category
    assert _event_category("190") == "MILITARY_ACTION"
    assert _event_category("112") == "DIPLOMATIC"
    assert _event_category("172") == "SANCTIONS"


def test_osint_ticker_mapping_is_specific():
    from scripts.rule_osint import get_tickers_for_event
    # Taiwan military -> semiconductors, not the generic defense/energy basket
    assert "TSM" in get_tickers_for_event("Taiwan Strait", "190")
    assert "LMT" not in get_tickers_for_event("Taiwan Strait", "190")
    # Diplomatic events map to nothing -> dropped, not defaulted
    assert get_tickers_for_event("Middle East", "112") == []
    assert get_tickers_for_event("Nowhere", "190") == []


# ── Fix: Reddit subreddit list ───────────────────────────────────────────────
def test_reddit_subreddit_list():
    import importlib.util as _u
    sp = _u.spec_from_file_location("rr", os.path.join(os.path.dirname(__file__), "..", "scripts", "rule_reddit.py"))
    rr = _u.module_from_spec(sp); sp.loader.exec_module(rr)
    assert "stocks" not in rr.SUBREDDITS and "ValueInvesting" not in rr.SUBREDDITS
    assert "wallstreetbets" in rr.SUBREDDITS and "options" in rr.SUBREDDITS
    # a bad subreddit must return [] (never raise) so the sweep continues
    assert isinstance(rr._fetch_subreddit("this_sub_does_not_exist_zzz"), list)


# ── Fix: ticker normalization (single write point) ───────────────────────────
def test_normalize_ticker():
    from jpt_common import normalize_ticker as n
    assert n("$LMT") == "LMT"
    assert n("lmt") == "LMT"
    assert n("BRK-B") == "BRK.B" == n("brk.b") == n("BRK.B")   # class-share variants collapse
    assert n(None) is None
    assert n("") == ""
    assert n("   ") is None
    assert n(" $COIN $MSTR ") == "COIN MSTR"                   # multi-symbol token-wise


def test_insert_alert_normalizes_on_write():
    from jpt_common import db_connection, insert_alert
    conn = db_connection()
    aid = insert_alert(conn, rule="RULE_06", ticker="$brk-b", severity="HIGH", headline="t")
    stored = conn.execute("SELECT ticker FROM alerts WHERE id=?", (aid,)).fetchone()[0]
    conn.execute("DELETE FROM alerts WHERE id=?", (aid,)); conn.commit(); conn.close()
    assert stored == "BRK.B"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
