#!/usr/bin/env python3
"""
RULE_10 gate redesign — D1 (instruments, not rules), D2 (threshold 3), D4 (14d).

The single most important test in this file is
`test_congressional_trio_alone_does_NOT_fire`. The old gate required 4 distinct
*rule names*, and three of the six rules that could realistically satisfy it —
RULE_01B, RULE_02, RULE_CLUSTER — all read the same `transactions` table. So one
instrument wearing three rule names could supply most of a "cross-source"
corroboration. If that test ever passes-by-firing, the moat's core claim is false
and the redesign has been undone.

What these tests can and cannot show: they run against fixtures on a disposable
DB, so they pin the *gate logic* — counting, threshold, window, exclusions.
Whether convergence actually fires on real data is NOT provable here and is not
claimed; it needs prod after RULE_06 has accumulated insider alerts alongside the
other instruments.

Runs under pytest or standalone:  python3 tests/test_gate_redesign.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                                   # noqa: E402
from jpt_common import (RULE_10_EXCLUDED, RULE_10_MIN_INSTRUMENTS,  # noqa: E402
                        rule10_instruments, rule10_is_valid)

_spec = importlib.util.spec_from_file_location(
    "r10", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "rule_10_corroboration.py"))
r10 = importlib.util.module_from_spec(_spec)
sys.modules["r10"] = r10
_spec.loader.exec_module(r10)

TICKER = "ZWAR"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _seed(rules_with_age, ticker: str = TICKER) -> None:
    """rules_with_age: [(rule, age_expression)] or [(rule, age, corroborates)].

    ⚠️ A SIGNED RULE'S LEG DEFAULTS TO A GENUINE OPEN-MARKET BUY, and the default is a
    considered choice rather than a shortcut. From 2026-07-30 the gate asks a second
    question per ALERT — does this leg actually say the bullish thing — and a NULL verdict
    fails closed. Every fixture in this file predates that column, so leaving it unset
    would silently delete the insider leg from D1's own test cases and they would then be
    measuring 2-instrument arithmetic while claiming to measure 3.

    The direction itself is tested where it belongs — `tests/test_signed_insider_leg.py`
    for the rule, and `test_a_non_corroborating_insider_leg_cannot_complete_the_gate`
    below for the gate.
    """
    conn = jpt_common.db_connection()
    for row in rules_with_age:
        rule, age = row[0], row[1]
        corroborates = row[2] if len(row) > 2 else (
            1 if rule.upper() in jpt_common.SIGNED_RULES else None)
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at,
                                   corroborates)
               VALUES (?, ?, 'HIGH', ?, datetime('now', ?), ?)""",
            (rule, ticker, f"{rule} fired on {ticker}", age, corroborates),
        )
    conn.commit()
    conn.close()


def _fires(window_hours: int | None = None) -> bool:
    conn = jpt_common.db_connection()
    hours = window_hours if window_hours is not None else r10.CONVERGENCE_WINDOW_DAYS * 24
    clusters = r10.find_corroborated_tickers(conn, hours)
    conn.close()
    return TICKER in clusters


def _instruments(window_hours: int | None = None) -> list[str]:
    """What the GATE counts for this ticker.

    ⚠️ THIS HELPER USED TO BE A COPY OF `instruments_for`'s BODY —
    `rule10_instruments({r["rule"] for r in rows ...})` — and that made it a fifth
    hand-written copy of the gate's counting rule, alongside the gate itself,
    `api/routers/forming.py`, `scripts/morning_brief.py` and `api/static/ticker.html`.
    It was correct only while the gate counted nothing but rule names; the moment the gate
    began deciding per alert, this helper started reporting a number the gate does not use,
    inside the very file that exists to pin the gate's counting. It now calls the gate's
    own function, so it cannot drift again.
    """
    conn = jpt_common.db_connection()
    hours = window_hours if window_hours is not None else r10.CONVERGENCE_WINDOW_DAYS * 24
    rows = r10._candidate_alerts(conn, hours)
    conn.close()
    return r10.instruments_for([r for r in rows if r["ticker"] == TICKER])


