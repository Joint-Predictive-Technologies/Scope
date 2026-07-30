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
    """rows: [(ticker, rule, age_expression)] or [(ticker, rule, age, corroborates)]

    ⚠️ A SIGNED RULE'S LEG IS SEEDED AS A GENUINE BUY UNLESS THE ROW SAYS OTHERWISE, and
    that default is deliberate rather than convenient. Since 2026-07-30 the gate asks a
    second question per alert — an insider leg counts only on a genuine open-market buy —
    and a NULL verdict fails closed. Every fixture here predates that column, so leaving
    it NULL would silently drop the insider leg from every scenario below and these tests
    would then be measuring a different arithmetic than the one they were written for.
    Spelling the direction out in each row instead would bury what they actually test.
    The rejected direction is covered explicitly by
    `test_a_non_corroborating_insider_leg_does_not_count_toward_the_band`.
    """
    conn = jpt_common.db_connection()
    for row in rows:
        ticker, rule, age = row[0], row[1], row[2]
        corroborates = row[3] if len(row) > 3 else (
            1 if rule.upper() in jpt_common.SIGNED_RULES else None)
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at,
                                   corroborates)
               VALUES (?, ?, 'HIGH', ?, datetime('now', ?), ?)""",
            (rule, ticker, f"{rule} fired on {ticker}", age, corroborates),
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


def test_a_non_corroborating_insider_leg_does_not_count_toward_the_band():
    """THE SIGNED LEG, on this surface. Same three rules as the test above — but the
    insider SOLD, so it is not corroboration and the ticker is two legs, not three.

    ⚠️ THIS SURFACE HAS ITS OWN COPY OF THE GATE'S CANDIDATE QUERY, so it is exactly where
    a per-alert decision goes stale unnoticed. If `find_near_misses` stopped importing
    `instruments_for` and went back to counting rule names, this ticker would vanish from
    the list (counted as fired at 3) while the gate correctly refused to fire it — a
    convergence that is invisible on both surfaces.
    """
    _seed([("SOLD", "RULE_01B", "-1 days"),
           ("SOLD", "RULE_11", "-2 days"),
           ("SOLD", "RULE_06", "-3 days", 0)])       # insider, but a SALE

    items = {f["ticker"]: f for f in _near()}
    assert "SOLD" in items, (
        "a ticker whose third leg does not corroborate must appear as FORMING — the gate "
        "will not fire it, so hiding it here loses it from every surface at once")
    assert items["SOLD"]["instrument_count"] == 2
    assert "insider" not in items["SOLD"]["instruments"]
    # and the rejected leg is disclosed rather than silently absent
    assert [d["rule"] for d in items["SOLD"]["non_corroborating"]] == ["RULE_06"]
    assert "RULE_06" not in [a["rule"] for a in items["SOLD"]["alerts"]]


def test_an_insider_leg_with_NO_verdict_also_does_not_count():
    """Forward-only: every alert written before the verdict column existed carries NULL,
    and NULL fails closed. Pinned so a later "convenience" default to 1 is visible."""
    _seed([("NOVERDICT", "RULE_01B", "-1 days"),
           ("NOVERDICT", "RULE_11", "-2 days"),
           ("NOVERDICT", "RULE_06", "-3 days", None)])

    items = {f["ticker"]: f for f in _near()}
    assert items["NOVERDICT"]["instrument_count"] == 2
    assert "insider" not in items["NOVERDICT"]["instruments"]


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

    # ⚠️ THE PER-ALERT DECISION IS COUPLED TOO, AND LATE BINDING IS THE ACTUAL PROPERTY.
    # `find_near_misses` used to carry a hand-written copy of `instruments_for`'s body,
    # harmless while the gate counted only rule names and a silent divergence the moment it
    # began reading each alert. Asserting `forming.instruments_for is r10.instruments_for`
    # is NOT enough: with `from ... import instruments_for` that holds at import and then
    # goes stale the moment anything reloads the gate module — which
    # `test_divergence_is_impossible_not_merely_absent` does deliberately. Measured: the
    # import form passed alone and failed in the full suite.
    #
    # So this asserts the surface RESOLVES THE FUNCTION AT CALL TIME, by swapping the gate's
    # implementation and checking the surface follows.
    from scripts import rule_10_corroboration as _r10
    assert forming._gate is _r10, "forming no longer resolves the gate through its module"

    _seed([("LATEBIND", "RULE_01B", "-1 days"), ("LATEBIND", "RULE_11", "-2 days")])
    assert "LATEBIND" in _tickers(), "precondition: it should be a near-miss at 2"

    real = _r10.instruments_for
    try:
        _r10.instruments_for = lambda alerts: []      # the gate now counts nothing
        assert "LATEBIND" not in _tickers(), (
            "the surface did not follow the gate's own function — it is holding a stale "
            "or private copy, so the two can report different instrument counts")
    finally:
        _r10.instruments_for = real
    assert "LATEBIND" in _tickers(), "restore leaked"
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


# An ALLOW-list, not a deny-list. A deny-list of banned words misses anything
# renamed (`likelihood_of_firing`, `strength`, `completion_pct`, `odds`) and is
# brittle in the other direction — a legitimate headline like "LMT scored $2B Navy
# award" would trip a naive grep for "score". Pinning the exact schema is both
# stricter and stable.
ALLOWED_TOP_KEYS = {"status", "label", "window_days", "threshold", "count", "forming"}
ALLOWED_ITEM_KEYS = {"ticker", "instruments", "instrument_count", "needed",
                     # A leg that fired but does not corroborate, with the reason. Not
                     # score-shaped — it is the honest counterpart to `missing_legs`: the
                     # difference between "no insider filed" and "an insider sold".
                     "missing_legs", "latest_at", "alerts", "non_corroborating"}
ALLOWED_ALERT_KEYS = {"id", "rule", "severity", "headline", "created_at"}


def test_the_api_payload_schema_admits_no_score_like_field():
    """A near-miss is a watch item. Nothing score-shaped may reach the client."""
    _seed([("NOCONF", "RULE_01B", "-1 days"), ("NOCONF", "RULE_11", "-2 days")])

    payload = _get("/forming")
    assert set(payload) == ALLOWED_TOP_KEYS, set(payload) ^ ALLOWED_TOP_KEYS
    for item in payload["forming"]:
        assert set(item) == ALLOWED_ITEM_KEYS, set(item) ^ ALLOWED_ITEM_KEYS
        for alert in item["alerts"]:
            assert set(alert) == ALLOWED_ALERT_KEYS, set(alert) ^ ALLOWED_ALERT_KEYS

    # `threshold` and `instrument_count` are counts of instruments, not scores
    assert isinstance(payload["threshold"], int)
    assert all(isinstance(i["instrument_count"], int) for i in payload["forming"])


def test_the_page_states_these_are_not_signals():
    html = (STATIC / "forming.html").read_text()
    assert "These are not signals" in html
    assert "independent instruments" in html
    assert "short of firing" in html
    assert "no confidence score and no probability" in html
    assert "Most will do the latter" in html          # i.e. most will NOT complete


def test_the_page_binds_only_the_allowed_fields():
    """Allow-list the template bindings.

    The previous deny-list version was decisively too weak: an independent pass
    showed it missed 5 of 5 mutants that added confidence framing — a computed
    percentage, a per-alert score, a "chance of firing", a STRENGTH meter, and a
    rewritten disclaimer claiming a historical completion rate. Enumerating what
    MAY be bound catches all of those, because any new binding is a new name.
    """
    import re

    html = (STATIC / "forming.html").read_text()
    bound = set(re.findall(r"\b(?:f|a|data)\.([A-Za-z_][A-Za-z0-9_]*)", html))
    allowed = ALLOWED_TOP_KEYS | ALLOWED_ITEM_KEYS | ALLOWED_ALERT_KEYS | {"length", "map"}

    unexpected = bound - allowed
    assert not unexpected, (
        f"forming.html binds fields outside the allowed schema: {sorted(unexpected)}")

    # and no arithmetic dressing a count up as a rate
    for pattern in (r"100\s*\*", r"\*\s*100", r"toFixed\(", r"%\s*(chance|confidence|probability)"):
        assert not re.search(pattern, html, re.I), f"forming.html computes {pattern!r}"


def test_the_disclaimer_does_not_hardcode_the_threshold():
    """The one sentence whose job is to be true must not carry a stale copy."""
    html = (STATIC / "forming.html").read_text()
    assert "2 of the 3" not in html
    assert 'id="d-need"' in html and "data.threshold" in html


def test_the_html_page_is_actually_served_at_slash_forming(tmp_path, monkeypatch):
    """The API must NOT shadow the page.

    The router was first mounted at the bare `/forming`, the same path as the HTML
    page. Starlette matches the first registered route, so `GET /forming` returned
    JSON and the page — with the "these are not signals" disclaimer that is the
    entire point of it — was unreachable. The nav link served raw JSON. The old
    test only asserted `"/forming" in routes`, which is true for either handler,
    so it was green for the wrong reason.

    TestClient is used WITHOUT its context manager on purpose: entering it runs
    the app lifespan, which shells out to every rule script.
    """
    from starlette.testclient import TestClient

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "route.db"))
    jpt_common.db_connection().close()

    from api import main as appmain
    client = TestClient(appmain.app)

    page = client.get("/forming")
    assert page.status_code == 200
    assert page.text.lstrip().startswith("<!DOCTYPE"), (
        "GET /forming did not serve the HTML page — the API router is shadowing it")
    assert "These are not signals" in page.text

    api = client.get("/api/forming")
    assert api.status_code == 200
    assert api.headers["content-type"].startswith("application/json")
    assert client.get("/api/forming/count").status_code == 200


def test_the_window_is_taken_from_the_gate_not_hardcoded():
    """A literal 14 here would pass an equality check via the unused import.

    Assert structurally that the constant is USED, not merely imported — a
    hardcoded window would keep the gate's value in scope while ignoring it.
    """
    source = pathlib.Path(forming.__file__).read_text()
    assert "window_days if window_days is not None else CONVERGENCE_WINDOW_DAYS" in source
    assert "= 14" not in source, "the window looks hardcoded"


def test_a_ticker_that_already_fired_is_not_shown_as_forming():
    """Otherwise the same ticker means two different things in two places.

    A fired convergence re-enters this list as "forming" as soon as one leg ages
    out of the window. The gate guards its own re-firing with a dedup window;
    this mirrors it using the same imported constant.
    """
    _seed([("FIREDX", "RULE_01B", "-1 days"), ("FIREDX", "RULE_11", "-2 days")])
    assert "FIREDX" in _tickers(), "precondition: it is a 2-instrument near-miss"

    conn = jpt_common.db_connection()
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, created_at)
           VALUES ('RULE_10','FIREDX','CRITICAL','[CORROBORATION] FIREDX',
                   datetime('now','-2 days'))"""
    )
    conn.commit()
    conn.close()

    assert "FIREDX" not in _tickers(), (
        "a ticker that already fired RULE_10 is being shown as forming")


def test_the_already_fired_guard_uses_the_gates_dedup_window():
    from scripts.rule_10_corroboration import DEDUP_WINDOW_DAYS

    assert forming.DEDUP_WINDOW_DAYS == DEDUP_WINDOW_DAYS
    # a corroboration older than the dedup window no longer suppresses it
    _seed([("OLDFIRE", "RULE_01B", "-1 days"), ("OLDFIRE", "RULE_11", "-2 days")])
    conn = jpt_common.db_connection()
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, created_at)
           VALUES ('RULE_10','OLDFIRE','CRITICAL','old corroboration',
                   datetime('now', ? || ' days'))""",
        (f"-{DEDUP_WINDOW_DAYS + 3}",),
    )
    conn.commit()
    conn.close()

    assert "OLDFIRE" in _tickers(), (
        "an expired corroboration should no longer suppress the near-miss")


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
