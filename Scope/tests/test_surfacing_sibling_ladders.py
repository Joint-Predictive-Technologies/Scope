#!/usr/bin/env python3
"""
The secondary surfaces rank by `opportunity_score` too.

Three surfaces still carried a hardcoded rule ladder. **One was fixed; two were
deliberately skipped**, and there are tests below locking each skip so a later
session cannot quietly "fix" the ordering and believe the promotion is gone.

  * **FIXED — `scripts/send_digest.py`** (the email digest): was severity, then
    RULE_10 -> 1 / RULE_06 -> 2 / RULE_11 -> 3 / else -> 4. It ranks by
    `opportunity_score` on top of an existing `WHERE severity IN
    ('CRITICAL','HIGH')` floor, exactly like the reference
    (`scripts/morning_brief.py:174-180`). No LLM in this path.

  * **SKIPPED — `api/routers/chat.py`**: the ladder was removed and then
    **reverted**, because chat.py has *no severity floor*. Measured on the working
    DB read-only: ranking by score alone dropped 18 of 21 CRITICALs out of the
    LIMIT-25 context, replaced by MEDIUMs. Adding a floor is an eligibility
    change, out of scope for an ordering-only pass. See the test below.

  * **SKIPPED — `api/routers/digest.py`**: its rule promotion is structural — one
    hardcoded query per rule, feeding fixed keys that
    `api/static/digest.html:112-115` renders as fixed cards and that the `digests`
    table persists — so no ordering change can fix it.

The fixture is crafted so the correct order is unambiguous: the high-opportunity
signal is given the *worst* value of every other field the old query sorted on —
HIGH not CRITICAL, the oldest row, the lowest id, and a rule in the old `ELSE`
bucket. If it still comes first, only `opportunity_score` can have put it there.
The order is then re-checked with the scores flipped, which proves it came from
the seeded scores rather than from ambient data.

Runs under pytest or standalone:
    python3 tests/test_surfacing_sibling_ladders.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                   # noqa: E402

# (rule, ticker, severity, opportunity_score, age, headline)
# ZWAR must win; it is handicapped on every other axis.
CRAFTED = [
    ("RULE_08", "ZWAR", "HIGH",     91.0, "-40 hours", "Zephyr Waterworks rule change"),
    ("RULE_06", "GLUE", "HIGH",     55.0, "-10 hours", "CFO of GLUE bought $800K"),
    ("RULE_11", "RTX",  "CRITICAL", 12.0, "-2 hours",  "RTX awarded $400M contract"),
    ("RULE_11", "LMT",  "CRITICAL", 11.0, "-3 hours",  "LMT awarded $300M contract"),
    ("RULE_11", "NOC",  "CRITICAL", 10.0, "-4 hours",  "NOC awarded $250M contract"),
]

EXPECTED = ["ZWAR", "GLUE", "RTX", "LMT", "NOC"]
DEFENCE = ("RTX", "LMT", "NOC")


def _seed(rows=CRAFTED) -> None:
    conn = jpt_common.db_connection()
    for rule, ticker, severity, opp, age, headline in rows:
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, detail,
                                   opportunity_score, created_at)
               VALUES (?, ?, ?, ?, 'seeded detail', ?, datetime('now', ?))""",
            (rule, ticker, severity, headline, opp, age),
        )
    conn.commit()
    conn.close()


def _flip_scores() -> None:
    """Give the defence names the top scores and sink ZWAR. Nothing else changes."""
    conn = jpt_common.db_connection()
    conn.execute("UPDATE alerts SET opportunity_score=99.0 WHERE ticker='RTX'")
    conn.execute("UPDATE alerts SET opportunity_score=98.0 WHERE ticker='LMT'")
    conn.execute("UPDATE alerts SET opportunity_score=97.0 WHERE ticker='NOC'")
    conn.execute("UPDATE alerts SET opportunity_score=1.0  WHERE ticker='ZWAR'")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# surface 1 — scripts/send_digest.py (email; no LLM in the path)
# ---------------------------------------------------------------------------

def _digest_tickers() -> list[str]:
    from scripts import send_digest
    conn = jpt_common.db_connection()
    signals = send_digest._gather_top_signals(conn)
    conn.close()
    return [s["ticker"] for s in signals]


def test_send_digest_orders_by_opportunity_score():
    _seed()
    assert _digest_tickers() == EXPECTED


def test_send_digest_high_opportunity_name_beats_the_defence_names():
    _seed()
    order = _digest_tickers()
    assert order[0] == "ZWAR", order
    for prime in DEFENCE:
        assert order.index("ZWAR") < order.index(prime)


def test_send_digest_severity_does_not_outrank_opportunity():
    """HIGH/91 must beat CRITICAL/12."""
    _seed()
    order = _digest_tickers()
    assert order.index("ZWAR") < order.index("RTX")


def test_send_digest_order_is_not_ambient():
    _seed()
    assert _digest_tickers()[0] == "ZWAR"
    _flip_scores()
    flipped = _digest_tickers()
    assert flipped[0] == "RTX", flipped
    assert flipped[-1] == "ZWAR", flipped


def test_send_digest_carries_the_score_on_its_rows():
    _seed()
    from scripts import send_digest
    conn = jpt_common.db_connection()
    signals = send_digest._gather_top_signals(conn)
    conn.close()
    assert signals[0]["opportunity_score"] == 91.0
    scores = [s["opportunity_score"] for s in signals]
    assert scores == sorted(scores, reverse=True), scores


