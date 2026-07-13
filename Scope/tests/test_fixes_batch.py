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


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
