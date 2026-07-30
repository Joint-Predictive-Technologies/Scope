#!/usr/bin/env python3
"""
WS3 — RULE_08 emits one alert per symbol, so `fed-register` becomes a real
convergence leg.

The defect: `process_document` emitted ONE alert whose `ticker` was every matched
symbol space-joined — `"$LMT $RTX $NOC"`. `SECTOR_MAP`'s smallest entry has three
symbols, so RULE_08's ticker was *always* a composite, and `normalize_ticker`
preserves multi-token strings. That value can never equal another instrument's
single symbol, so although the gate maps RULE_08 to `fed-register`, it could never
contribute a leg. All 72 historical RULE_08 rows are composite.

The test that matters is `test_fed_register_completes_a_convergence_the_composite_blocked`
— it runs the GATE's own `find_corroborated_tickers` twice on identical data, once
with the split ticker and once with the composite, and shows the split fires while
the composite does not. Everything else is scaffolding for that.

Forward-only: the historical rows are frozen detection-time records and must not be
rewritten. There is a test for that too.

Runs under pytest or standalone:  python3 tests/test_rule08_composite_split.py
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                             # noqa: E402
import rule_08_federal_register as r08                        # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "r10", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "rule_10_corroboration.py"))
r10 = importlib.util.module_from_spec(_spec)
sys.modules["r10"] = r10
_spec.loader.exec_module(r10)

# "defense" -> ["$LMT", "$RTX", "$NOC"] — the smallest sector, 3 symbols.
DEFENSE_DOC = {
    "title": "Proposed rule on defense procurement standards",
    "abstract": "Affects contractors in the defense sector.",
    "publication_date": "2026-07-20",
    "significant": True,                      # -> HIGH, so it is gate-eligible
    "agencies": [{"name": "Department of Defense"}],
    "docket_ids": ["DOD-2026-0001"],
}


def _rule08_rows() -> list[dict]:
    conn = jpt_common.db_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT ticker, severity, headline, detail FROM alerts "
        "WHERE rule='RULE_08' ORDER BY id")]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# the split
# ---------------------------------------------------------------------------

def test_a_three_symbol_sector_emits_three_single_symbol_alerts():
    conn = jpt_common.db_connection()
    emitted = r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    conn.close()

    assert emitted == 3, f"expected 3 alerts, got {emitted}"
    rows = _rule08_rows()
    assert sorted(r["ticker"] for r in rows) == ["LMT", "NOC", "RTX"]


def test_no_composite_ticker_survives_anywhere():
    conn = jpt_common.db_connection()
    r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    conn.close()

    for row in _rule08_rows():
        assert " " not in row["ticker"], f"composite ticker emitted: {row['ticker']!r}"
        assert "$" not in row["ticker"], f"unnormalised ticker: {row['ticker']!r}"


def test_each_symbol_is_matchable_without_waiting_for_enrichment():
    """The ticker must be gate-matchable at insert time, not after enrich_scores."""
    conn = jpt_common.db_connection()
    r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    conn.close()

    for row in _rule08_rows():
        assert row["ticker"] == jpt_common.normalize_ticker(row["ticker"])


def test_provenance_survives_the_split():
    """Splitting must not lose which document, or which siblings, it came from."""
    conn = jpt_common.db_connection()
    r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    conn.close()

    for row in _rule08_rows():
        assert "defense procurement" in row["detail"]
        assert "DOD-2026-0001" in row["detail"]
        assert "affects: LMT, RTX, NOC" in row["detail"]      # the sibling set
        assert row["ticker"] in row["headline"]


def test_reprocessing_the_same_document_emits_no_duplicates():
    conn = jpt_common.db_connection()
    first = r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    second = r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    third = r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    conn.close()

    assert (first, second, third) == (3, 0, 0)
    assert len(_rule08_rows()) == 3


def test_two_documents_sharing_an_80_char_title_prefix_both_emit():
    """The dedup key must be per DOCUMENT, not per title prefix.

    The headline carries only `title[:80]`, and 80-char prefixes genuinely collide
    in the Federal Register — "Energy Conservation Program: Energy Conservation
    Standards for Certain Commercial …" is a whole family of distinct rules. On the
    headline alone the first emitted and every sibling silently emitted NOTHING.
    Verified before fixing: doc A emitted 3, doc B emitted 0. The docket now forms
    part of the key via `tags`.
    """
    prefix = ("Energy Conservation Program: Energy Conservation Standards for "
              "Certain Commercial")
    a = dict(DEFENSE_DOC, title=prefix + " Refrigeration Equipment",
             abstract="oil sector", docket_ids=["DOE-2026-A"])
    b = dict(DEFENSE_DOC, title=prefix + " Heating Equipment",
             abstract="oil sector", docket_ids=["DOE-2026-B"])
    assert a["title"][:80] == b["title"][:80], "fixture must actually collide"

    conn = jpt_common.db_connection()
    first = r08.process_document(a, emit_alerts=True, conn=conn)
    second = r08.process_document(b, emit_alerts=True, conn=conn)
    conn.close()

    assert first == 3
    assert second == 3, (
        "a second document sharing an 80-char title prefix emitted nothing — "
        "the dedup key is not per-document")


def test_the_dedup_lookback_covers_the_whole_fetch_window():
    """A shorter lookback than `--since` would re-emit everything each run.

    `--since` defaults to today-14d and the job runs every 240 min, so a document
    is re-processed ~84 times inside its own fetch window. If the dedup lookback
    were shorter than that window, each re-run would re-emit N alerts per document.
    Nothing pinned this before; a 14d -> 1d mutation went unnoticed.
    """
    from datetime import date, timedelta

    import rule_08_federal_register as mod

    assert mod.DEDUP_LOOKBACK_DAYS >= 14

    # the CLI default fetch window must not exceed the dedup lookback
    since = mod.build_parser().parse_args([]).since
    window_days = (date.today() - date.fromisoformat(since)).days
    assert window_days <= mod.DEDUP_LOOKBACK_DAYS, (
        f"fetch window {window_days}d exceeds dedup lookback "
        f"{mod.DEDUP_LOOKBACK_DAYS}d — re-runs would duplicate")

    # behavioural: a document emitted N days ago is still deduped
    conn = jpt_common.db_connection()
    r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    for age in (1, 5, 10, 13):
        conn.execute(
            "UPDATE alerts SET created_at = datetime('now', ? || ' days') "
            "WHERE rule='RULE_08'", (f"-{age}",))
        conn.commit()
        assert r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn) == 0, (
            f"re-emitted after backdating {age} days")
    conn.close()


def test_two_documents_sharing_a_symbol_both_emit():
    """Per-(document, symbol) dedup, not per-symbol — LMT can appear twice."""
    other = dict(DEFENSE_DOC, title="Second defense rule on export controls",
                 docket_ids=["DOD-2026-0002"])

    conn = jpt_common.db_connection()
    a = r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    b = r08.process_document(other, emit_alerts=True, conn=conn)
    conn.close()

    assert (a, b) == (3, 3)
    lmt = [r for r in _rule08_rows() if r["ticker"] == "LMT"]
    assert len(lmt) == 2, "two different documents affecting LMT should both emit"
    assert lmt[0]["headline"] != lmt[1]["headline"]


def test_a_multi_sector_document_emits_the_union_once_each():
    """A document matching two sectors must not duplicate a shared symbol."""
    doc = dict(DEFENSE_DOC,
               title="Rule affecting defense and tech procurement",
               abstract="Covers defense contractors and tech platforms.")
    conn = jpt_common.db_connection()
    emitted = r08.process_document(doc, emit_alerts=True, conn=conn)
    conn.close()

    tickers = [r["ticker"] for r in _rule08_rows()]
    assert len(tickers) == len(set(tickers)), f"duplicate symbols: {tickers}"
    assert emitted == len(tickers)
    assert {"LMT", "RTX", "NOC"} <= set(tickers)
    assert {"GOOGL", "META", "AMZN", "AAPL", "MSFT"} <= set(tickers)


def test_a_document_matching_nothing_emits_nothing():
    conn = jpt_common.db_connection()
    emitted = r08.process_document(
        {"title": "Rule about migratory bird habitats", "abstract": "",
         "publication_date": "2026-07-20"},
        emit_alerts=True, conn=conn)
    conn.close()
    assert emitted == 0
    assert _rule08_rows() == []


def test_dry_run_emits_nothing():
    conn = jpt_common.db_connection()
    emitted = r08.process_document(DEFENSE_DOC, emit_alerts=False, conn=conn)
    conn.close()
    assert emitted == 0
    assert _rule08_rows() == []


# ---------------------------------------------------------------------------
# THE PAYOFF — fed-register can now complete a convergence
# ---------------------------------------------------------------------------

def _seed_other_legs(ticker: str) -> None:
    """An insider leg and a congressional leg, both in-window and gate-eligible.

    ⚠️ The insider leg carries an explicit GENUINE-BUY verdict. Since 2026-07-30 the gate
    also asks per alert whether a leg corroborates, and NULL fails closed — so without it
    these two "other legs" would be one, and this file's whole argument (that the SPLIT
    ticker is what completes the convergence) would be untestable for an unrelated reason.
    """
    conn = jpt_common.db_connection()
    for rule, age in (("RULE_06", "-2 days"), ("RULE_01B", "-3 days")):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at,
                                   corroborates)
               VALUES (?, ?, 'HIGH', ?, datetime('now', ?), ?)""",
            (rule, ticker, f"{rule} on {ticker}", age,
             1 if rule.upper() in jpt_common.SIGNED_RULES else None),
        )
    conn.commit()
    conn.close()


