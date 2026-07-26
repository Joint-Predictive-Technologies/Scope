#!/usr/bin/env python3
"""
The forming-convergence (near-miss) surface.

Two properties matter more than the rest:

  1. **A real convergence must never appear here.** A ticker at 3+ instruments has
     fired the gate; showing it as "forming" would present a confirmed signal as a
     tentative one — and, worse, the operator would see the same ticker in two
     places with two meanings.
  2. **The surface must be COUPLED to the gate, not a copy of it.** It imports
     `rule10_instruments` and the RULE_10_* / window constants. A re-implementation
     would drift silently — exactly the bug that let RULE_01 count as a second
     congressional instrument. There is an explicit coupling test below: patch the
     gate's map, and the surface's output must move with it.

Everything runs against fixtures on a disposable DB. What is near-miss on *real*
data is a separate, corroborative question and is not asserted here.

Runs under pytest or standalone:  python3 tests/test_near_miss_surface.py
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                              # noqa: E402
from api.routers import forming                                # noqa: E402

STATIC = pathlib.Path(__file__).resolve().parent.parent / "api" / "static"


def _seed(rows) -> None:
    """rows: [(ticker, rule, age_expression)]"""
    conn = jpt_common.db_connection()
    for ticker, rule, age in rows:
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at)
               VALUES (?, ?, 'HIGH', ?, datetime('now', ?))""",
            (rule, ticker, f"{rule} fired on {ticker}", age),
        )
    conn.commit()
    conn.close()


def _near() -> list[dict]:
    conn = jpt_common.db_connection()
    try:
        return forming.find_near_misses(conn)
    finally:
        conn.close()


def _tickers() -> list[str]:
    return [f["ticker"] for f in _near()]


# ---------------------------------------------------------------------------
# THE headline: a convergence is not a near-miss
# ---------------------------------------------------------------------------

def test_a_three_instrument_convergence_does_NOT_appear():
    """It has fired the gate. Showing it here would mislabel a real signal."""
    _seed([("FIRED", "RULE_01B", "-1 days"),      # congressional
           ("FIRED", "RULE_11", "-2 days"),       # contracts
           ("FIRED", "RULE_06", "-3 days")])      # insider

    assert "FIRED" not in _tickers(), "a fired convergence leaked into the near-miss list"


def test_exactly_two_instruments_appears():
    _seed([("FORM", "RULE_01B", "-1 days"), ("FORM", "RULE_11", "-2 days")])

    result = _near()
    assert [f["ticker"] for f in result] == ["FORM"]
    assert result[0]["instruments"] == ["congressional", "contracts"]
    assert result[0]["instrument_count"] == 2
    assert result[0]["needed"] == 3


def test_one_instrument_does_not_appear():
    _seed([("SOLO", "RULE_01B", "-1 days")])
    assert "SOLO" not in _tickers()


def test_the_congressional_trio_resolves_to_one_and_does_not_appear():
    """The D1 property, inherited: three rules, one source, one instrument."""
    _seed([("TRIO", "RULE_01B", "-1 days"),
           ("TRIO", "RULE_02", "-2 days"),
           ("TRIO", "RULE_CLUSTER", "-3 days")])

    assert "TRIO" not in _tickers(), "the congressional trio surfaced as a near-miss"


def test_the_trio_plus_one_other_instrument_IS_a_near_miss():
    """Trio (1) + contracts (1) = 2 instruments — genuinely one leg short."""
    _seed([("TRIO2", "RULE_01B", "-1 days"), ("TRIO2", "RULE_02", "-2 days"),
           ("TRIO2", "RULE_CLUSTER", "-3 days"), ("TRIO2", "RULE_11", "-4 days")])

    result = _near()
    assert [f["ticker"] for f in result] == ["TRIO2"]
    assert result[0]["instruments"] == ["congressional", "contracts"]


def test_missing_legs_are_named():
    _seed([("MISS", "RULE_01B", "-1 days"), ("MISS", "RULE_06", "-2 days")])

    entry = _near()[0]
    assert entry["instruments"] == ["congressional", "insider"]
    missing = entry["missing_legs"]
    assert "contracts" in missing and "fed-register" in missing
    # what it already has must not be listed as missing
    assert "congressional" not in missing and "insider" not in missing


# ---------------------------------------------------------------------------
# eligibility inherited from the gate
# ---------------------------------------------------------------------------

