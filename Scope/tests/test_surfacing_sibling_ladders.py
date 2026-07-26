#!/usr/bin/env python3
"""
The secondary surfaces rank by `opportunity_score` too.

Two sibling surfaces still carried the hardcoded rule ladder that was removed
from the daily brief:

  * `scripts/send_digest.py` — the email digest: severity, then
    RULE_10 -> 1 / RULE_06 -> 2 / RULE_11 -> 3 / else -> 4.
  * `api/routers/chat.py` — the chat context block: severity, then
    RULE_10 -> 1 / RULE_06 -> 2 / else -> 3.

A third, `api/routers/digest.py`, was deliberately NOT changed — its rule
promotion is structural (one hardcoded query per rule, feeding fixed per-rule
keys that `api/static/digest.html:112-115` renders as fixed cards and that are
persisted in the `digests` table), so no ordering change can fix it. See the
session note.

Each fixture is crafted so the correct order is unambiguous: the high-opportunity
signal is given the *worst* value of every other field the old query sorted on —
HIGH not CRITICAL, the oldest row, the lowest id, and a rule in the old `ELSE`
bucket. If it still comes first, only `opportunity_score` can have put it there.
Each surface is then re-checked with the scores flipped, which proves the order
came from the seeded scores rather than from ambient data.

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


def test_chat_breaks_ties_by_recency_then_id():
    _seed_ties()
    from api.routers import chat
    context, _ = chat._fetch_context("what is happening", days=7)
    block = context.split("ALERTS (")[1]
    pos = {t: block.index(f"| {t} |") for t in ("NEWER", "OLDER", "TIEA", "TIEB")}
    assert pos["NEWER"] < pos["OLDER"]
    assert pos["TIEB"] < pos["TIEA"]


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
# surface 2 — api/routers/chat.py (Groq surface: SQL *and* prompt checked)
# ---------------------------------------------------------------------------

def _chat_context(message: str = "what is the strongest signal right now") -> str:
    from api.routers import chat
    context, _count = chat._fetch_context(message, days=7)
    return context


def _chat_order(context: str) -> list[str]:
    block = context.split("ALERTS (")[1]
    seen = []
    for line in block.splitlines():
        for ticker in EXPECTED:
            if f"| {ticker} |" in line and ticker not in seen:
                seen.append(ticker)
    return seen


def test_chat_context_orders_by_opportunity_score():
    _seed()
    assert _chat_order(_chat_context()) == EXPECTED


def test_chat_high_opportunity_name_beats_the_defence_names():
    _seed()
    order = _chat_order(_chat_context())
    assert order[0] == "ZWAR", order
    for prime in DEFENCE:
        assert order.index("ZWAR") < order.index(prime)


def test_chat_order_is_not_ambient():
    _seed()
    assert _chat_order(_chat_context())[0] == "ZWAR"
    _flip_scores()
    flipped = _chat_order(_chat_context())
    assert flipped[0] == "RTX", flipped
    assert flipped[-1] == "ZWAR", flipped


def test_chat_ticker_matched_branch_also_orders_by_score():
    """The with-tickers branch is a separate query — it must rank the same way."""
    _seed()
    order = _chat_order(_chat_context("tell me about ZWAR GLUE RTX LMT NOC"))
    assert order[0] == "ZWAR", order
    assert order.index("ZWAR") < order.index("RTX")


def test_chat_context_label_describes_the_real_ordering():
    """It said "ranked by severity" — a false description of its own input."""
    _seed()
    context = _chat_context()
    assert "ranked by opportunity score" in context
    assert "ranked by severity" not in context


def test_chat_prompt_layer_does_not_promote_a_rule_category():
    """The lesson from the brief: a promotion can live in the prompt, not the SQL."""
    from api.routers import chat

    for text in (chat.SYSTEM_PROMPT,):
        lowered = text.lower()
        assert "lead with" not in lowered
        assert "insider, contract" not in lowered
        assert "strongest individual signal" not in lowered


def test_chat_corroboration_block_is_untouched():
    """RULE_10 keeps its own labelled block — that is not a ranking promotion."""
    conn = jpt_common.db_connection()
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score, created_at)
           VALUES ('RULE_10', 'CORR', 'HIGH', 'four rules converged', 5.0, datetime('now','-1 hours'))"""
    )
    conn.commit()
    conn.close()
    _seed()

    context = _chat_context()
    assert "ACTIVE CORROBORATIONS" in context
    # and the ranked list still leads on score, not on RULE_10
    assert _chat_order(context)[0] == "ZWAR"


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
