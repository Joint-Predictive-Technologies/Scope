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
    """rules_with_age: [(rule, age_expression)] e.g. [("RULE_06", "-2 days")]."""
    conn = jpt_common.db_connection()
    for rule, age in rules_with_age:
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at)
               VALUES (?, ?, 'HIGH', ?, datetime('now', ?))""",
            (rule, ticker, f"{rule} fired on {ticker}", age),
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
    conn = jpt_common.db_connection()
    hours = window_hours if window_hours is not None else r10.CONVERGENCE_WINDOW_DAYS * 24
    rows = r10._candidate_alerts(conn, hours)
    conn.close()
    return rule10_instruments({r["rule"] for r in rows if r["ticker"] == TICKER})


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
    assert RULE_10_EXCLUDED == (noisy_or_self_referential | retired
                                | candidate_generators)


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
