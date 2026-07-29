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


def test_the_ACTIONGEO_COLUMNS_still_parse_the_globes_coordinates():
    """THE SALVAGE, EXERCISED — not grepped.

    ⚠️ THE FIRST VERSION OF THIS TEST COULD NOT FAIL. It searched `rule_osint.py` for the
    strings `geo_lat`/`geo_lng` and then passed a ready-made dict through
    `_filter_hostile` — so it never touched `_fetch_gdelt_events`, which is where the
    coordinates actually come from. A verification pass changed the column indices from
    `row[56]/row[57]` to `row[46]/row[47]` and the entire 995-test suite stayed green:
    the globe's spine could be silently broken with nothing to catch it.

    This builds a real 61-column GDELT export row, zips it as GDELT does, serves it
    through a stubbed `requests.get`, and asserts the parser reads the coordinates back
    out of the RIGHT columns.
    """
    import io
    import zipfile

    row = [""] * 61
    row[0]  = "GEO-1"          # event_id
    row[26] = "190"            # cameo
    row[30] = "-9.0"           # goldstein
    row[31] = "40"             # num_mentions
    row[34] = "-8.0"           # avg_tone
    row[37] = "XX"             # a DIFFERENT country field — must lose to ActionGeo
    row[53] = "RS"             # ActionGeo_CountryCode
    row[56] = "55.7558"        # ActionGeo_Lat
    row[57] = "37.6173"        # ActionGeo_Long
    row[60] = "http://example.invalid/story"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export.CSV", "\t".join(row) + "\n")
    payload = buf.getvalue()

    class _Resp:
        def __init__(self, text=b"", content=b""):
            self.text = text if isinstance(text, str) else ""
            self.content = content
        def raise_for_status(self):
            pass

    def _get(url, **kw):
        if url.endswith("lastupdate.txt"):
            return _Resp(text="123 abc http://example.invalid/x.export.CSV.zip")
        return _Resp(content=payload)

    real_get = osint.requests.get
    osint.requests.get = _get
    try:
        events = osint._fetch_gdelt_events()
    finally:
        osint.requests.get = real_get

    assert len(events) == 1, events
    e = events[0]
    assert (e["geo_lat"], e["geo_lng"]) == (55.7558, 37.6173), \
        f"the ActionGeo columns no longer yield the event's coordinates: {e}"
    assert e["country"] == "RS", "ActionGeo_CountryCode is no longer preferred"
    assert e["source_url"] == "http://example.invalid/story"
    # ...and the coordinates survive the hostile filter, which is what carries them on.
    assert osint._filter_hostile(events)[0]["geo_lat"] == 55.7558


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


# ── 4. the surfaces that used to invent content ─────────────────────────────

def test_the_NEWS_TAPE_is_empty_rather_than_fabricated():
    """`/api/conflict-news` served six invented headlines labelled `"source": "Scope
    OSINT"` whenever it had no rows — "South China Sea — ADS-B military flight tracking
    enabled" — on the SAME page as the globe whose hotspots were being fabricated by the
    same reasoning. Retiring the emission made that the PERMANENT state.
    """
    conn = db_connection()
    conn.execute("DELETE FROM alerts")
    conn.commit()
    conn.close()
    assert m.conflict_news() == []

    # ⚠️ THE LINT READS THE AST, NOT THE TEXT — for the third time this session a
    # source-grep found the paragraph EXPLAINING the deletion and failed on it. Only
    # string constants that the function would actually return are inspected; the
    # docstring is skipped by construction.
    import ast as _ast
    tree = _ast.parse(open(os.path.join(os.path.dirname(__file__), "..",
                                        "api", "main.py")).read())
    fn = next(n for n in _ast.walk(tree)
              if isinstance(n, _ast.FunctionDef) and n.name == "conflict_news")
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], _ast.Expr)
                           and isinstance(fn.body[0].value, _ast.Constant)
                           and isinstance(fn.body[0].value.value, str)) else fn.body
    literals = [n.value for b in body for n in _ast.walk(b)
                if isinstance(n, _ast.Constant) and isinstance(n.value, str)]
    assert not [x for x in literals if "Scope OSINT" in x or "monitoring GDELT" in x], \
        f"the invented headlines are back: {literals}"


def test_a_REAL_conflict_headline_still_reaches_the_tape():
    """The control for the test above."""
    conn = db_connection()
    conn.execute("DELETE FROM alerts")
    _seed(conn, {"source": "GDELT", "source_url": "http://example.invalid/x"}, "TSM")
    conn.close()
    out = m.conflict_news()
    assert len(out) == 1 and out[0]["source"] == "GDELT"


def test_an_UNMAPPED_region_is_not_placed_in_the_LEVANT():
    """The "Middle East" default was removed as a STRING and survived as a pair of
    NUMBERS: `REGION_COORDS.get(region, (31.0, 35.0))` — 31.0N 35.0E is the Levant, so an
    unmapped region with no event coordinates was still placed in a war zone. Latent
    (every `COUNTRY_REGION_MAP` value has coordinates), removed on the same argument."""
    conn = db_connection()
    conn.execute("DELETE FROM alerts")
    _seed(conn, {"region": "Atlantis"}, "XOM")
    conn.close()
    out = m.osint_hotspots()
    assert out == [], f"an unmapped region was given a fallback coordinate: {out}"


def test_a_DRY_RUN_does_not_consume_the_dedupe(monkeypatch):
    """A dry run with a side effect is not a dry run. Under the retirement the event was
    still marked seen, so `--dry-run` silently changed what a later real run would
    process."""
    event = {"event_id": "DRY-1", "cameo": "190", "goldstein": -9.0, "num_mentions": 40,
             "avg_tone": -8.0, "country": "RS", "geo_lat": 55.7, "geo_lng": 37.6,
             "source_url": ""}
    monkeypatch.setattr(osint, "_fetch_gdelt_events", lambda: [event])
    conn = db_connection()
    conn.execute("DELETE FROM gdelt_events")
    conn.commit()
    osint._run_gdelt(conn, emit=True, dry_run=True)
    dry = conn.execute("SELECT COUNT(*) FROM gdelt_events").fetchone()[0]
    osint._run_gdelt(conn, emit=True, dry_run=False)
    wet = conn.execute("SELECT COUNT(*) FROM gdelt_events").fetchone()[0]
    conn.close()
    assert dry == 0, "a dry run consumed the dedupe"
    assert wet == 1, "a real run stopped recording the event"


def test_the_region_context_endpoint_still_RESOLVES_its_import():
    """A REGRESSION THIS SESSION CAUSED AND THE VERIFIER CAUGHT. Removing
    `REGION_TICKERS` from the globe endpoint's import was done with an unbounded
    `str.replace`, so it also stripped the import from `osint_region_context` — a
    different function, with no module-level binding to fall back on. Every call raised
    `NameError`, two live pages fetch it (`osint_region.html:334,449`), and the entire
    suite stayed green.

    ⚠️ A non-empty basket still raises `OperationalError: no such column: recipient` —
    verified identical on `main`, so it is PRE-EXISTING and out of scope here. This test
    therefore pins the NAME RESOLUTION, which is what was broken, using a region with no
    basket so the pre-existing defect cannot mask it.
    """
    out = m.osint_region_context("Nowhere")     # not a REGION_TICKERS key
    assert out["tickers"] == [] and out["trades"] == [] and out["contracts"] == []