# ---------------------------------------------------------------------------
# D1 — the headline: one instrument cannot satisfy the gate
# ---------------------------------------------------------------------------

def test_congressional_trio_alone_does_NOT_fire():
    """THE test. Three rules, one source, must not be a convergence."""
    _seed([("RULE_01B", "-1 days"), ("RULE_02", "-2 days"), ("RULE_CLUSTER", "-3 days")])

    assert _instruments() == ["congressional"], _instruments()
    assert not _fires(), "the congressional trio satisfied the gate on its own"


def test_trio_counts_as_exactly_one_instrument():
    _seed([("RULE_01B", "-1 days"), ("RULE_02", "-2 days"), ("RULE_CLUSTER", "-3 days")])
    assert len(_instruments()) == 1


def test_trio_plus_two_other_instruments_fires():
    """The trio still contributes — as one leg of three."""
    _seed([("RULE_01B", "-1 days"), ("RULE_02", "-2 days"), ("RULE_CLUSTER", "-3 days"),
           ("RULE_11", "-4 days"), ("RULE_06", "-5 days")])

    assert _instruments() == ["congressional", "contracts", "insider"]
    assert _fires()


def test_three_distinct_instruments_count_as_three():
    _seed([("RULE_01B", "-1 days"), ("RULE_11", "-2 days"), ("RULE_06", "-3 days")])
    assert len(_instruments()) == 3
    assert _fires()


# ── D3 (2026-07-30): a leg must SAY the thing, not merely be present ─────────
#
# The gate used to count presence alone, so an insider REDUCING exposure corroborated a
# bullish thesis exactly as well as one buying. That shipped: prod theme 1 fired on RTX at
# exactly 3 instruments where the insider leg was an exercise-and-sell.

def test_a_non_corroborating_insider_leg_cannot_complete_the_gate():
    """THE REGRESSION, at the gate. Identical to the test above except the insider SOLD."""
    _seed([("RULE_01B", "-1 days"), ("RULE_11", "-2 days"), ("RULE_06", "-3 days", 0)])
    assert _instruments() == ["congressional", "contracts"], _instruments()
    assert not _fires(), "an insider SALE completed a bullish convergence"


def test_an_insider_leg_with_no_verdict_on_record_fails_closed():
    """Forward-only. Every RULE_06 alert written before this shipped carries NULL, and the
    stored `sale`/`purchase` tag is NOT an acceptable fallback: it comes from
    `majority_action`, which only ever saw codes P and S, so it reads the RTX
    exercise-and-sell as a plain sale and an exercise-and-HOLD as a purchase."""
    _seed([("RULE_01B", "-1 days"), ("RULE_11", "-2 days"), ("RULE_06", "-3 days", None)])
    assert _instruments() == ["congressional", "contracts"], _instruments()
    assert not _fires()


def test_a_genuine_buy_still_completes_the_gate():
    """The positive control — without it the two tests above pass on a dead gate."""
    _seed([("RULE_01B", "-1 days"), ("RULE_11", "-2 days"), ("RULE_06", "-3 days", 1)])
    assert _instruments() == ["congressional", "contracts", "insider"], _instruments()
    assert _fires()


