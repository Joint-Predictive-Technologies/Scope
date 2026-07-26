#!/usr/bin/env python3
"""
The opportunity-score decomposition must not present its placeholder as a
measurement.

The bug: the fourth component was labelled `base win-rate 0.5` -> `+5.0`. No
caller anywhere passes `historical_win_rate` (jpt_common.py:856, :1028,
api/routers/warroom.py:121 all take the default), so that term is a hard constant
on every alert ever scored — it carries zero information about the signal, yet it
was displayed as one of four "reasons" for the number, in the same vocabulary as
the genuinely measured win rate on the member page (api/main.py:1162 ->
api/static/member.html:403).

These tests lock two things at once:
  1. the wording is honest and no longer says "win-rate";
  2. the VALUE and all the score maths are untouched. Test 2 is the important
     one — the whole point is that this was a labelling fix, and a labelling fix
     that moved a score would be a far worse bug than the one it repaired.

Runs under pytest or standalone:  python3 tests/test_winrate_placeholder_honesty.py
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                                    # noqa: E402
from jpt_common import (calculate_opportunity_score,                 # noqa: E402
                        opportunity_score_breakdown)

STATIC = pathlib.Path(__file__).resolve().parent.parent / "api" / "static"

# Snapshotted from the PRE-CHANGE implementation on `main`. Component order is
# [novelty, absorption, horizon, base weight].
BASELINE = [
    ((1.0, 0.0, "IMMEDIATE"), [40.0, -0.0, 20.0, 5.0], 65.0),
    ((0.5, 25.0, "SHORT"),    [20.0, -7.5, 17.0, 5.0], 34.5),
    ((0.15, 90.0, "LONG"),    [6.0, -27.0, 9.0, 5.0],   0.0),
    ((0.0, 0.0, "MEDIUM"),    [0.0, -0.0, 13.0, 5.0],  18.0),
    ((1.0, 100.0, None),      [40.0, -30.0, 14.0, 5.0], 29.0),
]


# ---------------------------------------------------------------------------
# 1. the wording is honest
# ---------------------------------------------------------------------------

def _placeholder_label(*args) -> str:
    return opportunity_score_breakdown(*args)["components"][3]["label"]


def test_placeholder_is_labelled_uncalibrated():
    label = _placeholder_label(1.0, 0.0, "IMMEDIATE")
    assert "uncalibrated" in label.lower()
    assert "placeholder" in label.lower()


def test_placeholder_no_longer_says_win_rate():
    """The naming collision with the measured figure is gone."""
    label = _placeholder_label(1.0, 0.0, "IMMEDIATE")
    lowered = label.lower()
    assert "win-rate" not in lowered
    assert "win rate" not in lowered
    assert "win" not in lowered          # no "winrate"/"winning" either


def test_no_component_label_mentions_win_across_many_inputs():
    for novelty, absorption, horizon in (
        (0.0, 0.0, "IMMEDIATE"), (1.0, 100.0, "LONG"), (0.37, 12.5, "SHORT"),
        (0.5, 50.0, "MEDIUM"), (0.9, 5.0, None), (0.15, 99.9, "bogus-horizon"),
    ):
        for component in opportunity_score_breakdown(
                novelty, absorption, horizon)["components"]:
            assert "win" not in component["label"].lower(), component


def test_placeholder_label_does_not_show_a_bare_number():
    """`base win-rate 0.5` invited reading 0.5 as a measured rate."""
    assert "0.5" not in _placeholder_label(1.0, 0.0, "IMMEDIATE")


# ---------------------------------------------------------------------------
# 2. nothing computational moved  <-- the claim that matters
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args,values,total", BASELINE)
def test_component_values_and_total_match_the_pre_change_baseline(args, values, total):
    breakdown = opportunity_score_breakdown(*args)
    assert [c["value"] for c in breakdown["components"]] == values
    assert breakdown["total"] == total


@pytest.mark.parametrize("args,values,total", BASELINE)
def test_calculate_opportunity_score_matches_the_pre_change_baseline(args, values, total):
    assert calculate_opportunity_score(*args) == total


def test_the_placeholder_term_is_still_worth_exactly_five():
    """Relabelled, not revalued."""
    for args, _, _ in BASELINE:
        assert opportunity_score_breakdown(*args)["components"][3]["value"] == 5.0


def test_breakdown_total_still_equals_the_sum_of_its_components():
    for args, _, total in BASELINE:
        breakdown = opportunity_score_breakdown(*args)
        summed = round(sum(c["value"] for c in breakdown["components"]), 1)
        assert breakdown["total"] == min(max(summed, 0.0), 100.0)


def test_an_explicit_win_rate_still_scales_the_term():
    """The parameter still works — only its label stopped lying."""
    assert opportunity_score_breakdown(
        1.0, 0.0, "IMMEDIATE", 1.0)["components"][3]["value"] == 10.0
    assert calculate_opportunity_score(1.0, 0.0, "IMMEDIATE", 1.0, 1.0) == 70.0


# ---------------------------------------------------------------------------
# 3. the served surface + immutability guard
# ---------------------------------------------------------------------------

_NOVELTY, _ABSORPTION, _HORIZON = 1.0, 0.0, "IMMEDIATE"
_OPPORTUNITY = 65.0


def _seed_cluster() -> None:
    conn = jpt_common.db_connection()
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, tags,
                               opportunity_score, novelty_score, absorption_pct,
                               time_horizon, evidence_confidence)
           VALUES ('RULE_CLUSTER', 'ZWAR', 'HIGH', 'ZWAR cluster',
                   '{"fingerprint": "CLUSTER::A+B::ZWAR::consensus_buy"}',
                   ?, ?, ?, ?, 50)""",
        (_OPPORTUNITY, _NOVELTY, _ABSORPTION, _HORIZON),
    )
    conn.commit()
    conn.close()


