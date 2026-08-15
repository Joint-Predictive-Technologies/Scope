#!/usr/bin/env python3
"""POSITION SIZING IS CONTEXT, AND CONTEXT MUST NOT LEAK INTO THE SCORE.

Two properties carry this file, in this order of importance:

  1. `test_the_panel_never_writes_a_cache_the_gate_reads` — the isolation property.
     `jpt_common.contract_leg_weight` weights a contracts leg ONLY when a `ticker_meta` row
     already exists for the ticker, and refuses to resolve a cold one so the scheduler
     cannot be made to fire dozens of live lookups. A display panel that warmed
     `ticker_meta` as a side effect of somebody opening a page would therefore decide which
     contracts legs get weighted — a scoring change delivered by a UI feature, and
     invisible in any diff of the scoring code. The panel's cache is a separate table and
     it must stay that way.

  2. `test_a_stale_concept_never_outranks_a_fresh_one` — the honesty property, and a real
     shipped bug. Boeing's `RevenueFromContractWithCustomerExcludingAssessedTax` holds
     three facts, the newest ending 2019-12-31, while its `Revenues` runs to the current
     quarter. The first implementation returned on the first concept that had any rows, so
     BA's "TTM revenue" resolved to $76.6B of 2019 revenue, correctly labelled, sitting
     beside a live market cap — and every contract's "% of revenue" was computed against
     it.

Fixtures inject facts rather than fetching them: this is a test of the arithmetic and the
period logic, not of SEC's uptime. The shapes below are real, copied from companyfacts on
2026-08-15 and cited per fixture.
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                                       # noqa: E402
from jpt_common import db_connection                                    # noqa: E402
from scripts.position_sizing import (_dedupe_periods, _four_quarters,   # noqa: E402
                                     _fy_plus_ytd, _ttm_revenue, _days,
                                     cash_runway_months, monthly_burn_rate, resolve)

REV = ("us-gaap", "Revenues")
REV606 = ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax")


def _f(start, end, val, filed="2026-08-01"):
    return {"start": start, "end": end, "val": val, "filed": filed}


# ── the isolation property ────────────────────────────────────────────────────

def test_the_panel_never_writes_a_cache_the_gate_reads(monkeypatch):
    """Resolving a ticker must leave `ticker_meta` and `issuer_cap` untouched.

    Both are read by the moat: `contract_leg_weight` requires a live `ticker_meta` row
    before it will weight a contracts leg at all, and `issuer_cap` backs the cluster
    surface's cap gate. If this test fails, opening a ticker page has become able to change
    a corroboration's `evidence_confidence`.
    """
    import scripts.position_sizing as ps

    monkeypatch.setattr(ps, "_cik_for", lambda s: "0000012927")
    monkeypatch.setattr(ps, "_market_cap_core",
                        lambda *a, **k: (a[4](1_000_000_000), 1_000_000_000)[1])
    monkeypatch.setattr(ps, "_shares_outstanding", lambda c: (10_000_000, "2026-06-30"))
    monkeypatch.setattr(ps, "_last_close", lambda s: 100.0)
    monkeypatch.setattr(ps, "_companyfacts_units", lambda c: {})

    conn = db_connection()
    resolve(conn, "BA")

    assert conn.execute("SELECT COUNT(*) FROM ticker_meta").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM issuer_cap").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM position_sizing_cache").fetchone()[0] == 1
    conn.close()


def test_no_rule_or_gate_module_reads_the_panel_cache():
    """The isolation property, stated over the SOURCE rather than one code path.

    The runtime test above proves the panel does not WRITE a moat cache. This proves the
    converse direction — that nothing in the detection path READS the panel's table — and
    it is deliberately a grep, because the failure it guards against is a future edit
    somewhere in the scoring engine, not a call the current tests happen to exercise.
    """
    root = os.path.join(os.path.dirname(__file__), "..")
    watched = [
        os.path.join(root, "jpt_common.py"),
        os.path.join(root, "scripts", "rule_10_corroboration.py"),
        os.path.join(root, "scripts", "enrich_scores.py"),
        os.path.join(root, "scripts", "rule_cluster.py"),
        os.path.join(root, "scripts", "rule_11_contracts.py"),
        os.path.join(root, "scripts", "insider_clusters.py"),
    ]
    # READS specifically, not every mention: `jpt_common` legitimately CREATEs the table in
    # migration m015, and asserting on the bare name would fail on the schema definition
    # itself. What must never appear in the detection path is a query against it.
    reads = re.compile(r"(FROM|JOIN|UPDATE|INTO)\s+position_sizing_cache", re.I)
    offenders = [os.path.basename(p) for p in watched if os.path.exists(p)
                 and reads.search(open(p, encoding="utf-8").read())]
    assert not offenders, (
        f"{offenders} query position_sizing_cache. The panel is display-only context; "
        "wiring it into detection or scoring is a moat change and needs its own pass.")


# ── a transport failure is not a verdict ──────────────────────────────────────

def _stub_resolver(monkeypatch, *, cap_works=True, facts_works=True):
    import scripts.position_sizing as ps

    monkeypatch.setattr(ps, "_cik_for", lambda s: "0000012927")
    monkeypatch.setattr(ps, "_shares_outstanding", lambda c: (10_000_000, "2026-06-30"))
    monkeypatch.setattr(ps, "_last_close", lambda s: 100.0)
    if cap_works:
        monkeypatch.setattr(ps, "_market_cap_core",
                            lambda *a, **k: (a[4](1_000_000_000), 1_000_000_000)[1])
    else:
        # The core's signature for "could not ask": returns None and never calls the
        # write hook. Distinct from "asked, and cannot price this", which writes None.
        monkeypatch.setattr(ps, "_market_cap_core", lambda *a, **k: None)
    monkeypatch.setattr(ps, "_companyfacts_units", lambda c: {
        ("us-gaap", "Revenues"): {"USD": [_f("2025-01-01", "2025-12-31", 500)]},
    } if facts_works else {})
    return ps


def test_a_companyfacts_outage_keeps_the_previous_revenue_and_retries(monkeypatch):
    """Writing NULLs on a 429 would publish "Revenue unavailable" on Boeing for 24 hours."""
    _stub_resolver(monkeypatch)
    conn = db_connection()
    first = resolve(conn, "BA")
    assert first["ttm_revenue"] == 500 and first["fetched_at"]

    _stub_resolver(monkeypatch, facts_works=False)
    second = resolve(conn, "BA", force=True)
    conn.close()

    assert second["ttm_revenue"] == 500, "a transport failure erased a known fact"
    assert second["fetched_at"] is None, "a non-answer must not be cached for a full TTL"


def test_the_two_legs_degrade_independently(monkeypatch):
    """A Yahoo outage must not blank a revenue figure fetched successfully in the same pass.

    The legs fail independently in reality — SEC's XBRL endpoints and Yahoo's chart
    endpoint are different hosts — so retention is applied per column group, not per row.
    """
    _stub_resolver(monkeypatch)
    conn = db_connection()
    resolve(conn, "BA")

    _stub_resolver(monkeypatch, cap_works=False)
    row = resolve(conn, "BA", force=True)
    conn.close()

    assert row["market_cap"] == 1_000_000_000, "the cap leg lost its previous answer"
    assert row["ttm_revenue"] == 500, "the healthy facts leg was collateral damage"


def test_a_resolved_unknown_is_recorded_rather_than_retried_forever(monkeypatch):
    """"Asked SEC and Yahoo, genuinely cannot price this" is an ANSWER with its own TTL.

    It must be distinguishable from the outage above: the core CALLS the write hook with
    None, so the row caches a NULL cap and `fetched_at` is stamped.
    """
    ps = _stub_resolver(monkeypatch)
    monkeypatch.setattr(ps, "_market_cap_core", lambda *a, **k: (a[4](None), None)[1])
    conn = db_connection()
    row = resolve(conn, "SPCX")
    conn.close()

    assert row["market_cap"] is None
    assert row["fetched_at"] is not None
    assert row["shares_outstanding"] is None, (
        "components of a cap the guards refused must not be published — a reader can "
        "multiply them by hand and reach the mis-scale the guards exist to prevent")


# ── the honesty property: revenue freshness ───────────────────────────────────

def test_a_stale_concept_never_outranks_a_fresh_one():
    """THE BOEING BUG. Real shapes: BA's 606 tag ends 2019, `Revenues` is current."""
    facts = {
        REV606: {"USD": [_f("2019-01-01", "2019-12-31", 76_559_000_000)]},
        REV: {"USD": [
            _f("2025-01-01", "2025-12-31", 89_463_000_000),
            _f("2026-01-01", "2026-06-30", 46_777_000_000),
            _f("2025-01-01", "2025-06-30", 42_245_000_000),
        ]},
    }
    value, as_of, basis = _ttm_revenue(facts)
    # 89,463 + 46,777 - 42,245 = 93,995
    assert value == 93_995_000_000
    assert as_of == "2026-06-30"
    assert basis == "TTM-FY+YTD"