@pytest.mark.parametrize("rule,instrument", [
    ("RULE_01B", "congressional"), ("RULE_09", "senate-lda"),
    ("RULE_11", "contracts"), ("RULE_15", "earnings"), ("RULE_16", "institutional"),
])
def test_the_UNSIGNED_instruments_are_completely_untouched(rule, instrument):
    """⚠️ THE BLAST-RADIUS PROOF, and the reason `SIGNED_RULES` exists as a set rather than
    as scattered `if rule == "RULE_06"` checks.

    Only a signed rule is interrogated per alert. Every other rule contributes its
    instrument on presence alone — with a NULL verdict, with an explicit 0, with anything —
    exactly as it did before this change. If a future session signs one of these WITHOUT
    repairing its attribution first, this test fails and names it. That matters most for
    RULE_15 (which misattributed *rituximab* to RTX) and RULE_01B (~46% of sales
    mislabelled as opens): a confident sign on known-wrong data makes a false convergence
    look MORE credible, not less.
    """
    assert rule not in jpt_common.SIGNED_RULES, (
        f"{rule} has been signed — its ATTRIBUTION must be repaired first, and this test "
        f"and tests/test_signed_insider_leg.py both need updating deliberately")
    for verdict in (None, 0, 1):
        conn = jpt_common.db_connection()
        conn.execute("DELETE FROM alerts WHERE ticker = ?", (TICKER,))
        conn.commit()
        conn.close()
        _seed([(rule, "-1 days", verdict)])
        assert _instruments() == [instrument], (
            f"{rule} with corroborates={verdict!r} resolved to {_instruments()} — an "
            f"unsigned rule must not be affected by the per-alert verdict at all")


def test_same_source_rules_collapse_even_under_different_names():
    """RULE_09 and RULE_12 both read lda.senate.gov — one instrument.

    Derived from code, and a deliberate deviation from the design note (which
    lists lobbying and foreign-agents separately): rule_12_fara.py's docstring
    claims DOJ FARA, but its LDA_API_URL is the same endpoint rule_09 uses.
    """
    _seed([("RULE_09", "-1 days"), ("RULE_12", "-2 days")])
    assert _instruments() == ["senate-lda"]
    assert not _fires()


def test_edgar_rules_reading_different_forms_stay_separate():
    """RULE_06 (forms=4) and RULE_15 (forms=8-K) share a host, not a document."""
    assert rule10_instruments(["RULE_06", "RULE_15"]) == ["earnings", "insider"]


# ---------------------------------------------------------------------------
# D2 — threshold 3, and the recorded instrument count
# ---------------------------------------------------------------------------

def test_two_instruments_do_not_fire():
    _seed([("RULE_01B", "-1 days"), ("RULE_11", "-2 days")])
    assert len(_instruments()) == 2
    assert not _fires()


def test_threshold_is_three_instruments():
    assert RULE_10_MIN_INSTRUMENTS == 3
    assert r10.MIN_DISTINCT_INSTRUMENTS == RULE_10_MIN_INSTRUMENTS


def test_emission_records_the_instrument_count_on_alert_and_theme():
    _seed([("RULE_01B", "-1 days"), ("RULE_02", "-2 days"), ("RULE_11", "-3 days"),
           ("RULE_06", "-4 days")])
    r10.run(dry_run=False)

    conn = jpt_common.db_connection()
    alert = conn.execute(
        "SELECT tags, headline FROM alerts WHERE rule='RULE_10' AND ticker=?", (TICKER,)
    ).fetchone()
    theme = conn.execute(
        "SELECT title, supporting_rules, what_changed FROM themes WHERE primary_ticker=?",
        (TICKER,)
    ).fetchone()
    links = conn.execute("SELECT COUNT(*) FROM theme_signals").fetchone()[0]
    conn.close()

    assert alert is not None, "no RULE_10 alert emitted"
    tags = json.loads(alert["tags"])
    assert tags["instrument_count"] == 3
    assert tags["instruments"] == ["congressional", "contracts", "insider"]
    # four rules, three instruments — both recorded, and the headline says instruments
    assert tags["rule_count"] == 4
    assert "3 independent instruments" in alert["headline"]

    assert theme is not None, "no theme written"
    assert "3 instruments aligned" in theme["title"]
    assert r10.theme_instrument_count(theme["supporting_rules"]) == 3
    assert links >= 5          # the corroboration + its 4 contributing signals


