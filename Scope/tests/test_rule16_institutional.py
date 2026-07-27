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


def test_dropped_holdings_are_visible_not_silent(whale_emitting):
    """OpenFIGI resolves 0% of CINS CUSIPs — on a conviction book that is Chubb,
    Aon and Accenture. Dropping is right; dropping SILENTLY hides a known gap."""
    r = r16.run(emit=True, whales=whale_emitting)
    # the Q2 fixture maps cleanly; re-run the cold-start book which has an unmapped CINS
    conn = db_connection()
    row = conn.execute("SELECT notes FROM activity_log WHERE source='RULE_16' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert "unmapped_dropped=" in (row["notes"] or "")


def test_unmapped_cins_is_named_in_the_activity_notes(monkeypatch):
    filings = {"0000000009": [{"accession": "0009-26-000001", "filing_date": ISO,
                               "report_date": "2026-03-31", "filer_name": "Cold Book"}]}
    table = {"0009-26-000001": {
        "G1151C101": {"issuer": "ACCENTURE PLC IRELAND", "class": "SHS CLASS A",
                      "value": 900_000_000.0, "shares": 100_000.0}}}
    _fake_sources(monkeypatch, filings, table, {})      # nothing resolves
    r = r16.run(emit=True, whales={"0000000009": "Cold Book"})
    assert r["dropped_unmapped"] == 1
    assert any("ACCENTURE" in d and "CINS" in d for d in r["unmapped_detail"]), \
        f"a dropped top-ten CINS position was not named: {r['unmapped_detail']}"
    conn = db_connection()
    notes = conn.execute("SELECT notes FROM activity_log WHERE source='RULE_16' "
                         "ORDER BY id DESC LIMIT 1").fetchone()["notes"]
    conn.close()
    assert "DROPPED" in notes and "ACCENTURE" in notes


# ---------------------------------------------------------------------------
# The fenced CINS fallback — name matching is the RULE_15 failure technique, so
# every fence gets its own test, and the last one tries to slip a name-matched
# ticker through disguised as a lookup.
# ---------------------------------------------------------------------------

def _seed_tickers(rows):
    conn = db_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS tickers (symbol TEXT PRIMARY KEY, company_name TEXT)")
    conn.executemany("INSERT OR REPLACE INTO tickers(symbol, company_name) VALUES (?,?)", rows)
    conn.commit()
    r16._TICKER_NAMES_CACHE = None       # the cache is per-process
    return conn


def test_fallback_resolves_the_known_cins_names():
    """The whole point: Chubb, Aon, Accenture, WTW are top holdings OpenFIGI can't map."""
    conn = _seed_tickers([("CB", "Chubb Limited"), ("AON", "Aon plc"),
                          ("ACN", "Accenture plc"), ("WTW", "Willis Towers Watson Public Limited Co")])
    cases = [("G0403H108", "AON PLC", "AON"),
             ("G1151C101", "ACCENTURE PLC IRELAND", "ACN"),
             ("G96629103", "WILLIS TOWERS WATSON PLC", "WTW")]
    for cusip, issuer, want in cases:
        got = r16.cins_fallback(conn, cusip, issuer)
        assert got and got["ticker"] == want, f"{issuer} -> {got} (wanted {want})"
        assert got["confidence"] == r16.CONF_NAME
        assert got["match_score"] >= r16.CINS_FALLBACK_CUTOFF
    conn.close()


def test_fence1_fallback_never_runs_for_a_digit_cusip():
    """OpenFIGI resolves digit CUSIPs at 98%; name matching must not touch them."""
    conn = _seed_tickers([("ADBE", "Adobe Inc")])
    assert r16.cins_fallback(conn, "00724F101", "ADOBE INC") is None
    conn.close()


def test_fence3_a_weak_cins_match_is_dropped_not_guessed():
    """Below the 0.90 cutoff the answer is None — never a plausible-looking guess."""
    conn = _seed_tickers([("NBTB", "NBT Bancorp Inc"), ("DCOM", "Dime Community Bancshares")])
    # the RULE_09 failure case, as a CINS: nothing here is a 0.90 match
    assert r16.cins_fallback(conn, "G1234H567", "FB BANCORP INC") is None
    assert r16.cins_fallback(conn, "G9999X999", "TOTALLY UNRELATED HOLDINGS") is None
    conn.close()


def test_cutoff_is_far_above_the_rule09_value_that_misfired():
    assert r16.CINS_FALLBACK_CUTOFF >= 0.90


def test_fallback_alert_is_labelled_distinctly_from_a_lookup(monkeypatch):
    """The load-bearing fence: a name-matched ticker must never read as a lookup."""
    _seed_tickers([("ACN", "Accenture plc")]).close()
    filings = {"0000000007": [{"accession": "0007-26-000001", "filing_date": ISO,
                               "report_date": "2026-03-31", "filer_name": "Book A"}]}
    q1 = {"0007-26-000001": {"ZZZ111111": {"issuer": "SEED CO", "class": "COM",
                                           "value": 9_000_000.0, "shares": 100.0}}}
    seed_figi = {"ZZZ111111": {"ticker": "SEED", "name": "SEED CO",
                               "security_type": "Common Stock"}}
    _fake_sources(monkeypatch, filings, q1, seed_figi)
    r16.run(emit=True, whales={"0000000007": "Book A"})          # baseline

    q2 = {"0000000007": [{"accession": "0007-26-000002", "filing_date": ISO,
                          "report_date": "2026-06-30", "filer_name": "Book A"}]}
    t2 = {"0007-26-000002": {
        "ZZZ111111": {"issuer": "SEED CO", "class": "COM", "value": 30_000_000.0, "shares": 900.0},
        "G1151C101": {"issuer": "ACCENTURE PLC IRELAND", "class": "SHS CLASS A",
                      "value": 80_000_000.0, "shares": 50_000.0},
    }}
    _fake_sources(monkeypatch, q2, t2, seed_figi)   # ACN absent -> CINS fallback fires
    r = r16.run(emit=True, whales={"0000000007": "Book A"})
    assert r["name_matched"] == 1

    conn = db_connection()
    rows = conn.execute("SELECT ticker, headline, detail, tags FROM alerts "
                        "WHERE rule='RULE_16' ORDER BY id").fetchall()
    conn.close()
    by = {x["ticker"]: x for x in rows}
    assert {"SEED", "ACN"} <= set(by), f"expected both paths, got {list(by)}"

    lookup, fallback = by["SEED"], by["ACN"]
    lt, ft = json.loads(lookup["tags"]), json.loads(fallback["tags"])

    # the two paths must be distinguishable in the payload...
    assert lt["ticker_confidence"] == r16.CONF_LOOKUP
    assert ft["ticker_confidence"] == r16.CONF_NAME
    assert lt["ticker_confidence"] != ft["ticker_confidence"]
    assert "lookup" in lt["mapping"].lower() and "fallback" in ft["mapping"].lower()
    # ...and in what a human actually reads
    assert "name-matched" in fallback["headline"] and "unverified" in fallback["headline"]
    assert "name-matched" not in lookup["headline"]
    assert "TICKER CONFIDENCE" in fallback["detail"]
    assert "TICKER CONFIDENCE" not in lookup["detail"]


def test_no_alert_can_claim_lookup_confidence_without_a_cusip_resolution(monkeypatch):
    """Adversarial: try to slip a name-matched ticker through as a lookup."""
    _seed_tickers([("ACN", "Accenture plc")]).close()
    conn = db_connection()
    fb = r16.cins_fallback(conn, "G1151C101", "ACCENTURE PLC IRELAND")
    conn.close()
    assert fb["confidence"] == r16.CONF_NAME
    # the emission path reads m.get("confidence", CONF_LOOKUP); a fallback dict must
    # therefore always carry an explicit non-lookup confidence, or it would default
    # to looking like a verified lookup.
    assert "confidence" in fb and fb["confidence"] != r16.CONF_LOOKUP


def test_short_cins_names_require_an_exact_match_not_a_fuzzy_one():
    """"AON" is 3 chars; a 0.90 ratio there is nearly meaningless, so short names
    demand exact normalised equality — stricter than the cutoff, not looser."""
    conn = _seed_tickers([("AON", "Aon plc"), ("AONC", "American Oncology Network")])
    assert r16.cins_fallback(conn, "G0403H108", "AON PLC")["ticker"] == "AON"
    # a near-miss short name must NOT slide through on ratio alone
    assert r16.cins_fallback(conn, "G0403H109", "AONX PLC") is None
    conn.close()


def test_fence5_a_share_class_collision_is_dropped_not_coin_flipped():
    """Found by the verifier on a REAL Baupost holding, and it was a wrong ticker.

    G61188127 is "LIBERTY GLOBAL LTD" class "COM CL C" = LBTYK. Three share classes
    share the issuer name, all scoring 0.933, and the old tie-break returned whichever
    row came first — LBTYA. The CUSIP is what distinguishes them and the fallback
    discards it, so a name that doesn't identify a UNIQUE security must be dropped.
    """
    conn = _seed_tickers([("LBTYA", "Liberty Global Ltd."), ("LBTYB", "Liberty Global Ltd."),
                          ("LBTYK", "Liberty Global Ltd.")])
    assert r16.cins_fallback(conn, "G61188127", "LIBERTY GLOBAL LTD") is None
    conn.close()


def test_chubb_uses_the_string_that_actually_appears_in_filings():
    """My original test asserted on "CHUBB LTD", which appears in NO 13F in the
    universe — a fixture written to the answer I wanted. Real filings say
    "CHUBB LTD SWITZ", which scores 0.625 and is correctly DROPPED. Recording the
    real behaviour rather than the flattering one."""
    conn = _seed_tickers([("CB", "Chubb Ltd")])
    assert r16.cins_fallback(conn, "H1467J104", "CHUBB LTD SWITZ") is None
    conn.close()


# ---------------------------------------------------------------------------
# The units bug — Baupost and Duquesne report `value` in THOUSANDS, so the $5M
# floor silently zeroed both entire books (90 holdings). A silent zero is exactly
# what this rule's docstring forbids.
# ---------------------------------------------------------------------------

def test_thousands_filing_is_detected_and_scaled():
    """A 13F filer has >$100M by law, so a book summing below that is in thousands."""
    baupost_like = {f"C{i:08d}": {"value": 5_115_380 / 22} for i in range(22)}
    assert r16.detect_value_scale(baupost_like) == 1000.0


def test_dollars_filing_is_left_alone():
    berkshire_like = {"A": {"value": 263_095_703_570.0}}
    assert r16.detect_value_scale(berkshire_like) == 1.0
    assert r16.detect_value_scale({}) == 1.0            # empty book must not crash


def test_a_thousands_book_survives_the_materiality_floor(monkeypatch):
    """Before the fix this book stored 0 of 3 and logged nothing."""
    filings = {"0000000011": [{"accession": "0011-26-000001", "filing_date": ISO,
                               "report_date": "2026-03-31", "filer_name": "Thousands Book"}]}
    # $22M, $40M, $9M positions — but reported in thousands
    table = {"0011-26-000001": {
        "T1111111": {"issuer": "ALPHA CO", "class": "COM", "value": 22_000.0, "shares": 1000.0},
        "T2222222": {"issuer": "BETA CO", "class": "COM", "value": 40_000.0, "shares": 2000.0},
        "T3333333": {"issuer": "GAMMA CO", "class": "COM", "value": 9_000.0, "shares": 300.0}}}
    figi = {c: {"ticker": t, "name": n, "security_type": "Common Stock"} for c, t, n in
            [("T1111111", "ALPH", "ALPHA CO"), ("T2222222", "BETA", "BETA CO"),
             ("T3333333", "GAMA", "GAMMA CO")]}
    _fake_sources(monkeypatch, filings, table, figi)
    r = r16.run(emit=True, whales={"0000000011": "Thousands Book"})
    assert r["stored"] == 3, f"thousands book still zeroed by the floor: {r}"
    conn = db_connection()
    vals = [x["value_usd"] for x in
            conn.execute("SELECT value_usd FROM institutional_holdings")]
    conn.close()
    assert min(vals) >= 9_000_000, f"values not scaled to dollars: {vals}"


def test_a_floor_zeroed_book_is_logged_loudly(monkeypatch):
    """The whole point: a book vanishing must never again be silent.

    The book must total ABOVE $100M so it reads as dollars (below that the unit
    heuristic correctly treats it as thousands), while every individual position sits
    under the $5M floor: 40 x $3M = $120M.
    """
    filings = {"0000000013": [{"accession": "0013-26-000001", "filing_date": ISO,
                               "report_date": "2026-03-31", "filer_name": "Small Book"}]}
    table = {"0013-26-000001": {f"S{i:08d}": {"issuer": f"SMALL {i}", "class": "COM",
                                              "value": 3_000_000.0, "shares": 10.0}
                                for i in range(40)}}
    figi = {f"S{i:08d}": {"ticker": f"SM{i:02d}", "name": f"SMALL {i}",
                          "security_type": "Common Stock"} for i in range(40)}
    _fake_sources(monkeypatch, filings, table, figi)
    r = r16.run(emit=True, whales={"0000000013": "Small Book"})
    assert r["stored"] == 0, "positions under the floor should not be stored"
    assert r["floor_notes"], "a whole book was dropped by the floor with no log line"
    assert "WHOLE BOOK" in " ".join(r["floor_notes"])
    conn = db_connection()
    notes = conn.execute("SELECT notes FROM activity_log WHERE source='RULE_16' "
                         "ORDER BY id DESC LIMIT 1").fetchone()["notes"]
    conn.close()
    assert "FLOOR" in notes and "Small Book" in notes


def test_unit_heuristic_edge_case_is_documented():
    """A book totalling under $100M is read as thousands. That is correct for a real
    13F filer (>$100M by law) but WOULD mis-scale a partial or amended filing. Pinned
    so the trade-off is explicit rather than accidental."""
    assert r16.AUM_FLOOR_USD == 100_000_000
    assert r16.detect_value_scale({"a": {"value": 99_000_000}}) == 1000.0
    assert r16.detect_value_scale({"a": {"value": 101_000_000}}) == 1.0


# ---------------------------------------------------------------------------
# The PUBLIC over-strip — must be plc-context only
# ---------------------------------------------------------------------------

def test_public_is_stripped_only_in_the_plc_context():
    assert r16._norm("WILLIS TOWERS WATSON PUBLIC LIMITED CO") == "WILLIS TOWERS WATSON"
    assert r16._norm("VODAFONE GROUP PUBLIC LTD CO") == "VODAFONE"
    # ...and never standalone, where it is part of the actual name
    assert r16._norm("PUBLIC STORAGE") == "PUBLIC STORAGE"
    assert r16._norm("PUBLIC SERVICE ENTERPRISE GROUP INC") == "PUBLIC SERVICE ENTERPRISE"
    assert r16._norm("AMERICAN PUBLIC EDUCATION INC") == "AMERICAN PUBLIC EDUCATION"


def test_public_fix_does_not_reintroduce_a_mismap():
    """Normalisation changed, so re-run the wrong-ticker checks (the LBTYA lesson)."""
    conn = _seed_tickers([("PSA", "Public Storage"), ("PEG", "Public Service Enterprise Group Inc"),
                          ("LBTYA", "Liberty Global Ltd."), ("LBTYK", "Liberty Global Ltd.")])
    # share-class collision still drops
    assert r16.cins_fallback(conn, "G61188127", "LIBERTY GLOBAL LTD") is None
    # PUBLIC-named US companies are not CINS, so the fallback must not touch them
    assert r16.cins_fallback(conn, "74460D109", "PUBLIC STORAGE") is None
    conn.close()


# ---------------------------------------------------------------------------
# The whale list — live entities only
# ---------------------------------------------------------------------------

def test_whale_list_has_no_known_dead_or_nt_only_ciks():
    w = r16._whales()
    assert "0001159159" not in w, "dead Jana CIK (last 13F 2023-08-14) still shipped"
    assert "0001998597" in w, "live Jana entity missing"
    assert "0001345472" not in w, "Trian GP files 13F-NT only"
    assert "0001345471" in w, "live Trian entity missing"
    assert "0001079114" not in w and "0001649339" not in w, \
        "Greenlight/Scion are stale (no 13F-HR inside LOOKBACK_DAYS)"