def test_preference_order_still_breaks_ties_at_equal_freshness():
    """Concept preference is a TIEBREAK, not an override — both directions matter."""
    rows = {"USD": [_f("2025-01-01", "2025-12-31", 100)]}
    value, _, _ = _ttm_revenue({REV606: rows, REV: {"USD": [_f("2025-01-01",
                                                              "2025-12-31", 999)]}})
    assert value == 100, "the higher-preference concept must win an exact freshness tie"


# ── the honesty property: period arithmetic ───────────────────────────────────

def test_overlapping_quarters_are_never_summed():
    """XBRL publishes Q2 as BOTH three months and six months to June, same concept.

    Summing the newest four rows by `end` double-counts and overstates by ~50%, and the
    result is a plausible number of a plausible magnitude — it fails silently.
    """
    facts = {REV: {"USD": [
        _f("2026-04-01", "2026-06-30", 100),     # Q2, three months
        _f("2026-01-01", "2026-06-30", 180),     # H1, SAME end — must not be added to Q2
        _f("2026-01-01", "2026-03-31", 80),
        _f("2025-10-01", "2025-12-31", 70),
        _f("2025-07-01", "2025-09-30", 60),
    ]}}
    assert _four_quarters(_dedupe_periods(facts[REV]["USD"])) == (310, "2026-06-30")


