#!/usr/bin/env python3
"""Tests for api/receipts.build_receipts — factual alert provenance.

Offline, self-contained. Run: python3 tests/test_receipts.py  (or pytest).
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.receipts import build_receipts  # noqa: E402


def test_cluster_shows_members_and_ptr_links():
    detail = json.dumps({
        "direction": "consensus_buy",
        "distinct_members": 3,
        "members": [
            {"name": "Cisneros, Gilbert Ray", "direction": "buy",
             "transaction_dates": ["2026-06-18"], "sizes": ["$1,001 - $15,000"],
             "filing_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20034906.pdf"},
            {"name": "Meuser, Daniel", "direction": "buy",
             "transaction_dates": ["2026-06-20"], "sizes": ["$15,001 - $50,000"],
             "filing_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20034907.pdf"},
            {"name": "McGuire, John J.", "direction": "buy",
             "transaction_dates": ["2026-06-19"], "sizes": ["$1,001 - $15,000"],
             "filing_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20034908.pdf"},
        ],
    })
    alert = {"rule": "RULE_CLUSTER", "ticker": "SPCX", "detail": detail,
             "verify_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20034906.pdf"}
    r = build_receipts(alert)
    assert r is not None
    assert "3 members bought SPCX" in r["summary"], r["summary"]
    assert "2026-06-18" in r["summary"] and "2026-06-20" in r["summary"], r["summary"]
    names = [i["primary"] for i in r["items"]]
    assert "Cisneros, Gilbert Ray" in names
    # every member carries a real, clickable PTR PDF link
    assert all(i["url"] and i["url"].endswith(".pdf") for i in r["items"])
    assert all(i["url_label"] == "PTR PDF" for i in r["items"])
    assert r["gap"] is None


def test_corroboration_links_contributing_alerts_via_theme_signals():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE alerts (id INTEGER PRIMARY KEY, rule TEXT, ticker TEXT,
                             headline TEXT, created_at TEXT);
        CREATE TABLE theme_signals (id INTEGER PRIMARY KEY, theme_id INTEGER, alert_id INTEGER);
    """)
    contributors = [
        (1, "RULE_01B", "NVDA", "First Touch — Pelosi opens NVDA", "2026-07-01"),
        (2, "RULE_11", "NVDA", "Gov Contract — NVDA awarded $2B", "2026-07-02"),
        (3, "RULE_09", "NVDA", "Lobbying spike on AI chips", "2026-07-02"),
        (4, "RULE_06", "NVDA", "CFO of NVDA bought $500K", "2026-07-03"),
        (5, "RULE_10", "NVDA", "[CORROBORATION] NVDA: 4 signals", "2026-07-03"),
    ]
    conn.executemany("INSERT INTO alerts VALUES (?,?,?,?,?)", contributors)
    conn.executemany("INSERT INTO theme_signals (theme_id, alert_id) VALUES (7, ?)",
                     [(i,) for i in range(1, 6)])
    alert = {"rule": "RULE_10", "ticker": "NVDA", "theme_id": 7,
             "tags": json.dumps({"rules": ["RULE_01B", "RULE_11", "RULE_09", "RULE_06"],
                                 "rule_count": 4})}
    r = build_receipts(alert, conn)
    assert r is not None
    rules_shown = {i["primary"] for i in r["items"]}
    # the 4 contributing families are present; the RULE_10 alert itself is excluded
    assert {"RULE_01B", "RULE_11", "RULE_09", "RULE_06"} <= rules_shown
    assert "RULE_10" not in rules_shown
    # each contributing item links somewhere real
    assert all(i["url"] for i in r["items"])
    assert r["source"]["url"] == "/thesis?id=7"


def test_insider_degrades_gracefully_and_flags_gap():
    alert = {"rule": "RULE_06", "ticker": "GLUE",
             "tags": "Champoux Jennifer,sale,2.4x", "detail": ""}
    r = build_receipts(alert)
    assert r is not None
    assert r["items"][0]["primary"] == "Champoux Jennifer"
    assert "SALE" in (r["items"][0]["secondary"] or "")
    assert r["source"] is None
    assert r["gap"] and "Form 4" in r["gap"]  # honest about the missing receipt


def test_member_trade_flags_missing_ptr_link():
    alert = {"rule": "RULE_01B", "ticker": "META", "member_id": "C001120",
             "full_name": "Dan Crenshaw", "tags": "Crenshaw, Dan|purchase|?|2026-04-28|"}
    r = build_receipts(alert)
    assert r is not None
    assert "Dan Crenshaw" in r["summary"]
    assert "2026-04-28" in r["summary"]
    assert r["gap"] and "PTR" in r["gap"]


def test_empty_and_unknown():
    assert build_receipts(None) is None
    assert build_receipts({}) is None
    # unknown rule with detail falls through to generic
    r = build_receipts({"rule": "RULE_ZZZ", "detail": "some factual detail",
                        "source_url": "https://example.com/doc"})
    assert r is not None and r["source"]["url"] == "https://example.com/doc"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
            passed += 1
    print(f"\n{passed} passed")