def test_theme_instrument_count_derives_without_a_schema_change():
    """D2's count is derived from stored supporting_rules — no new column."""
    assert r10.theme_instrument_count(json.dumps(
        ["RULE_01B", "RULE_02", "RULE_CLUSTER"])) == 1
    assert r10.theme_instrument_count(json.dumps(
        ["RULE_01B", "RULE_11", "RULE_06"])) == 3
    assert r10.theme_instrument_count("not json") == 0

    conn = jpt_common.db_connection()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(themes)")}
    conn.close()
    assert "instrument_count" not in columns, (
        "a schema column appeared — the redesign was supposed to need no migration"
    )


# ---------------------------------------------------------------------------
# D4 — the 14-day window, on ingestion time
# ---------------------------------------------------------------------------

def test_window_default_is_fourteen_days():
    assert r10.CONVERGENCE_WINDOW_DAYS == 14
    args = r10.build_parser().parse_args([])
    assert args.window_days == 14


def test_three_instruments_spread_within_the_window_fire():
    _seed([("RULE_01B", "-1 days"), ("RULE_11", "-7 days"), ("RULE_06", "-13 days")])
    assert _fires(), "13 days apart should be inside a 14-day window"


def test_a_third_instrument_outside_the_window_is_excluded():
    _seed([("RULE_01B", "-1 days"), ("RULE_11", "-7 days"), ("RULE_06", "-15 days")])
    assert _instruments() == ["congressional", "contracts"], _instruments()
    assert not _fires(), "a signal 15 days old must fall outside the 14-day window"


def test_the_window_is_ingestion_time_not_event_time():
    """Explicit: the gate reads `created_at`, and that is a recorded decision."""
    import inspect
    src = inspect.getsource(r10._candidate_alerts)
    assert "created_at >=" in src
    assert "event_date" not in src
    assert "event-time" in r10.__doc__.lower() or "event_date" in r10.__doc__


# ---------------------------------------------------------------------------
# unchanged by this redesign: eligibility (that is D3)
# ---------------------------------------------------------------------------

def test_excluded_rules_are_still_excluded():
    """Pinned so any eligibility change is deliberate rather than incidental.

    THREE DIFFERENT REASONS, kept apart on purpose so a future reader does not
    conclude every excluded rule was judged noisy:

      noisy/self-referential  what the gate redesign excluded.
      retired                 RULE_12/13/14 — dead sources, excluded rather than merely
                              unmapped so they cannot become phantom instruments via
                              `.get(rule, rule)`. See tests/test_cleanup_pass.py.
      coverage collector      RULE_COLLECTOR — it gathers ticker NAMES for the real
                              instruments to cross-reference against. A collected name
                              is not "watch this", it is "this name exists", so it must
                              never open a corroboration. RULE_DISCOVERY is its retired
                              predecessor, kept excluded rather than deleted.
                              Social buzz must never be able to open a corroboration;
                              that is the whole reason the moat is worth anything. It
                              emits no alerts today, so this is a future-proof against
                              the same phantom trap, not a behaviour change (proven:
                              zero alerts carry this rule).
    """
    noisy_or_self_referential = {"RULE_07", "RULE_OSINT", "RULE_ANOMALY",
                                 "RULE_REDDIT", "RULE_10"}
    retired = {"RULE_12", "RULE_13", "RULE_14"}
    candidate_generators = {"RULE_DISCOVERY", "RULE_COLLECTOR"}
    # A FOURTH CATEGORY, added 2026-07-29: rules whose ticker comes from a
    # hardcoded region->basket table rather than from the event. RULE_ADSB reads
    # `REGION_TICKERS`, the same table that gave RULE_OSINT 8 distinct tickers
    # across 387 alerts. Unlike OSINT it was NOT already contained — it mapped to
    # the `flight` instrument, so a basket rule could complete a convergence on a
    # 5-minute cadence.
    #
    # RULE_08 joined this category 2026-07-29 and it is the one that MATTERED: live,
    # scheduled every 240 min, and actually counted as `fed-register`. Its
    # `SECTOR_MAP` fans a KEYWORD in a Federal Register title out into a basket
    # ("bank" -> JPM/BAC/WFC/GS), and a prior session split the composite ticker so
    # each element became its own matchable alert — which is what made the leg real.
    # Measured: ['RULE_01B','RULE_06','RULE_08'] fired; it no longer does. Human-
    # gated decision (it removes a counted leg from live convergences); `fed-register`
    # returns only via real issuer attribution. Its SECTOR_MAP and emission are
    # deliberately UNCHANGED — this is a gate exclusion, not a rule rewrite.
    basket_keyed = {"RULE_ADSB", "RULE_TELEGRAM_OSINT", "RULE_08"}
    assert RULE_10_EXCLUDED == (noisy_or_self_referential | retired
                                | candidate_generators | basket_keyed)