def test_a_us_filers_missing_q4_can_never_become_a_fifteen_month_year():
    """Nobody reports Q4 as a quarter — the 10-K publishes the full year instead.

    So four "consecutive" quarterly facts run Q2/Q1/Q3/Q2 and span fifteen months. The span
    check is what makes that a refusal rather than a 25% overstatement.
    """
    facts = {REV: {"USD": [
        _f("2026-04-01", "2026-06-30", 83_772_000),
        _f("2026-01-01", "2026-03-31", 50_122_000),
        _f("2025-07-01", "2025-09-30", 10_098_310),
        _f("2025-04-01", "2025-06-30", 6_273_000),
    ]}}
    assert _four_quarters(_dedupe_periods(facts[REV]["USD"])) is None


def test_fy_plus_ytd_requires_the_START_dates_to_line_up_exactly():
    """The two starts are STRUCTURAL and are never approximate — only the prior-year END
    gets slack, and only because 52/53-week fiscal calendars require it.

    ONDS's calendar-year shape: FY2025 + 1H2026 - 1H2025.
    """
    fy = _f("2025-01-01", "2025-12-31", 50_731_000)
    periods = [fy, _f("2026-01-01", "2026-06-30", 133_894_000),
               _f("2025-01-01", "2025-06-30", 10_522_000)]
    assert _fy_plus_ytd(periods, fy) == (174_103_000, "2026-06-30")

    # Interim does not start the day after the fiscal year ends — it is not the interim.
    assert _fy_plus_ytd([fy, _f("2026-01-02", "2026-06-30", 133_894_000),
                         _f("2025-01-01", "2025-06-30", 10_522_000)], fy) is None

    # Prior-year comparable does not share the fiscal year's start — different basis.
    assert _fy_plus_ytd([fy, _f("2026-01-01", "2026-06-30", 133_894_000),
                         _f("2025-01-02", "2025-06-30", 10_522_000)], fy) is None


