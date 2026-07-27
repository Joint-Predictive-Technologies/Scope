"""RULE_15 Stage 6 — the `earnings` leg completes a fire, and emission stays D1-safe.

The second half matters more than the first. My earlier D1 test ran RULE_15 with no
fixtures, so it may never have reached the emit path at all — proving nothing. These
tests force actual emission and THEN assert nothing is corroborated outside RULE_10.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import (RULE_10_INSTRUMENTS, db_connection, rule10_instruments,
                        rule10_is_valid)
from scripts import rule_15_earnings_nlp as r15


# --- the payoff, on the gate's real logic ---------------------------------

def test_earnings_is_a_distinct_mapped_instrument():
    assert RULE_10_INSTRUMENTS.get("RULE_15") == "earnings"
    assert rule10_instruments(["RULE_15"]) == ["earnings"]


def test_earnings_leg_completes_a_three_instrument_fire():
    assert not rule10_is_valid(["RULE_06", "RULE_01B"]), "two instruments must not fire"
    assert rule10_is_valid(["RULE_06", "RULE_01B", "RULE_15"]), \
        "earnings + insider + congressional should reach 3 instruments"
    assert rule10_instruments(["RULE_06", "RULE_01B", "RULE_15"]) == \
        ["congressional", "earnings", "insider"]


def test_earnings_does_not_collapse_into_insider():
    """RULE_06 is Form 4 (an officer's own trade); RULE_15 is an 8-K earnings release.
    Same EDGAR host, different document population — they must stay separate."""
    assert rule10_instruments(["RULE_06", "RULE_15"]) == ["earnings", "insider"]


def test_earnings_alone_does_not_self_corroborate():
    assert rule10_instruments(["RULE_15", "RULE_15", "RULE_15"]) == ["earnings"]
    assert not rule10_is_valid(["RULE_15", "RULE_15", "RULE_15"])


# --- D1 under REAL emission ----------------------------------------------

def _seed_emitting_history(conn, ticker="TESTCO"):
    """Two like-for-like periods far enough apart to clear the 45-day gap, with a
    >50% jump, plus an earlier row so the cold-start guard does not suppress."""
    today = date.today()
    rows = [(ticker, (today - timedelta(days=240)).isoformat(), f"{ticker}-old", 0.05,
             json.dumps({"washington": 1})),
            (ticker, (today - timedelta(days=120)).isoformat(), f"{ticker}-prior", 0.10,
             json.dumps({"washington": 2})),
            (ticker, (today - timedelta(days=5)).isoformat(), f"{ticker}-cur", 0.90,
             json.dumps({"washington": 9, "tariff": 3}))]
    conn.executemany(
        "INSERT OR REPLACE INTO earnings_sentiment "
        "(ticker, filing_date, accession, political_score, keyword_counts) VALUES (?,?,?,?,?)",
        rows)
    conn.commit()


def test_rule15_actually_emits_when_its_trigger_is_met(monkeypatch):
    """Guard against the earlier test's flaw: prove the emit path is REACHED."""
    monkeypatch.setattr(r15, "TRACKED_TICKERS", ["TESTCO"])
    monkeypatch.setattr(r15, "_fetch_8k_filings", lambda t, days_back=120: [])
    conn = db_connection()
    # the real schema has more columns — name them rather than positional VALUES
    conn.execute("INSERT OR REPLACE INTO tickers (symbol, company_name, cik) "
                 "VALUES ('TESTCO','Test Co','0000000999')")
    r15._ensure_tables(conn)
    _seed_emitting_history(conn)
    conn.commit(); conn.close()

    r15.run(emit=True)

    conn = db_connection()
    n = conn.execute("SELECT COUNT(*) c FROM alerts WHERE rule='RULE_15'").fetchone()["c"]
    conn.close()
    assert n >= 1, "RULE_15 still never reaches its emit path — the D1 test below is vacuous"


def test_rule15_emitting_corroborates_nothing_outside_rule10(monkeypatch):
    """THE D1 PROOF, now with the rule actually emitting.

    Seeds the exact RULE_08 + RULE_09 co-occurrence the removed shadow gate keyed on,
    then makes RULE_15 emit onto the same ticker. Nothing may be marked corroborated.
    """
    monkeypatch.setattr(r15, "TRACKED_TICKERS", ["TESTCO"])
    monkeypatch.setattr(r15, "_fetch_8k_filings", lambda t, days_back=120: [])
    conn = db_connection()
    # the real schema has more columns — name them rather than positional VALUES
    conn.execute("INSERT OR REPLACE INTO tickers (symbol, company_name, cik) "
                 "VALUES ('TESTCO','Test Co','0000000999')")
    r15._ensure_tables(conn)
    _seed_emitting_history(conn)
    for rule in ("RULE_08", "RULE_09"):
        conn.execute("INSERT INTO alerts (rule, ticker, headline, severity, lifecycle_stage) "
                     "VALUES (?,?,?,'HIGH','created')", (rule, "TESTCO", f"{rule} on TESTCO"))
    conn.commit(); conn.close()

    r15.run(emit=True)

    conn = db_connection()
    rows = conn.execute("SELECT rule, lifecycle_stage FROM alerts "
                        "WHERE ticker='TESTCO'").fetchall()
    emitted = [r for r in rows if r["rule"] == "RULE_15"]
    conn.close()
    assert emitted, "RULE_15 did not emit — this test would be vacuous"
    for r in rows:
        assert r["lifecycle_stage"] != "corroborated", \
            f"{r['rule']} corroborated by RULE_15 emission — the shadow gate is back"
