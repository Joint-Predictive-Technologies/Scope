"""RULE_16 (13F institutional) — emission, mapping discipline, timing, and the gate leg.

The lessons this file exists to pin, each learned from a prior rule that got it wrong:
  * WS3 / RULE_08  — single-symbol tickers only; a composite can never match.
  * RULE_14        — a bare substring/name match invents tickers; map by CUSIP only.
  * RULE_15        — never attribute a record to a ticker you did not verify; drop it.
  * RULE_14 again  — never collapse a trailing window into the cron instant.
  * RULE_13        — never let a source failure look like a clean zero.
  * RULE_01        — a gate rule must be MAPPED, or it becomes a phantom instrument.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common
from jpt_common import (RULE_10_EXCLUDED, RULE_10_INSTRUMENTS, db_connection,
                        rule10_instruments, rule10_is_valid)
from scripts import rule_16_institutional as r16

TODAY = date.today()
ISO = TODAY.isoformat()


# ---------------------------------------------------------------------------
# fixtures — a fake 13F universe, no network
# ---------------------------------------------------------------------------

def _fake_sources(monkeypatch, filings, tables, figi):
    monkeypatch.setattr(r16, "recent_13f", lambda cik, limit=1: filings.get(cik, []))
    monkeypatch.setattr(r16, "holdings", lambda cik, acc: tables.get(acc, {}))
    monkeypatch.setattr(r16, "map_cusips",
                        lambda cusips, budget_deadline=None:
                            {c: figi[c] for c in cusips if c in figi})


CONVICTION = {"0000000001": [{"accession": "0001-26-000001", "filing_date": ISO,
                              "report_date": "2026-03-31", "filer_name": "Conviction Capital"}]}
TABLE = {"0001-26-000001": {
    "AAA111111": {"issuer": "ADOBE INC", "class": "COM", "value": 50_000_000.0, "shares": 100_000.0},
    "BBB222222": {"issuer": "SOME ETF TRUST", "class": "COM", "value": 90_000_000.0, "shares": 200_000.0},
    "CCC333333": {"issuer": "TINY HOLDING CO", "class": "COM", "value": 100_000.0, "shares": 50.0},
    "DDD444444": {"issuer": "UNMAPPABLE FOREIGN LTD", "class": "ORD SHS", "value": 60_000_000.0, "shares": 10_000.0},
}}
FIGI = {
    "AAA111111": {"ticker": "ADBE", "name": "ADOBE INC", "security_type": "Common Stock"},
    # BBB is an ETF -> map_cusips would filter it; modelled by omission below
    "CCC333333": {"ticker": "TINY", "name": "TINY HOLDING CO", "security_type": "Common Stock"},
    # DDD deliberately absent -> unresolved CUSIP
}


@pytest.fixture
def whale(monkeypatch):
    """A filer whose FIRST filing is being seen — cold start, so it baselines."""
    _fake_sources(monkeypatch, CONVICTION, TABLE, FIGI)
    return {"0000000001": "Conviction Capital"}


# Quarter 2 for the same filer: ADBE grows 100k -> 300k, and a genuinely new name.
Q2 = {"0000000001": [{"accession": "0001-26-000002", "filing_date": ISO,
                      "report_date": "2026-06-30", "filer_name": "Conviction Capital"}]}
TABLE_Q2 = {"0001-26-000002": {
    "AAA111111": {"issuer": "ADOBE INC", "class": "COM",
                  "value": 120_000_000.0, "shares": 300_000.0},
    "EEE555555": {"issuer": "BRAND NEW CO", "class": "COM",
                  "value": 40_000_000.0, "shares": 80_000.0},
}}
FIGI_Q2 = dict(FIGI, EEE555555={"ticker": "BNEW", "name": "BRAND NEW CO",
                                "security_type": "Common Stock"})


@pytest.fixture
def whale_emitting(monkeypatch):
    """Baseline already established, so the NEXT run emits real position changes.

    Most emission tests need this: after the cold-start guard, a filer's first
    filing is deliberately silent.
    """
    _fake_sources(monkeypatch, CONVICTION, TABLE, FIGI)
    r16.run(emit=True, whales={"0000000001": "Conviction Capital"})   # baseline
    _fake_sources(monkeypatch, Q2, TABLE_Q2, FIGI_Q2)
    return {"0000000001": "Conviction Capital"}


# ---------------------------------------------------------------------------
# Stage 2 — emission
# ---------------------------------------------------------------------------

def test_emits_single_symbol_tickers_only(whale_emitting):
    r = r16.run(emit=True, whales=whale_emitting)
    conn = db_connection()
    rows = conn.execute("SELECT ticker FROM alerts WHERE rule='RULE_16'").fetchall()
    conn.close()
    assert rows, "expected at least one RULE_16 alert"
    for row in rows:
        t = row["ticker"]
        assert t and " " not in t and "$" not in t, f"non-single-symbol ticker {t!r}"


def test_unresolved_cusip_is_dropped_not_guessed(whale):
    """RULE_15's lesson: never attribute a holding to a ticker you did not resolve."""
    r16.run(emit=True, whales=whale)
    conn = db_connection()
    tickers = {r["ticker"] for r in
               conn.execute("SELECT ticker FROM alerts WHERE rule='RULE_16'")}
    stored = {r["cusip"] for r in
              conn.execute("SELECT cusip FROM institutional_holdings")}
    conn.close()
    assert "DDD444444" not in stored, "unresolved CUSIP was stored anyway"
    # and nothing invented a ticker from the issuer name
    assert not {t for t in tickers if t.startswith("UNMAP")}


