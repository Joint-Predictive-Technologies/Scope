"""OSINT: the emission is retired, and the globe stops inventing places.

The audit (`vault/Scope/02_Sessions/SESSION-2026-07-29-osint-audit.md`) established
what this rule actually did: `news → region → CAMEO category → a hardcoded ticker
basket → ticker = tickers[0]`. Nothing ever asked which companies were in an event —
the article text is never read. Measured: **387 alerts → 8 distinct tickers, top 3 =
75.7%**.

Three separate defects are pinned here, and they fail for three different reasons:

  1. the emission itself — a basket ticker must not reach the alerts table;
  2. the globe fabricating hotspots when it has none — invented data rendering as
     real, on a product whose stated principle is that its zeros are honest;
  3. the globe GUESSING a location from a ticker basket — the same defect running
     backwards, a wrong-location generator on a surface whose whole claim is that
     position means something.

⚠️ `api.main` is imported directly and its endpoint FUNCTIONS are called. No
`TestClient` is constructed anywhere in this file, deliberately: APScheduler starts
inside the FastAPI `lifespan` (`api/main.py:296-310`), and a previous session caught
it executing a real rule against a live database during testing.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.main as m  # noqa: E402
from jpt_common import RULE_10_EXCLUDED, db_connection, rule10_instruments  # noqa: E402
from scripts import rule_osint as osint  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scheduler():
    """The scheduler must not be running for any test in this file."""
    assert m._scheduler is None, "APScheduler is live — a test could trigger a real rule run"
    yield


def _seed(conn, tags: dict | None, ticker: str = "LMT", severity: str = "HIGH"):
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, tags, created_at) "
        "VALUES ('RULE_OSINT', ?, ?, 'h', ?, datetime('now'))",
        (ticker, severity, json.dumps(tags) if tags is not None else None))
    conn.commit()


# ── 1. the emission ─────────────────────────────────────────────────────────

def test_the_BASKET_emission_is_retired_and_writes_no_alert(monkeypatch):
    """The whole point. A ticker chosen by `EVENT_TICKER_MAP[(region, category)][0]`
    is an artefact of the lookup table, so it must not reach the alerts table at all.

    The GDELT fetch is stubbed with one event that WOULD have emitted — Russia +
    military action, whose basket leads with LMT — so this fails if the emission
    returns, not merely if the network is quiet.
    """
    event = {"event_id": "TEST-1", "cameo": "190", "goldstein": -9.0,
             "num_mentions": 40, "avg_tone": -8.0, "country": "RS",
             "geo_lat": 55.7, "geo_lng": 37.6, "source_url": "http://example.invalid/a"}
    monkeypatch.setattr(osint, "_fetch_gdelt_events", lambda: [event])

    assert osint.get_tickers_for_event("Russia", "190")[0] == "LMT", \
        "the fixture no longer exercises the basket path it is meant to test"

    conn = db_connection()
    before = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    scanned, flagged, emitted = osint._run_gdelt(conn, emit=True, dry_run=False)
    after = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    seen = conn.execute("SELECT COUNT(*) FROM gdelt_events").fetchone()[0]
    conn.close()

    assert flagged == 1, "the event did not reach the emission decision"
    assert emitted == 0 and after == before, "a basket-derived alert was written"
    assert seen == 1, "the dedupe stopped working — the ingest must still run"


def test_the_INGEST_and_its_coordinates_survive_the_retirement():
    """The salvage. Retiring the emission must not cost the globe its spine: the
    keyless GDELT feed, the hostile filter, and the ActionGeo coordinates — which the
    audit measured on **100% of 387 alerts** — all stay.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "scripts", "rule_osint.py")).read()
    for name in ("_fetch_gdelt_events", "_filter_hostile", "geo_lat", "geo_lng",
                 "COUNTRY_REGION_MAP"):
        assert name in src, f"the globe salvage lost {name}"
    # ...and behaviourally: the parser still yields coordinates, not just the names.
    assert osint._filter_hostile([
        {"event_id": "x", "cameo": "190", "goldstein": -9.0, "num_mentions": 40,
         "avg_tone": -8.0, "country": "RS", "geo_lat": 55.7, "geo_lng": 37.6,
         "source_url": ""}])[0]["geo_lat"] == 55.7


def test_OSINT_did_not_become_a_gate_leg_on_the_way_out():
    assert "RULE_OSINT" in RULE_10_EXCLUDED
    assert rule10_instruments(["RULE_OSINT"]) == []


# ── 2. the globe never invents a place ──────────────────────────────────────

def test_an_EMPTY_globe_is_the_honest_answer_not_twelve_invented_hotspots():
    """`/api/osint-hotspots` used to answer "none" with 12 fabricated regions —
    invented severities, counts and ticker lists — flagged `demo: True`, a flag
    `osint.html` never read. `[]` is a true statement; the fallback was not."""
    conn = db_connection()
    conn.execute("DELETE FROM alerts")
    conn.commit()
    conn.close()
    assert m.osint_hotspots() == []


def test_no_fabricated_hotspot_data_remains_in_the_source():
    """A lint beside the behavioural test above, because the failure mode is a
    fixture quietly reappearing as a 'the globe must never be blank' convenience."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "api", "main.py")).read()
    assert "_DEMO_HOTSPOTS = [" not in src, "the invented hotspot fixture is back"


def test_a_REAL_located_event_still_reaches_the_globe():
    """The control. Without it, an endpoint that returns `[]` unconditionally passes
    every test above."""
    conn = db_connection()
    conn.execute("DELETE FROM alerts")
    _seed(conn, {"region": "Taiwan Strait", "lat": 23.9, "lng": 121.1}, "TSM")
    conn.close()
    out = m.osint_hotspots()
    assert len(out) == 1 and out[0]["region"] == "Taiwan Strait"
    assert (out[0]["lat"], out[0]["lng"]) == (23.9, 121.1), \
        "the event's own coordinates were replaced by a region centroid"


# ── 3. a location is never guessed from a basket ────────────────────────────

@pytest.mark.parametrize("tags,ticker,why", [
    (None,                    "LMT", "no tags at all"),
    ({},                      "USO", "tags with no region"),
    ({"region": ""},          "XOM", "an empty region"),
    ({"goldstein": -7.0},     "LMT", "a ticker that appears in SIX region baskets"),
])
def test_an_unlocatable_event_is_DROPPED_not_placed_by_its_ticker(tags, ticker, why):
    """Two inferences lived in this endpoint and both are gone: a region deduced from
    which `REGION_TICKERS` basket the symbol appears in, and a hard default of
    "Middle East" that placed any unlocatable alert in a war zone.

    A confidently mislocated event is worse than an absent one on a surface whose
    entire claim is that position means something.
    """
    conn = db_connection()
    conn.execute("DELETE FROM alerts")
    _seed(conn, tags, ticker)
    conn.close()
    assert m.osint_hotspots() == [], f"a location was invented for: {why}"


def test_the_endpoint_no_longer_reads_the_ticker_basket_at_all():
    """⚠️ COMMENT LINES ARE STRIPPED FIRST. The paragraph explaining WHY the default
    was removed necessarily names it, so a naive grep finds the explanation and fails —
    a lint that trips over its own documentation. Only executable lines are checked."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "api", "main.py")).read()
    start = src.index("def osint_hotspots(")
    body = src[start:src.index("@app.get", start)]
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "REGION_TICKERS" not in code, "the globe endpoint still consults a ticker basket"
    assert '"Middle East"' not in code, "the Middle East default is back"