def test_served_decomposition_carries_the_honest_label():
    _seed_cluster()
    from api.routers import warroom

    detail = warroom.cluster_detail("A+B__ZWAR__consensus_buy")
    labels = [c["label"] for c in detail["decomposition"]["components"]]

    assert any("uncalibrated placeholder" in lbl for lbl in labels), labels
    assert not any("win" in lbl.lower() for lbl in labels), labels


def test_served_decomposition_total_still_equals_the_stored_score():
    """Immutability guard: relabelling must not disturb the stored score."""
    _seed_cluster()
    from api.routers import warroom

    detail = warroom.cluster_detail("A+B__ZWAR__consensus_buy")
    assert detail["decomposition"]["total"] == _OPPORTUNITY
    assert detail["alert"]["opportunity_score"] == _OPPORTUNITY


def test_relabelling_rewrote_no_stored_score():
    """Reading the breakdown must not UPDATE anything."""
    _seed_cluster()
    from api.routers import warroom

    conn = jpt_common.db_connection()
    before = conn.execute(
        "SELECT opportunity_score, novelty_score, absorption_pct, time_horizon "
        "FROM alerts WHERE ticker='ZWAR'").fetchone()
    conn.close()

    warroom.cluster_detail("A+B__ZWAR__consensus_buy")

    conn = jpt_common.db_connection()
    after = conn.execute(
        "SELECT opportunity_score, novelty_score, absorption_pct, time_horizon "
        "FROM alerts WHERE ticker='ZWAR'").fetchone()
    conn.close()
    assert tuple(before) == tuple(after)


# ---------------------------------------------------------------------------
# 4. the rendered pages
# ---------------------------------------------------------------------------

def test_no_static_page_renders_base_win_rate():
    for name in ("cluster.html", "thesis.html"):
        text = (STATIC / name).read_text()
        assert "base win-rate" not in text, name
        assert "base win_rate" not in text, name


@pytest.mark.parametrize("page", ["thesis.html", "cluster.html"])
def test_caption_no_longer_implies_a_measurement(page):
    """"never recomputed" alone read as a real rate frozen at detection.

    Both surfaces carry this caption. The first pass fixed only thesis.html and
    claimed "the caption is fixed" — the independent verifier found the identical
    string still live in cluster.html. Parametrised so the pair cannot drift apart
    again.
    """
    text = (STATIC / page).read_text()
    assert "detection-time value — never recomputed" in text      # immutability kept
    assert "fixed placeholder, not a measured win rate" in text   # disambiguated