def test_a_52_53_week_filers_prior_year_period_is_matched_within_a_week():
    """QCOM's shape, verbatim from companyfacts. A 52/53-week year ends on a WEEKDAY.

    Demanding the exact calendar anniversary made every such filer fall back to a year-end
    figure up to eleven months stale — 10 of 34 sampled tickers, most of the semiconductor
    and defence names this panel exists for.
    """
    fy = _f("2024-09-30", "2025-09-28", 44_284_000_000)
    periods = [fy,
               _f("2025-09-29", "2026-06-28", 32_798_000_000),
               _f("2024-09-30", "2025-06-29", 33_013_000_000)]   # anniversary + 1 day
    assert _fy_plus_ytd(periods, fy) == (44_069_000_000, "2026-06-28")


def test_the_slack_is_bounded_by_the_RESULT_not_by_the_calendar():
    """The three legs telescope to `prior_end+1 .. cur_end`, and THAT is range-checked.

    So a prior-year row far enough off to produce a fourteen-month window is refused even
    though its start date lines up — the tolerance widens which rows are considered, it
    does not widen what counts as a year.
    """
    fy = _f("2024-09-30", "2025-09-28", 44_284_000_000)
    # Prior-year comparable eight days off — outside the window, so no candidate at all.
    assert _fy_plus_ytd([fy, _f("2025-09-29", "2026-06-28", 32_798_000_000),
                         _f("2024-09-30", "2025-07-06", 33_013_000_000)], fy) is None
    # And a candidate inside the date window whose telescoped span is not a year is still
    # rejected by the span check.
    assert _fy_plus_ytd([fy, _f("2025-09-29", "2027-06-28", 32_798_000_000),
                         _f("2024-09-30", "2025-06-29", 33_013_000_000)], fy) is None


def test_restatements_resolve_to_the_newest_filing():
    """ONDS's 2025 H1 is 10,521,570 as first filed and 10,522,000 as re-filed in 2026."""
    rows = [_f("2025-01-01", "2025-06-30", 10_521_570, filed="2025-08-12"),
            _f("2025-01-01", "2025-06-30", 10_522_000, filed="2026-08-13")]
    assert [r["val"] for r in _dedupe_periods(rows)] == [10_522_000]


# ── cash runway ───────────────────────────────────────────────────────────────

def test_a_duration_fact_is_inclusive_of_both_endpoints():
    """2026-01-01..2026-06-30 is a 181-day half-year. Bare subtraction says 180.

    Immaterial to the FY/quarter bands, which have tens of days of slack; NOT immaterial to
    the burn rate, which divides by it.
    """
    assert _days({"start": "2026-01-01", "end": "2026-06-30"}) == 181


def test_the_burn_rate_uses_the_period_actually_reported():
    """Operating cash flow is YTD, so a Q2 figure covers six months, not three.

    Dividing by three because "it came from a 10-Q" makes the runway wrong by 2x. Insperity:
    $619M cash against -$19M over 2026 H1.
    """
    burn = monthly_burn_rate(-19_000_000, "2026-01-01", "2026-06-30")
    assert burn == pytest.approx(19_000_000 / (181 / 30.44))
    assert cash_runway_months(619_000_000, -19_000_000,
                              "2026-01-01", "2026-06-30") == pytest.approx(193.7, abs=0.1)


def test_a_cash_generative_company_has_no_runway_rather_than_a_huge_one():
    """`None` here means "the question does not apply", and the API labels it `not_burning`.

    Printing a very large number of months for a profitable company reads as a forecast.
    """
    assert cash_runway_months(7_239_000_000, 1_185_000_000,
                              "2026-01-01", "2026-06-30") is None
    assert monthly_burn_rate(1_185_000_000, "2026-01-01", "2026-06-30") is None


# ── the events table ──────────────────────────────────────────────────────────

def _seed_alert(conn, **kw):
    cols = ", ".join(kw)
    conn.execute(f"INSERT INTO alerts ({cols}) VALUES ({', '.join('?' * len(kw))})",
                 tuple(kw.values()))
    conn.commit()