def _gate_instruments(ticker: str) -> list[str]:
    conn = jpt_common.db_connection()
    try:
        rows = r10._candidate_alerts(conn, r10.CONVERGENCE_WINDOW_DAYS * 24)
        return r10.instruments_for([r for r in rows if r["ticker"] == ticker])
    finally:
        conn.close()


def _gate_fires(ticker: str) -> bool:
    conn = jpt_common.db_connection()
    try:
        clusters = r10.find_corroborated_tickers(
            conn, r10.CONVERGENCE_WINDOW_DAYS * 24)
        return ticker in clusters
    finally:
        conn.close()


def test_fed_register_completes_a_convergence_the_composite_blocked():
    """THE test. Same data, two ticker shapes, opposite outcomes.

    Uses the gate's real `find_corroborated_tickers` / `instruments_for` — not a
    reimplementation — so this is the actual firing decision.
    """
    # --- SPLIT form: RULE_08 emits a single-symbol LMT alert ---
    conn = jpt_common.db_connection()
    r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    conn.close()
    _seed_other_legs("LMT")

    instruments = _gate_instruments("LMT")
    assert instruments == ["congressional", "fed-register", "insider"], instruments
    assert len(instruments) >= r10.MIN_DISTINCT_INSTRUMENTS
    assert _gate_fires("LMT"), "3 instruments including fed-register should fire"