def test_noisy_rules_cannot_contribute_an_instrument():
    _seed([("RULE_01B", "-1 days"), ("RULE_07", "-2 days"), ("RULE_OSINT", "-3 days"),
           ("RULE_REDDIT", "-4 days"), ("RULE_ANOMALY", "-5 days")])
    assert _instruments() == ["congressional"]
    assert not _fires(), "excluded rules contributed to the gate"


def test_severity_floor_unchanged():
    """MEDIUM alerts are not corroboration inputs — pre-existing, not touched."""
    conn = jpt_common.db_connection()
    for rule in ("RULE_01B", "RULE_11", "RULE_06"):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at)
               VALUES (?, ?, 'MEDIUM', 'medium', datetime('now','-1 days'))""",
            (rule, TICKER),
        )
    conn.commit()
    conn.close()
    assert not _fires()


# ---------------------------------------------------------------------------
# the shared definition cannot drift from the gate
# ---------------------------------------------------------------------------

def test_rule10_is_valid_agrees_with_the_gate():
    """The brief and the evidence API gate citations on rule10_is_valid.

    If it still required 4 distinct RULES, every new 3-instrument corroboration
    would be silently dropped from the brief as "invalid".
    """
    assert rule10_is_valid(["RULE_01B", "RULE_11", "RULE_06"]) is True
    assert rule10_is_valid(["RULE_01B", "RULE_02", "RULE_CLUSTER"]) is False
    assert rule10_is_valid(["RULE_01B", "RULE_11"]) is False
    # noise cannot make up the numbers
    assert rule10_is_valid(["RULE_01B", "RULE_11", "RULE_07", "RULE_OSINT"]) is False


def test_rule_01_is_congressional_not_its_own_instrument():
    """`ingest_senate.py:278` emits RULE_01 (Senate PTR ingest) at HIGH/CRITICAL.

    It was missing from the map, so it fell through the "unmapped counts as its
    own instrument" fallback and became a SECOND congressional leg — two views of
    the same feed plus one other source would have cleared the gate.
    """
    assert rule10_instruments(["RULE_01"]) == ["congressional"]
    assert rule10_instruments(["RULE_01", "RULE_01B", "RULE_02", "RULE_CLUSTER"]) == [
        "congressional"]
    # two congressional feeds + contracts is 2 instruments, not 3
    assert rule10_is_valid(["RULE_01", "RULE_01B", "RULE_11"]) is False


def test_the_gate_is_not_defeatable_by_rule_name_casing():
    """Names are folded before matching the exclusion set and the map.

    Without folding, three casings of ONE rule counted as three instruments, and
    three lower-cased *excluded* noise rules cleared the gate outright.
    """
    assert rule10_instruments(["RULE_01B", "rule_01b", "Rule_01b"]) == ["congressional"]
    assert rule10_is_valid(["RULE_01B", "rule_01b", "Rule_01b"]) is False
    assert rule10_instruments(["rule_07", "rule_osint", "rule_reddit"]) == []
    assert rule10_is_valid(["rule_07", "rule_osint", "rule_reddit"]) is False
    # whitespace too
    assert rule10_instruments([" RULE_02 ", "\tRULE_CLUSTER\n"]) == ["congressional"]


def test_an_unmapped_rule_counts_as_its_own_instrument():
    """A new rule must not silently vanish from the count."""
    assert rule10_instruments(["RULE_01B", "RULE_11", "RULE_BRAND_NEW"]) == [
        "RULE_BRAND_NEW", "congressional", "contracts"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_a_BASKET_KEYED_rule_cannot_contribute_an_instrument():
    """A rule whose alert ticker comes from a hardcoded region basket must not be able
    to complete a convergence. `RULE_ADSB` reads `REGION_TICKERS[zone]`
    (`scripts/rule_adsb.py:127`); `RULE_TELEGRAM_OSINT` reads the same table
    (`scripts/rule_telegram_osint.py:109`) and writes `tickers[0]` as the ticker
    (`:141`). A lookup-table ticker is not evidence that a company is involved in
    anything.

    ⚠️ THIS ASSERTS A CLASS, SO IT ENUMERATES THE CLASS — AND CHECKS THE ENUMERATION
    AGAINST SOURCE. Its first version named the class and checked ONE member, and a
    verification pass immediately found the other: TELEGRAM_OSINT was unexcluded, hourly,
    and actually firing the gate —
    `['RULE_01B','RULE_06','RULE_TELEGRAM_OSINT']` produced three instruments where
    `['RULE_01B','RULE_06']` produced two. The sweep at the end is what stops a third
    instance arriving unnoticed.

    Each rule's mapping in `RULE_10_INSTRUMENTS` is deliberately LEFT in place: an
    eligible-but-UNMAPPED rule becomes its own instrument — the phantom trap that let
    RULE_12/13/14 count as three legs after being 'retired'.
    """
    import os
    import re
    from jpt_common import RULE_10_EXCLUDED, RULE_10_INSTRUMENTS, rule10_instruments

    for rule, instrument in (("RULE_ADSB", "flight"),
                             ("RULE_TELEGRAM_OSINT", "telegram")):
        assert rule10_instruments([rule]) == [], \
            f"{rule}'s ticker comes from a hardcoded basket and it can still be a gate leg"
        assert RULE_10_INSTRUMENTS.get(rule) == instrument, \
            f"{rule}'s mapping was deleted — an unmapped eligible rule becomes its own"
        assert rule10_instruments(["RULE_01B", "RULE_06", rule]) == \
            rule10_instruments(["RULE_01B", "RULE_06"]), \
            f"{rule} was smuggled in beside real legs"

    # THE COMPLETENESS HALF, derived from source rather than memory: every rule script
    # that reads a region ticker basket must be excluded.
    root = os.path.join(os.path.dirname(__file__), "..", "scripts")
    checked = []
    for fn in sorted(os.listdir(root)):
        if not fn.startswith("rule_") or not fn.endswith(".py"):
            continue
        src = open(os.path.join(root, fn)).read()
        if "REGION_TICKERS" not in src and "EVENT_TICKER_MAP" not in src:
            continue
        m = re.search(r"RULE\s*=\s*[\"']([A-Z_0-9]+)[\"']", src) or \
            re.search(r"rule\s*=\s*[\"']([A-Z_0-9]+)[\"']", src) or \
            re.search(r"[\"'](RULE_[A-Z_0-9]+)[\"']", src)
        assert m, f"{fn} reads a ticker basket but its rule name could not be determined"
        checked.append((fn, m.group(1)))
        assert m.group(1) in RULE_10_EXCLUDED, \
            f"{fn} keys its ticker on a hardcoded basket and {m.group(1)} is NOT excluded"
    assert len(checked) >= 3, f"the sweep found too few basket readers to be working: {checked}"