def test_the_events_table_matches_the_ticker_exactly_not_loosely():
    """The alert LIST above matches `ticker LIKE '%sym%'`, which also matches BABA for BA.

    A loose match is survivable in a list a human reads and is not survivable in an
    arithmetic claim about THIS company's market cap.
    """
    from api.routers.tickers import _dollar_events

    conn = db_connection()
    conn.execute("INSERT INTO contracts (recipient_name, ticker, amount, award_id) "
                 "VALUES ('THE BOEING COMPANY','BA',1_000_000_000,'AWD1')")
    _seed_alert(conn, rule="RULE_11", ticker="BA", headline="Boeing award",
                severity="MEDIUM", award_key="AWD1")
    _seed_alert(conn, rule="RULE_11", ticker="BABA", headline="Alibaba award",
                severity="MEDIUM", award_key="AWD1")

    events = _dollar_events(conn, "BA", 10_000_000_000, 5_000_000_000)
    conn.close()

    assert len(events) == 1
    assert events[0]["headline"] == "Boeing award"
    assert events[0]["pct_of_market_cap"] == 10.0
    assert events[0]["pct_of_ttm_revenue"] == 20.0


def test_an_unknown_cap_yields_a_null_percentage_and_never_a_zero():
    """`None` is "no cap to compare against"; `0.0` would read as "immaterial"."""
    from api.routers.tickers import _dollar_events

    conn = db_connection()
    conn.execute("INSERT INTO contracts (recipient_name, ticker, amount, award_id) "
                 "VALUES ('X','ZZZZ',5_000_000,'AWD9')")
    _seed_alert(conn, rule="RULE_11", ticker="ZZZZ", headline="award",
                severity="MEDIUM", award_key="AWD9")
    events = _dollar_events(conn, "ZZZZ", None, None)
    conn.close()

    assert events[0]["pct_of_market_cap"] is None
    assert events[0]["pct_of_ttm_revenue"] is None


def test_a_malformed_tags_string_skips_one_event_rather_than_the_whole_panel():
    """`tags` is free TEXT and several rules fill it with a positional comma string."""
    from api.routers.tickers import _dollar_events

    conn = db_connection()
    _seed_alert(conn, rule="RULE_16", ticker="NSP", headline="whale", severity="MEDIUM",
                tags="MARKEL GROUP INC.,NEW,2026-06-30")
    _seed_alert(conn, rule="RULE_16", ticker="NSP", headline="good whale",
                severity="MEDIUM", tags=json.dumps({"value_usd": 7_490_989.0}))
    events = _dollar_events(conn, "NSP", 1_993_793_123, None)
    conn.close()

    assert [e["headline"] for e in events] == ["good whale"]
    assert events[0]["pct_of_market_cap"] == pytest.approx(0.3757, abs=1e-4)


# ── the page still parses ─────────────────────────────────────────────────────

def test_the_ticker_pages_inline_script_still_parses():
    """A SYNTAX ERROR HERE TAKES DOWN THE WHOLE PAGE, NOT JUST THE PANEL.

    Everything on `ticker.html` lives in one inline `<script>`, so one bad character stops
    `load()` from ever running and the page renders its "Loading…" placeholder forever —
    no alerts, no timeline, no graph, nothing. This is not hypothetical: an HTML comment
    containing backticks, written inside the per-row template literal of `events.map`,
    closed that literal early and produced exactly that. The browser said
    "missing ) after argument list"; the entire Python suite stayed green.

    ⚠️ WHAT IT DOES NOT CATCH, stated so nobody over-trusts it. This is a PARSE check, not
    a correctness check — it cannot see a wrong selector or a mis-formatted number. And an
    odd backtick in plain JS (outside any template literal) frequently still parses,
    because the file has many backticks and they simply re-pair into a different, valid
    grouping. It reliably catches the defect above, which is the one that has actually
    happened here; it is not a general proof that the page is sound.

    Checked with `node --check`, skipped rather than failed where node is absent, so this
    never becomes a reason CI cannot run.
    """
    import re as _re
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available — cannot syntax-check the page's inline script")

    page = os.path.join(os.path.dirname(__file__), "..", "api", "static", "ticker.html")
    html = open(page, encoding="utf-8").read()
    blocks = _re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, _re.S)
    assert blocks, "found no inline script in ticker.html — this test is not testing anything"

    for i, block in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(block)
            path = fh.name
        try:
            proc = subprocess.run([node, "--check", path], capture_output=True, text=True)
        finally:
            os.unlink(path)
        assert proc.returncode == 0, (
            f"inline script block {i} of ticker.html does not parse — the ENTIRE page is "
            f"dead, not just the position-sizing panel:\n{proc.stderr[:800]}")