def test_etf_is_excluded(whale):
    """A basket is not a conviction signal — map_cusips only allows Common Stock/REIT."""
    r16.run(emit=True, whales=whale)
    conn = db_connection()
    stored = {r["cusip"] for r in conn.execute("SELECT cusip FROM institutional_holdings")}
    conn.close()
    assert "BBB222222" not in stored


def test_materiality_floor_drops_tiny_positions(whale):
    r16.run(emit=True, whales=whale)
    conn = db_connection()
    stored = {r["cusip"] for r in conn.execute("SELECT cusip FROM institutional_holdings")}
    conn.close()
    assert "CCC333333" not in stored, "a $100k position cleared MIN_POSITION_USD"


def test_quant_sweep_is_skipped(monkeypatch):
    """Finding 3: a 13F holding a third of the market is an index, not a signal."""
    big = {f"Q{i:08d}": {"issuer": f"CO {i}", "class": "COM",
                         "value": 10_000_000.0, "shares": 1000.0}
           for i in range(r16.MAX_HOLDINGS + 5)}
    filings = {"0000000002": [{"accession": "0002-26-000001", "filing_date": ISO,
                               "report_date": "2026-03-31", "filer_name": "Quant Sweep LP"}]}
    _fake_sources(monkeypatch, filings, {"0002-26-000001": big}, {})
    r = r16.run(emit=True, whales={"0000000002": "Quant Sweep LP"})
    assert r["skipped_quant"] == 1
    assert r["emitted"] == 0


def test_new_vs_increase_classification():
    assert r16.classify(0, 100) == ("NEW", "HIGH")
    assert r16.classify(100, 250)[0] == "ADD"
    assert r16.classify(100, 210)[1] == "HIGH"      # +110% -> HIGH
    assert r16.classify(100, 160)[1] == "MEDIUM"    # +60%  -> MEDIUM
    assert r16.classify(100, 120) is None           # +20%  -> not a signal
    assert r16.classify(100, 0) is None             # an exit is not a buy signal


def test_increase_detected_against_prior_quarter(whale_emitting):
    r16.run(emit=True, whales=whale_emitting)
    conn = db_connection()
    rows = conn.execute("SELECT headline, ticker, tags FROM alerts "
                        "WHERE rule='RULE_16'").fetchall()
    conn.close()
    by_kind = {json.loads(r["tags"])["kind"]: r for r in rows}
    assert "ADD" in by_kind, f"no increase detected; got {list(by_kind)}"
    add = by_kind["ADD"]
    assert add["ticker"] == "ADBE" and "increase" in add["headline"]
    # 100k -> 300k is +200%, which is >=100% so it must be HIGH, not MEDIUM
    assert "200% increase" in add["headline"]


# ---------------------------------------------------------------------------
# Stage 2 — reliability
# ---------------------------------------------------------------------------