def test_the_old_composite_form_does_NOT_complete_the_same_convergence():
    """The control. Identical legs, composite ticker — only 2 instruments."""
    conn = jpt_common.db_connection()
    # exactly what the OLD code wrote: space-joined, normalised by enrich later
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, created_at)
           VALUES ('RULE_08', 'LMT RTX NOC', 'HIGH',
                   'Federal Register: defense rule — affects $LMT $RTX $NOC',
                   datetime('now','-1 days'))"""
    )
    conn.commit()
    conn.close()
    _seed_other_legs("LMT")

    instruments = _gate_instruments("LMT")
    assert "fed-register" not in instruments, (
        "the composite should not have contributed a fed-register leg")
    assert instruments == ["congressional", "insider"], instruments
    assert len(instruments) < r10.MIN_DISTINCT_INSTRUMENTS
    assert not _gate_fires("LMT"), "the composite must not complete a convergence"


def test_the_composite_ticker_matches_no_single_symbol():
    """Why it could never contribute: the string is not a symbol."""
    assert jpt_common.normalize_ticker("$LMT $RTX $NOC") == "LMT RTX NOC"
    assert jpt_common.normalize_ticker("$LMT $RTX $NOC") != "LMT"


def test_a_medium_severity_fed_register_alert_still_cannot_contribute():
    """Honest limit: the split helps only `significant` documents.

    The gate requires HIGH/CRITICAL. 63 of the 72 historical RULE_08 rows are
    MEDIUM, so splitting alone does not make every Federal Register document a
    convergence leg — only the significant ones.
    """
    conn = jpt_common.db_connection()
    r08.process_document(dict(DEFENSE_DOC, significant=False),
                         emit_alerts=True, conn=conn)
    conn.close()
    assert all(r["severity"] == "MEDIUM" for r in _rule08_rows())

    _seed_other_legs("LMT")
    assert _gate_instruments("LMT") == ["congressional", "insider"]
    assert not _gate_fires("LMT")


# ---------------------------------------------------------------------------
# immutability — forward only, no backfill
# ---------------------------------------------------------------------------

def test_historical_composite_rows_are_not_rewritten():
    """Detection-time records are frozen; they age out on their own."""
    conn = jpt_common.db_connection()
    for i in range(3):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at)
               VALUES ('RULE_08', 'LMT RTX NOC', 'HIGH', ?, datetime('now','-30 days'))""",
            (f"historical composite {i}",),
        )
    conn.commit()
    before = [dict(r) for r in conn.execute(
        "SELECT id, ticker, severity, headline, created_at FROM alerts "
        "WHERE rule='RULE_08' ORDER BY id")]
    conn.close()

    conn = jpt_common.db_connection()
    r08.process_document(DEFENSE_DOC, emit_alerts=True, conn=conn)
    conn.close()

    conn = jpt_common.db_connection()
    after = [dict(r) for r in conn.execute(
        "SELECT id, ticker, severity, headline, created_at FROM alerts "
        "WHERE rule='RULE_08' AND id <= ? ORDER BY id", (before[-1]["id"],))]
    conn.close()

    assert after == before, "a historical RULE_08 row was rewritten"
    assert all(r["ticker"] == "LMT RTX NOC" for r in after)


def test_the_rule_contains_no_update_or_backfill_of_alerts():
    """A split that rewrote history would be a migration, not an emission change."""
    import inspect

    source = inspect.getsource(r08).lower()
    for statement in ("update alerts", "delete from alerts", "alter table"):
        assert statement not in source, f"RULE_08 performs {statement!r}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
