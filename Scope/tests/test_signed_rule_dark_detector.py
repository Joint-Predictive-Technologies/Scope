#!/usr/bin/env python3
"""
A SIGNED rule that contributes nothing to the gate must say so.

Runs under pytest:  pytest tests/test_signed_rule_dark_detector.py

`alert_corroborates` fails closed on a NULL `corroborates` — correctly, and SILENTLY. So a
signed rule whose verdict column is unpopulated stops being a corroborating leg with no
outward sign.

The hazard is the BACKLOG, not the emit path: both signed rules write `corroborates` when
they emit, so a dark rule self-heals as new alerts land. What goes dark is a corpus written
BEFORE the rule was signed. RULE_01B's is repaired by `remap_rule01b_direction.py`, which is
prepared-not-run — shipping that signing first takes it from 26 corroborating legs to 0,
invisibly.

The signing session shipped a "guard" for this that could not detect it — it asserted
synthetic dicts don't corroborate, which holds whether or not the population path has ever
run. This is the real detector.

⚠️ THE LOAD-BEARING DISTINCTION, AND WHAT MOST OF THIS FILE IS ABOUT:
    corroborates IS NULL  ->  "no verdict on record"      -> DARK, alarm
    corroborates = 0      ->  "we looked; not a leg"      -> HEALTHY, silent
A signed rule whose in-window candidates are all SALES has every verdict 0. Alarming on that
would fire loudest exactly when the signing is doing its job.
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO = os.path.join(os.path.dirname(__file__), "..")
_GATE_PATH = os.path.join(_REPO, "scripts", "rule_10_corroboration.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load(_GATE_PATH, "rule_10_dark")

import jpt_common  # noqa: E402

WINDOW_H = gate.CONVERGENCE_WINDOW_DAYS * 24


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    yield


def _seed(rule, verdicts, severity="HIGH", age_days=1, ticker="ABT"):
    """One alert per entry in `verdicts` (None | 0 | 1)."""
    conn = jpt_common.db_connection()
    for v in verdicts:
        conn.execute(
            "INSERT INTO alerts (rule, ticker, severity, headline, created_at, corroborates) "
            "VALUES (?,?,?,'x', datetime('now', ?), ?)",
            (rule, ticker, severity, f"-{age_days} days", v))
    conn.commit()
    conn.close()


def _run_detector():
    conn = jpt_common.db_connection()
    dark = gate._detect_dark_signed_rules(conn, WINDOW_H)
    rows = conn.execute(
        "SELECT source, events_scanned, notes FROM activity_log WHERE source=? ORDER BY id",
        (gate.SIGNED_RULE_DARK_SOURCE,)).fetchall()
    conn.close()
    return dark, rows


# A rule that is signed in this repo today, used wherever "a signed rule" is meant.
SIGNED = "RULE_06"


# ─────────────────────────────────────────────────────────────────────────────
# 1. THE ALARM
# ─────────────────────────────────────────────────────────────────────────────

def test_all_null_verdicts_raise_exactly_one_CRITICAL_row():
    _seed(SIGNED, [None, None, None])

    dark, rows = _run_detector()

    assert dark == [SIGNED]
    assert len(rows) == 1, f"expected one alarm row, got {len(rows)}"
    note = rows[0]["notes"]
    assert note.startswith("CRITICAL:signed_rule_dark")
    assert f"rule={SIGNED}" in note
    assert "candidates=3" in note and "null=3" in note
    assert rows[0]["events_scanned"] == 3


def test_two_dark_signed_rules_raise_one_row_each(monkeypatch):
    monkeypatch.setattr(gate, "SIGNED_RULES", frozenset({"RULE_06", "RULE_01B"}))
    _seed("RULE_06", [None, None])
    _seed("RULE_01B", [None])

    dark, rows = _run_detector()

    assert sorted(dark) == ["RULE_01B", "RULE_06"]
    assert len(rows) == 2
    assert {r["notes"].split("rule=")[1].split()[0] for r in rows} == {"RULE_01B", "RULE_06"}


def test_a_dark_rule_alarms_even_when_nothing_fires():
    """The placement trap: a dark rule is one that stops completing convergences.

    `run()` returns early when `not clusters`, so a detector placed near the normal exit
    would be silent in exactly the case it exists for. One alert cannot converge, so
    nothing fires here — the alarm must still be raised.
    """
    _seed(SIGNED, [None])

    conn = jpt_common.db_connection()
    fired = gate.find_corroborated_tickers(conn, WINDOW_H)
    conn.close()
    assert fired == {}, "fixture error: something fired, so this is not the early-return case"

    dark, rows = _run_detector()
    assert dark == [SIGNED] and len(rows) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. 🔴 THE PRECISION POINT — a healthy rule must stay silent
# ─────────────────────────────────────────────────────────────────────────────

def test_all_ZERO_verdicts_do_NOT_alarm():
    """Every in-window candidate is a disposal. The rule is WORKING, not dark.

    This is the single most important case in the file. Keying on the corroboration
    boolean instead of on NULL would alarm here — loudest precisely when the signing is
    doing its job.
    """
    _seed(SIGNED, [0, 0, 0, 0])

    dark, rows = _run_detector()

    assert dark == [], "a healthy all-sales signed rule was reported as dark"
    assert rows == []


@pytest.mark.parametrize("verdicts", [[1, 0], [1, None], [0, None], [1, 0, None]])
def test_any_populated_verdict_means_the_path_is_running(verdicts):
    """Partial population is backfill or rollover — a different, quieter signal."""
    _seed(SIGNED, verdicts)

    dark, rows = _run_detector()

    assert dark == [] and rows == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. THE BOUNDARIES — it uses the GATE's own window, floor and rule set
# ─────────────────────────────────────────────────────────────────────────────

def test_zero_in_window_candidates_does_not_alarm():
    """Nothing to be NULL. Silence here is not a miss."""
    dark, rows = _run_detector()
    assert dark == [] and rows == []


def test_an_unsigned_rule_with_all_null_does_not_alarm():
    """The blast radius is exactly SIGNED_RULES — an unsigned rule ignores the column."""
    _seed("RULE_02", [None, None, None])

    dark, rows = _run_detector()

    assert dark == [], "an UNSIGNED rule was reported dark; NULL is meaningless for it"
    assert rows == []


def test_out_of_window_nulls_do_not_alarm():
    _seed(SIGNED, [None, None], age_days=gate.CONVERGENCE_WINDOW_DAYS + 5)

    dark, rows = _run_detector()

    assert dark == [] and rows == []


def test_below_the_severity_floor_does_not_alarm():
    """The gate only considers HIGH/CRITICAL, so MEDIUM NULLs are not a dark signal."""
    _seed(SIGNED, [None, None], severity="MEDIUM")

    dark, rows = _run_detector()

    assert dark == [] and rows == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. THE NOTE MUST BE ACTIONABLE
# ─────────────────────────────────────────────────────────────────────────────

def test_the_note_distinguishes_the_two_causes():
    """Both shapes look identical in-window and need different responses.

    The labels name what is MEASURED, not what is inferred — "no verdict anywhere" is
    consistent with a broken population path AND with a rule that simply has not emitted
    since it was signed, and the query cannot tell those apart.
    """
    _seed(SIGNED, [None, None])
    _, rows = _run_detector()
    assert "cause=no_verdict_anywhere_in_db" in rows[0]["notes"]

    # the same rule, but with a populated verdict OUTSIDE the window -> it has run before
    _seed(SIGNED, [1], age_days=gate.CONVERGENCE_WINDOW_DAYS + 5)
    conn = jpt_common.db_connection()
    conn.execute("DELETE FROM activity_log")
    conn.commit()
    conn.close()

    _, rows = _run_detector()
    assert "cause=verdicts_exist_outside_candidate_set" in rows[0]["notes"], (
        "a rule with a historical verdict was reported as having none anywhere"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. MUTATION — prove the fixtures discriminate NULL from not-corroborating
# ─────────────────────────────────────────────────────────────────────────────

def test_keying_on_the_corroboration_boolean_breaks_the_all_zero_case(tmp_path):
    """Re-key the detector on "does not corroborate" and the healthy fixture goes red.

    If the all-zero test passed against this too, it would not be testing the distinction
    the whole detector is built around.
    """
    src = open(_GATE_PATH).read()
    target = 'nulls = [r for r in candidates if _row_value(r, "corroborates") is None]'
    assert target in src, (
        "the NULL predicate this mutation targets is no longer in the source — the "
        "mutation test has gone stale and must be updated"
    )
    mutant_path = tmp_path / "rule_10_mutant.py"
    mutant_path.write_text(src.replace(
        target,
        "nulls = [r for r in candidates if not alert_corroborates(r)[0]]", 1))
    mutant = _load(str(mutant_path), "rule_10_dark_mutant")

    _seed(SIGNED, [0, 0, 0])          # healthy: all disposals

    conn = jpt_common.db_connection()
    mutant_dark = mutant._detect_dark_signed_rules(conn, WINDOW_H)
    conn.close()

    assert mutant_dark == [SIGNED], (
        "the mutant did NOT alarm on the healthy all-zero fixture, so that fixture does "
        "not discriminate NULL from not-corroborating"
    )
    assert _run_detector()[0] == [], "the real detector must stay silent on all-zero"