def test_source_failure_is_loud_not_a_silent_zero(monkeypatch):
    """RULE_13's lesson: a dead source must never log a clean zero."""
    def boom(cik, limit=1):
        raise r16.SourceUnavailable("data.sec.gov -> HTTP 503")
    monkeypatch.setattr(r16, "recent_13f", boom)
    r = r16.run(emit=True, whales={"0000000001": "Conviction Capital"})
    assert r["failures"], "a total source failure produced no failure record"
    conn = db_connection()
    row = conn.execute("SELECT notes FROM activity_log WHERE source='RULE_16' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None, "no activity_log row on failure"
    assert "CRITICAL" in (row["notes"] or ""), f"failure not marked CRITICAL: {row['notes']!r}"


def test_activity_row_written_on_the_happy_path(whale_emitting):
    r16.run(emit=True, whales=whale_emitting)
    conn = db_connection()
    row = conn.execute("SELECT * FROM activity_log WHERE source='RULE_16' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None and row["alerts_emitted"] >= 1


def test_time_budget_produces_a_partial_and_still_logs(monkeypatch, whale):
    r = r16.run(emit=True, whales=whale, time_budget=-1)   # already past deadline
    assert r["partial"] is True
    conn = db_connection()
    row = conn.execute("SELECT notes FROM activity_log WHERE source='RULE_16' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert "PARTIAL" in (row["notes"] or "")


def test_reprocessing_the_same_filing_emits_nothing_new(whale_emitting):
    first = r16.run(emit=True, whales=whale_emitting)
    second = r16.run(emit=True, whales=whale_emitting)
    assert first["emitted"] >= 1
    assert second["emitted"] == 0, "a re-run re-emitted an already-seen accession"


# ---------------------------------------------------------------------------
# Stage 2 — timing preservation (the RULE_14 defect)
# ---------------------------------------------------------------------------

def test_stale_filings_outside_the_lookback_are_not_ingested(monkeypatch):
    old = (TODAY - timedelta(days=r16.LOOKBACK_DAYS + 30)).isoformat()
    filings = {"0000000003": [{"accession": "0003-26-000001", "filing_date": old,
                               "report_date": "2025-09-30", "filer_name": "Old Filer"}]}
    _fake_sources(monkeypatch, filings, {"0003-26-000001": TABLE["0001-26-000001"]}, FIGI)
    r = r16.run(emit=True, whales={"0000000003": "Old Filer"})
    assert r["emitted"] == 0, "a stale filing was ingested and stamped as if it were new"


def test_alert_carries_the_filing_date_as_event_date(whale_emitting):
    """Timing provenance: the disclosure date, not the quarter-end, not `now`."""
    r16.run(emit=True, whales=whale_emitting)
    conn = db_connection()
    row = conn.execute("SELECT event_date, tags FROM alerts WHERE rule='RULE_16' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    tags = json.loads(row["tags"])
    assert row["event_date"] == ISO == tags["filing_date"]
    assert tags["report_date"] == "2026-06-30"       # the quarter, kept separately


def test_headline_says_disclosed_not_buying(whale_emitting):
    """13F is a 45-day-old disclosure. The copy must not imply present-tense buying."""
    r16.run(emit=True, whales=whale_emitting)
    conn = db_connection()
    rows = conn.execute("SELECT headline, detail FROM alerts WHERE rule='RULE_16'").fetchall()
    conn.close()
    for r in rows:
        assert "disclos" in r["headline"].lower()
        assert "not evidence of buying today" in r["detail"]


# ---------------------------------------------------------------------------
# Stage 3 — the gate instrument
# ---------------------------------------------------------------------------

def test_rule16_is_mapped_not_a_phantom_instrument():
    """RULE_01's trap: an eligible-but-unmapped rule becomes its own instrument."""
    assert RULE_10_INSTRUMENTS.get("RULE_16") == "institutional"
    assert "RULE_16" not in RULE_10_EXCLUDED


def test_rule16_contributes_a_distinct_instrument():
    assert rule10_instruments(["RULE_16"]) == ["institutional"]


def test_institutional_does_not_collapse_into_insider():
    """Form 4 (officer's own trade, 2 days) != 13F (external manager, quarterly)."""
    assert rule10_instruments(["RULE_06", "RULE_16"]) == ["insider", "institutional"]
    assert RULE_10_INSTRUMENTS["RULE_06"] != RULE_10_INSTRUMENTS["RULE_16"]


def test_gate_change_is_the_map_entry_only():
    """No threshold, window or counting change came with this rule."""
    from scripts import rule_10_corroboration as r10
    assert jpt_common.RULE_10_MIN_INSTRUMENTS == 3
    assert r10.CONVERGENCE_WINDOW_DAYS == 14
    assert r10.DEDUP_WINDOW_DAYS == 7
    assert r10.MIN_DISTINCT_INSTRUMENTS == jpt_common.RULE_10_MIN_INSTRUMENTS


# ---------------------------------------------------------------------------
# Stage 4 — the convergence payoff, on the gate's real logic
# ---------------------------------------------------------------------------

def test_whale_leg_completes_a_three_instrument_fire():
    assert not rule10_is_valid(["RULE_06", "RULE_01B"]), "two instruments should not fire"
    assert rule10_is_valid(["RULE_06", "RULE_01B", "RULE_16"]), \
        "whale + insider + congressional should reach 3 instruments"
    assert rule10_instruments(["RULE_06", "RULE_01B", "RULE_16"]) == \
        ["congressional", "insider", "institutional"]


def test_whale_alone_does_not_self_corroborate():
    """Attribution control: many whales on one ticker are still ONE instrument."""
    assert rule10_instruments(["RULE_16", "RULE_16", "RULE_16"]) == ["institutional"]
    assert not rule10_is_valid(["RULE_16", "RULE_16", "RULE_16"])


def test_whale_plus_congressional_trio_still_does_not_fire():
    """D1 must hold: the congressional views collapse, so 4 rules = 2 instruments."""
    rules = ["RULE_01B", "RULE_02", "RULE_CLUSTER", "RULE_16"]
    assert rule10_instruments(rules) == ["congressional", "institutional"]
    assert not rule10_is_valid(rules)


# ---------------------------------------------------------------------------
# Cold start — found by the first live smoke, which emitted 27 of 27 as "NEW"
# ---------------------------------------------------------------------------

def test_first_filing_for_a_filer_is_a_silent_baseline(whale):
    """An empty table makes every holding look like a new position.

    The first live run emitted all 27 Berkshire holdings as NEW; across the whale
    list that is a few hundred spurious HIGH alerts on day one, every one of them an
    artefact of cold start rather than a real disclosure. The first filing seen for a
    filer must be stored and NOT emitted.
    """
    r = r16.run(emit=True, whales=whale)
    assert r["baselined"] == 1
    assert r["stored"] >= 1, "baseline must still be STORED, or nothing can diff later"
    assert r["emitted"] == 0, "cold start emitted alerts instead of baselining"


def test_real_change_emits_on_the_quarter_after_the_baseline(monkeypatch, whale):
    r16.run(emit=True, whales=whale)                      # Q1 -> baseline, silent
    q2 = {"0000000001": [{"accession": "0001-26-000002", "filing_date": ISO,
                          "report_date": "2026-06-30", "filer_name": "Conviction Capital"}]}
    t2 = {"0001-26-000002": {
        "AAA111111": {"issuer": "ADOBE INC", "class": "COM",
                      "value": 120_000_000.0, "shares": 300_000.0},   # 100k -> 300k
        "EEE555555": {"issuer": "BRAND NEW CO", "class": "COM",
                      "value": 40_000_000.0, "shares": 80_000.0},     # genuinely new
    }}
    figi2 = dict(FIGI)
    figi2["EEE555555"] = {"ticker": "BNEW", "name": "BRAND NEW CO",
                          "security_type": "Common Stock"}
    _fake_sources(monkeypatch, q2, t2, figi2)
    r = r16.run(emit=True, whales={"0000000001": "Conviction Capital"})
    assert r["baselined"] == 0, "second quarter was wrongly treated as a baseline"
    assert r["emitted"] == 2, f"expected an ADD and a NEW, got {r['emitted']}"
    conn = db_connection()
    kinds = {json.loads(x["tags"])["kind"] for x in
             conn.execute("SELECT tags FROM alerts WHERE rule='RULE_16'")}
    conn.close()
    assert kinds == {"NEW", "ADD"}