def _seed_ties() -> None:
    """Equal scores: two at different times, two at the SAME timestamp.

    Added after mutation testing showed that removing the `datetime(created_at)
    DESC, id DESC` tiebreak left every other test still passing — the ordering
    was asserted but its determinism was not.
    """
    conn = jpt_common.db_connection()
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score, created_at)
           VALUES ('RULE_09','NEWER','HIGH','same score, newer', 70.0, datetime('now','-1 hours'))"""
    )
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score, created_at)
           VALUES ('RULE_09','OLDER','HIGH','same score, older', 70.0, datetime('now','-30 hours'))"""
    )
    # identical score AND identical timestamp -> only `id DESC` can decide
    for ticker in ("TIEA", "TIEB"):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score, created_at)
               VALUES ('RULE_09', ?, 'HIGH', 'exact tie', 60.0, '2026-07-26 09:00:00')""",
            (ticker,),
        )
    conn.commit()
    conn.close()


def test_send_digest_breaks_ties_by_recency_then_id():
    _seed_ties()
    order = _digest_tickers()
    assert order.index("NEWER") < order.index("OLDER")
    # TIEB was inserted second, so it holds the higher id
    assert order.index("TIEB") < order.index("TIEA")


def test_send_digest_rendered_email_preserves_the_order():
    """The render layer must not reorder what the query ranked."""
    _seed()
    from scripts import send_digest
    conn = jpt_common.db_connection()
    signals = send_digest._gather_top_signals(conn)
    conn.close()

    html = send_digest._build_html(signals, "2026-07-26")
    positions = [html.index(t) for t in EXPECTED if t in html]
    assert positions == sorted(positions), "email HTML reordered the signals"


# ---------------------------------------------------------------------------
# surface 2 — api/routers/chat.py: SKIPPED after measurement, locked as such
# ---------------------------------------------------------------------------

def test_chat_router_was_left_untouched_after_measurement():
    """Guards the skip decision. chat.py has NO severity floor.

    The reference pattern (scripts/morning_brief.py:174-176) and send_digest both
    rank by opportunity_score *on top of* `WHERE severity IN ('HIGH','CRITICAL')`.
    chat.py has no severity filter at all, so ranking purely by score lets the
    MEDIUM population — 2,202 rows against 179 CRITICAL, with overlapping score
    ranges (MEDIUM max 65.0 > CRITICAL max 62.0) — flood the LIMIT 25 window.

    Measured read-only on the working DB over a 7-day window: the old ordering
    surfaced 21 CRITICAL + 4 HIGH; ranking by score alone surfaced 19 MEDIUM +
    3 HIGH + 3 CRITICAL, i.e. 18 of 21 CRITICALs dropped out of the chat context
    entirely. Making it safe needs a severity floor, which is an *eligibility*
    change and out of scope for an ordering-only pass — so this surface was
    reverted and left for a human.
    """
    import inspect

    from api.routers import chat

    src = inspect.getsource(chat._fetch_context)
    assert "CASE rule WHEN 'RULE_10' THEN 1 WHEN 'RULE_06' THEN 2 ELSE 3 END" in src, (
        "chat.py's ladder is gone — if that was deliberate, confirm a severity "
        "floor was added too; see SESSION-2026-07-26-surfacing-sibling-ladders.md"
    )
    assert "WHERE severity IN" not in src, (
        "chat.py gained a severity filter — that is an eligibility change; "
        "re-measure the CRITICAL displacement before ranking by score"
    )


# ---------------------------------------------------------------------------
# surface 3 — api/routers/digest.py: deliberately SKIPPED, locked as such
# ---------------------------------------------------------------------------

def test_digest_router_was_left_untouched_on_purpose():
    """Guards the skip decision: digest.py's promotion is structural.

    Its rule categories are not an ORDER BY — they are one hardcoded query per
    rule feeding fixed keys that the page renders as fixed cards and that are
    persisted in `digests`. Ordering cannot fix that; changing it means moving the
    prompt schema and digest.html together, which is a supervised change. If
    someone later "fixes" the ordering here, this test should make them read the
    session note first.
    """
    import inspect

    from api.routers import digest

    src = inspect.getsource(digest._gather_data)
    assert "rule = 'RULE_06'" in src
    assert "rule = 'RULE_02'" in src
    assert "rule = 'RULE_08'" in src
    assert "opportunity_score" not in src, (
        "digest.py was changed — its per-rule slots make ordering insufficient; "
        "see SESSION-2026-07-26-surfacing-sibling-ladders.md before proceeding"
    )


# ---------------------------------------------------------------------------
# excluded files must stay untouched by this branch
# ---------------------------------------------------------------------------

def test_excluded_surfaces_are_not_modified_here():
    """generate_brief.py and warroom.py belong to another unmerged branch."""
    import subprocess

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    changed = subprocess.run(
        ["git", "diff", "--name-only", "main"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.split()

    for forbidden in (
        "Scope/scripts/generate_brief.py",
        "Scope/api/routers/warroom.py",
        "Scope/api/static/brief.html",
        "Scope/jpt_common.py",
        "Scope/tests/conftest.py",
    ):
        assert forbidden not in changed, f"{forbidden} must not change on this branch"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