def test_excluded_rules_contribute_nothing():
    """Noise rules cannot manufacture a near-miss."""
    _seed([("NOISE", "RULE_01B", "-1 days"),
           ("NOISE", "RULE_07", "-1 days"),
           ("NOISE", "RULE_OSINT", "-2 days"),
           ("NOISE", "RULE_REDDIT", "-3 days"),
           ("NOISE", "RULE_ANOMALY", "-4 days")])

    assert "NOISE" not in _tickers(), "excluded rules produced a near-miss"


def test_excluded_rules_do_not_appear_as_evidence():
    """Even on a genuine near-miss, noise must not be listed as a contributing leg."""
    _seed([("EVID", "RULE_01B", "-1 days"), ("EVID", "RULE_11", "-2 days"),
           ("EVID", "RULE_OSINT", "-1 days")])

    rules = {a["rule"] for a in _near()[0]["alerts"]}
    assert rules == {"RULE_01B", "RULE_11"}, rules


def test_medium_severity_does_not_count():
    conn = jpt_common.db_connection()
    for rule in ("RULE_01B", "RULE_11"):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at)
               VALUES (?, 'MED', 'MEDIUM', 'medium', datetime('now','-1 days'))""",
            (rule,),
        )
    conn.commit()
    conn.close()
    assert "MED" not in _tickers()


def test_the_fourteen_day_window_is_respected():
    _seed([("WIN", "RULE_01B", "-1 days"), ("WIN", "RULE_11", "-13 days")])
    assert "WIN" in _tickers(), "13 days apart should be inside the window"

    _seed([("OUT", "RULE_01B", "-1 days"), ("OUT", "RULE_11", "-15 days")])
    assert "OUT" not in _tickers(), "a leg 15 days old is outside the window"


def test_ordering_is_newest_leg_first():
    _seed([("OLD", "RULE_01B", "-9 days"), ("OLD", "RULE_11", "-10 days"),
           ("NEW", "RULE_01B", "-1 hours"), ("NEW", "RULE_11", "-2 days")])
    assert _tickers()[:2] == ["NEW", "OLD"]


# ---------------------------------------------------------------------------
# THE COUPLING — the surface must move when the gate moves
# ---------------------------------------------------------------------------

def test_it_imports_the_gate_symbols_rather_than_redefining_them():
    from scripts.rule_10_corroboration import CONVERGENCE_WINDOW_DAYS

    assert forming.rule10_instruments is jpt_common.rule10_instruments
    assert forming.RULE_10_EXCLUDED is jpt_common.RULE_10_EXCLUDED
    assert forming.RULE_10_MIN_INSTRUMENTS == jpt_common.RULE_10_MIN_INSTRUMENTS
    assert forming.CONVERGENCE_WINDOW_DAYS == CONVERGENCE_WINDOW_DAYS
    # The UPPER bound must be the gate's threshold, not a copy of it: the filter
    # is `FLOOR <= n < RULE_10_MIN_INSTRUMENTS`, so a threshold change can never
    # leave a fired convergence labelled "forming".
    source = pathlib.Path(forming.__file__).read_text()
    assert "< RULE_10_MIN_INSTRUMENTS" in source, (
        "the upper bound is not derived from the gate threshold")
    assert forming.NEAR_MISS_FLOOR_INSTRUMENTS == 2


def test_changing_the_gates_instrument_map_changes_this_surface(monkeypatch):
    """The coupling proof. A copied map would not respond to this.

    Split the congressional trio into three separate instruments in the GATE's
    map. A trio-only ticker then has 3 instruments — a convergence — and must
    vanish from the near-miss list. If this surface had its own copy of the map,
    the ticker would still read as 1 instrument and nothing would change.
    """
    _seed([("COUPLE", "RULE_01B", "-1 days"),
           ("COUPLE", "RULE_02", "-2 days"),
           ("COUPLE", "RULE_CLUSTER", "-3 days"),
           ("COUPLE", "RULE_11", "-4 days")])

    assert "COUPLE" in _tickers(), "precondition: trio+contracts is a near-miss"

    patched = dict(jpt_common.RULE_10_INSTRUMENTS)
    patched.update({"RULE_01B": "cong_a", "RULE_02": "cong_b", "RULE_CLUSTER": "cong_c"})
    monkeypatch.setattr(jpt_common, "RULE_10_INSTRUMENTS", patched)

    assert "COUPLE" not in _tickers(), (
        "the near-miss surface did not follow the gate's instrument map — it is "
        "using its own copy and can drift")


def test_raising_the_gate_threshold_widens_the_near_miss_band(monkeypatch):
    """Raise the gate to 4: BOTH 2- and 3-instrument tickers become near-misses.

    This is why the filter is a range rather than `== threshold - 1`. With exact
    equality the 2-instrument ticker would silently vanish from the list the
    moment the threshold moved.
    """
    _seed([("THREE", "RULE_01B", "-1 days"), ("THREE", "RULE_11", "-2 days"),
           ("THREE", "RULE_06", "-3 days")])
    _seed([("TWO", "RULE_01B", "-1 days"), ("TWO", "RULE_11", "-2 days")])

    assert _tickers() == ["TWO"], "precondition: only the 2-instrument ticker"

    monkeypatch.setattr(forming, "RULE_10_MIN_INSTRUMENTS", 4)

    widened = set(_tickers())
    assert widened == {"TWO", "THREE"}, (
        f"a raised threshold must widen the band, not replace it: {widened}")


# ---------------------------------------------------------------------------
# honesty of the surface
# ---------------------------------------------------------------------------

def _get(path: str) -> dict:
    """Call the route function directly, passing the query param EXPLICITLY.

    Not TestClient: entering its context manager runs the app's lifespan, and
    `api/main.py`'s lifespan kicks off `_run_rules([*_RULE_SCHEDULE,
    *_CRON_SCHEDULE])` whenever the data looks stale — i.e. it shells out to every
    rule script. In a test that hangs for minutes and hits the network. Calling
    the endpoint function is enough here; the route wiring is asserted separately
    by inspecting `app.routes`.

    The explicit argument matters: the signature's default is FastAPI's `Query`
    object, which is only resolved to a value by the request cycle.
    """
    if path == "/forming/count":
        return forming.forming_count()
    return forming.list_forming(window_days=None)


def test_the_api_labels_these_as_not_confirmed():
    _seed([("LBL", "RULE_01B", "-1 days"), ("LBL", "RULE_11", "-2 days")])

    payload = _get("/forming")
    assert payload["status"] == "forming"
    assert "not a confirmed signal" in payload["label"]
    assert payload["threshold"] == 3


def test_the_api_returns_no_confidence_or_win_rate_field():
    """A near-miss is a watch item — it must carry no score-like number."""
    _seed([("NOCONF", "RULE_01B", "-1 days"), ("NOCONF", "RULE_11", "-2 days")])

    payload = _get("/forming")
    blob = repr(payload).lower()
    for banned in ("confidence", "win_rate", "win-rate", "probability",
                   "opportunity_score", "evidence_confidence", "score"):
        assert banned not in blob, f"the forming payload exposes {banned!r}"


def test_the_page_states_these_are_not_signals():
    html = (STATIC / "forming.html").read_text()
    assert "These are not signals" in html
    assert "2 of the 3 independent instruments" in html
    assert "one\n    leg short" in html or "leg short" in html


def test_the_page_renders_no_confidence_or_probability():
    html = (STATIC / "forming.html").read_text().lower()
    # the disclaimer may NAME these to deny them; no data binding may render one
    for banned in ("f.confidence", "f.win_rate", "f.score", "f.probability",
                   "opportunity_score", "evidence_confidence"):
        assert banned not in html, f"forming.html renders {banned!r}"


def test_the_page_is_routed_and_the_router_registered():
    from api import main as appmain

    routes = {getattr(r, "path", "") for r in appmain.app.routes}
    assert "/forming" in routes
    assert any(p.startswith("/forming") for p in routes)


# ---------------------------------------------------------------------------
# Stage 4 — the count must agree with the list
# ---------------------------------------------------------------------------

def test_the_count_endpoint_matches_the_list():
    _seed([("A1", "RULE_01B", "-1 days"), ("A1", "RULE_11", "-2 days"),
           ("B1", "RULE_06", "-1 days"), ("B1", "RULE_09", "-2 days"),
           ("C1", "RULE_01B", "-1 days")])                       # 1 instrument

    assert _get("/forming/count")["count"] == 2
    assert _get("/forming")["count"] == 2
    assert set(_tickers()) == {"A1", "B1"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
