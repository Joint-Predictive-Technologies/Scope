#!/usr/bin/env python3
"""
Surfacing order: the brief and the clusters war room rank by `opportunity_score`.

The bug: `generate_brief.py` sorted by severity, then by a hardcoded rule
priority that pinned RULE_11 (federal contracts) at rank 2 in every window.
That feed is concentrated in a handful of defence primes, so the brief opened on
the same names every morning no matter what the scores said. `opportunity_score`
was not even selected by the query.

The fixtures below are crafted so the correct order is unambiguous: the
high-opportunity signal is deliberately given the *least* favourable value of
every OTHER field the old query sorted on — it is HIGH not CRITICAL, it is the
OLDEST row, its rule falls in the `ELSE` bucket, and its id is lowest. If it
comes first, it can only be because `opportunity_score` decided the order.

Runs under pytest or standalone:  python3 tests/test_surfacing_opportunity_sort.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import jpt_common                                   # noqa: E402
from scripts import generate_brief                  # noqa: E402


# ---------------------------------------------------------------------------
# the crafted mix
# ---------------------------------------------------------------------------

# (rule, ticker, severity, opportunity_score, created_at, headline)
# ZWAR is the one that must win, and it is stacked against itself on every
# other axis the old ORDER BY used.
CRAFTED = [
    # inserted first -> lowest id; oldest timestamp; HIGH not CRITICAL; ELSE rule
    ("RULE_08", "ZWAR", "HIGH",     91.0, "-40 hours", "Zephyr Waterworks rule change"),
    # the defence primes: CRITICAL, newest, and RULE_11 (old rank 2)
    ("RULE_11", "RTX",  "CRITICAL", 12.0, "-2 hours",  "RTX awarded $400M contract"),
    ("RULE_11", "LMT",  "CRITICAL", 11.0, "-3 hours",  "LMT awarded $300M contract"),
    ("RULE_11", "NOC",  "CRITICAL", 10.0, "-4 hours",  "NOC awarded $250M contract"),
    # a mid-scoring non-defence name, to pin the middle of the order
    ("RULE_06", "GLUE", "HIGH",     55.0, "-10 hours", "CFO of GLUE bought $800K"),
]


def _seed(rows=CRAFTED) -> None:
    conn = jpt_common.db_connection()
    for rule, ticker, severity, opp, age, headline in rows:
        conn.execute(
            """INSERT INTO alerts
                   (rule, ticker, severity, headline, opportunity_score, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now', ?))""",
            (rule, ticker, severity, headline, opp, age),
        )
    conn.commit()
    conn.close()


def _surfaced_tickers(days: float = 2) -> list[str]:
    conn = jpt_common.db_connection()
    data = generate_brief._gather_data(conn, days=days)
    conn.close()
    return [s["ticker"] for s in data["signals"]]


# ---------------------------------------------------------------------------
# the ordering proof
# ---------------------------------------------------------------------------

def test_brief_orders_strictly_by_opportunity_score():
    _seed()
    assert _surfaced_tickers() == ["ZWAR", "GLUE", "RTX", "LMT", "NOC"]


def test_high_opportunity_name_beats_every_rule_11_defence_name():
    """The headline claim, stated as its own assertion."""
    _seed()
    order = _surfaced_tickers()

    assert order[0] == "ZWAR", f"expected the high-opportunity name first, got {order}"
    for prime in ("RTX", "LMT", "NOC"):
        assert order.index("ZWAR") < order.index(prime)


def test_rule_11_is_no_longer_promoted_above_a_higher_scoring_signal():
    """Directly contradicts the old hardcode: RULE_11 sat at rank 2 always."""
    _seed()
    order = _surfaced_tickers()
    # every RULE_11 name sits below both non-RULE_11 names, purely on score
    assert order.index("GLUE") < min(order.index(p) for p in ("RTX", "LMT", "NOC"))


def test_severity_does_not_outrank_opportunity():
    """A CRITICAL low-opportunity row must not beat a HIGH high-opportunity row."""
    _seed()
    conn = jpt_common.db_connection()
    rows = {r["ticker"]: r["severity"] for r in conn.execute(
        "SELECT ticker, severity FROM alerts").fetchall()}
    conn.close()

    order = _surfaced_tickers()
    assert rows["ZWAR"] == "HIGH" and rows["RTX"] == "CRITICAL"
    assert order.index("ZWAR") < order.index("RTX")


def test_opportunity_score_is_available_on_surfaced_rows():
    """It was absent from the old SELECT entirely."""
    _seed()
    conn = jpt_common.db_connection()
    data = generate_brief._gather_data(conn, days=2)
    conn.close()

    top = data["signals"][0]
    assert top["opportunity_score"] == 91.0
    # scores are monotonically non-increasing down the surfaced list
    scores = [s["opportunity_score"] for s in data["signals"]]
    assert scores == sorted(scores, reverse=True), scores


def test_recency_breaks_ties_deterministically():
    """Equal scores -> newest first, then id desc. No ambiguity."""
    _seed([
        ("RULE_09", "OLDER", "HIGH", 70.0, "-30 hours", "older, same score"),
        ("RULE_09", "NEWER", "HIGH", 70.0, "-1 hours",  "newer, same score"),
    ])
    assert _surfaced_tickers() == ["NEWER", "OLDER"]


def test_ordering_comes_from_the_seeded_scores_not_ambient_data():
    """Flip only the scores; the order must invert. Nothing else changes."""
    _seed()
    assert _surfaced_tickers()[0] == "ZWAR"

    conn = jpt_common.db_connection()
    # hand RTX the top score and drop ZWAR to the bottom
    conn.execute("UPDATE alerts SET opportunity_score=99.0 WHERE ticker='RTX'")
    conn.execute("UPDATE alerts SET opportunity_score=1.0  WHERE ticker='ZWAR'")
    conn.commit()
    conn.close()

    flipped = _surfaced_tickers()
    assert flipped[0] == "RTX", flipped
    assert flipped[-1] == "ZWAR", flipped


def test_empty_window_surfaces_nothing():
    _seed([("RULE_11", "RTX", "CRITICAL", 12.0, "-40 days", "long past the window")])
    assert _surfaced_tickers() == []


# ---------------------------------------------------------------------------
# clusters war room — same ordering
# ---------------------------------------------------------------------------

def test_clusters_war_room_orders_by_opportunity_score():
    conn = jpt_common.db_connection()
    for ticker, opp, age in [("STALE_HIGH", 88.0, "-20 hours"),
                             ("FRESH_LOW", 9.0, "-1 hours"),
                             ("MID", 44.0, "-10 hours")]:
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline,
                                   opportunity_score, tags, created_at)
               VALUES ('RULE_CLUSTER', ?, 'HIGH', ?, ?, '{}', datetime('now', ?))""",
            (ticker, f"{ticker} cluster", opp, age),
        )
    conn.commit()
    conn.close()

    from api.routers import warroom
    cards = warroom.list_clusters(limit=100)

    # highest opportunity first, even though FRESH_LOW is the most recent
    assert [c["ticker"] for c in cards] == ["STALE_HIGH", "MID", "FRESH_LOW"]
    assert [c["opportunity_score"] for c in cards] == [88.0, 44.0, 9.0]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