def test_a_reported_zero_is_an_ANSWER_and_is_never_relabelled_unavailable(monkeypatch):
    """A pre-revenue biotech reporting $0 ANSWERED. The server used truthiness and said
    "No annual or interim revenue facts in SEC XBRL data for this filer" — a sentence that
    is simply false about a filer who reported a number.

    No value was fabricated (the cell read "Unavailable"), so this erred safe; but the
    reason string is the entire point of an honest empty state, and it was a lie.
    """
    from api.routers.tickers import _position_sizing_payload
    import scripts.position_sizing as ps

    monkeypatch.setattr(ps, "resolve", lambda conn, sym: {
        "market_cap": 1_000_000, "cap_updated": "2026-08-15T00:00:00+00:00",
        "shares_outstanding": 100_000, "shares_as_of": "2026-06-30", "last_close": 10.0,
        "public_float_usd": 0.0, "public_float_as_of": "2025-06-30",
        "ttm_revenue": 0.0, "ttm_revenue_as_of": "2026-06-30",
        "ttm_revenue_basis": "TTM-FY+YTD",
        "cash_usd": 0.0, "cash_as_of": "2026-06-30",
        "operating_cash_flow": None, "ocf_period_start": None, "ocf_period_end": None,
        "fetched_at": "2026-08-15T00:00:00+00:00",
    })

    conn = db_connection()
    payload = _position_sizing_payload(conn, "ZERO")
    conn.close()

    for field in ("ttm_revenue", "public_float_usd", "cash"):
        assert payload[field]["status"] == "known", f"{field}: a reported 0 became unknown"
        assert payload[field]["value"] == 0.0
        assert payload[field]["reason"] is None, (
            f"{field}: a false reason string was attached to a real answer")


def test_a_derived_figure_is_dated_resolved_at_and_never_as_of(monkeypatch):
    """`as_of` is the date of a FILED FACT. A market cap has no such date — it is a product
    computed now from a share count and whatever close Yahoo last served. Labelling the
    resolve timestamp `as_of` claimed a precision that does not exist.
    """
    from api.routers.tickers import _position_sizing_payload
    import scripts.position_sizing as ps

    monkeypatch.setattr(ps, "resolve", lambda conn, sym: {
        "market_cap": 1_000_000, "cap_updated": "2026-08-15T12:00:00+00:00",
        "shares_outstanding": 100_000, "shares_as_of": "2026-06-30", "last_close": 10.0,
        "fetched_at": "2026-08-15T12:00:00+00:00",
    })
    conn = db_connection()
    payload = _position_sizing_payload(conn, "X")
    conn.close()

    for derived in ("market_cap", "last_close"):
        assert payload[derived]["resolved_at"], f"{derived} lost its resolve timestamp"
        assert "as_of" not in payload[derived], (
            f"{derived} claims a filed-fact date it does not have")
    # A genuinely filed fact keeps `as_of`, and must NOT acquire `resolved_at`.
    assert payload["shares_outstanding"]["as_of"] == "2026-06-30"
    assert "resolved_at" not in payload["shares_outstanding"]


# ── the migration ─────────────────────────────────────────────────────────────

def test_the_migration_is_registered_and_additive():
    conn = db_connection()
    assert conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m015_position_sizing_cache'"
    ).fetchone()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(position_sizing_cache)")}
    assert {"symbol", "market_cap", "shares_outstanding", "ttm_revenue",
            "ttm_revenue_basis", "cash_usd", "operating_cash_flow"} <= cols
    # No dilution column: an always-NULL column is an invitation to fill it with a guess.
    assert not [c for c in cols if "dilut" in c or "warrant" in c]
    conn.close()