def test_no_caption_states_immutability_without_the_disambiguation():
    """Catch a bare 'never recomputed' anywhere it is not followed by the caveat."""
    for page in STATIC.glob("*.html"):
        text = page.read_text()
        for idx in range(len(text)):
            idx = text.find("never recomputed", idx)
            if idx == -1:
                break
            tail = text[idx:idx + 200]
            assert "fixed placeholder, not a measured win rate" in tail, (
                f"{page.name}: immutability caption without the placeholder caveat")
            idx += 1


def test_thesis_js_duplicate_was_relabelled_too():
    """thesis.html recomputes the breakdown client-side; it must not drift."""
    text = (STATIC / "thesis.html").read_text()
    assert "base weight (uncalibrated placeholder)" in text


# ---------------------------------------------------------------------------
# 5. the genuinely measured win rate is untouched and now unambiguous
# ---------------------------------------------------------------------------

def test_measured_member_win_rate_still_computes():
    """api/main.py:1162 is a REAL figure — it must keep working."""
    conn = jpt_common.db_connection()
    conn.execute("INSERT INTO members (bioguide_id, full_name) VALUES ('T000001','Test Member')")
    # 4 backtested trades, 3 winners (>5% 30d) -> 75.0%
    for idx, ret in enumerate([12.0, 8.0, 6.0, -3.0], start=1):
        conn.execute(
            "INSERT INTO alerts (id, rule, ticker, severity, headline, member_id) "
            "VALUES (?, 'RULE_01B', ?, 'HIGH', 'seeded', 'T000001')",
            (900 + idx, f"TK{idx}"),
        )
        conn.execute(
            "INSERT INTO transactions (member_id, raw_ticker_string) VALUES ('T000001', ?)",
            (f"TK{idx}",),
        )
        conn.execute(
            "INSERT INTO backtest_results (alert_id, ticker, return_30d) VALUES (?, ?, ?)",
            (900 + idx, f"TK{idx}", ret),
        )
    conn.commit()
    conn.close()

    from api.main import member_signal_integrity
    out = member_signal_integrity("T000001")

    assert out["has_data"] is True
    assert out["backtested"] == 4
    assert out["winning_trades"] == 3
    assert out["win_rate"] == 75.0        # a real ratio, not a constant
    assert out["win_rate"] != 5.0         # and not the placeholder


def _code_without_docstring(func) -> str:
    """Source of `func` with its docstring stripped.

    Asserted on the CODE, not the prose: the docstring legitimately discusses the
    measured win rate in order to explain the collision it warns about, and an
    earlier version of this test failed on that mention alone.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    node = tree.body[0]
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        node.body = node.body[1:]          # drop the docstring
    return ast.unparse(tree)


def test_measured_win_rate_is_a_separate_code_path():
    """It must not be reachable from the placeholder, in either direction."""
    from api.main import member_signal_integrity

    integrity_code = _code_without_docstring(member_signal_integrity)
    assert "winning / backtested" in integrity_code        # genuinely computed
    assert "opportunity_score_breakdown" not in integrity_code

    breakdown_code = _code_without_docstring(opportunity_score_breakdown)
    assert "backtest_results" not in breakdown_code
    assert "member_signal_integrity" not in breakdown_code
    assert "alert_outcomes" not in breakdown_code


def test_docstring_documents_the_unit_hazard():
    """The measured figure is a 0-100 percent; this parameter is a 0-1 fraction.

    Flagged by the verifier: wiring the percent straight in multiplies the term by
    up to 100x and pins every score at the clamp, while the row still reads
    "uncalibrated placeholder". Cheapest mitigation is documenting the unit.
    """
    doc = opportunity_score_breakdown.__doc__ or ""
    assert "fraction" in doc and "percent" in doc
    assert "clamp" in doc


def test_passing_a_percent_shaped_value_would_hit_the_clamp():
    """Demonstrates the hazard the docstring now warns about (not a fix)."""
    as_fraction = calculate_opportunity_score(0.5, 50.0, "SHORT", 1.0, 0.6)
    as_percent = calculate_opportunity_score(0.5, 50.0, "SHORT", 1.0, 60.0)
    assert as_fraction < 100.0
    assert as_percent == 100.0          # clamped — every score would flatten to 100


def test_member_page_still_labels_the_measured_figure_win_rate():
    """The word "Win Rate" now belongs to the measured figure alone."""
    text = (STATIC / "member.html").read_text()
    assert "integrity.win_rate" in text
    assert "Win Rate" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
