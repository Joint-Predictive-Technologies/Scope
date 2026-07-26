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
    # Identical score AND identical timestamp -> only `id DESC` can decide.
    # Relative, not absolute: an earlier version hardcoded '2026-07-26 09:00:00',
    # which the 48h window would have aged out on 2026-07-28, silently turning
    # this guard into a crash. Anything inside the window works.
    tie_at = conn.execute("SELECT datetime('now','-2 hours')").fetchone()[0]
    for ticker in ("TIEA", "TIEB"):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score, created_at)
               VALUES ('RULE_09', ?, 'HIGH', 'exact tie', 60.0, ?)""",
            (ticker, tie_at),
        )
    conn.commit()
    conn.close()


def test_send_digest_breaks_ties_by_recency_then_id():
    _seed_ties()
    order = _digest_tickers()
    assert order.index("NEWER") < order.index("OLDER")
    # TIEB was inserted second, so it holds the higher id
    assert order.index("TIEB") < order.index("TIEA")


def _code_without_docstring(func) -> str:
    """Source of `func` with its docstring removed.

    The docstring legitimately names RULE_10/RULE_11 to explain what was removed,
    so asserting on raw source would fail on the explanation rather than the code.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    node = tree.body[0]
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        node.body = node.body[1:]
    return ast.unparse(tree)


def test_send_digest_has_no_rule_ladder_at_all():
    """The branch's whole purpose — and nothing else asserted it.

    Mutation testing found that re-adding the exact ladder as a *secondary* key
    left all ten tests green, because no two fixture rows shared a score while
    differing by rule. Guarded two ways: structurally, and behaviourally with
    equal-score rows whose only difference is the rule.
    """
    from scripts import send_digest

    code = _code_without_docstring(send_digest._gather_top_signals)
    assert "CASE rule" not in code, "a rule ladder is back in the digest ordering"
    assert "RULE_10" not in code and "RULE_11" not in code, code

    # Behavioural: identical score AND timestamp, differing only by rule, with the
    # LADDER-FAVOURED row inserted FIRST so it holds the LOWER id. That makes the
    # two hypotheses disagree:
    #   no ladder  -> `id DESC` wins -> LADD_LOWPRIO (RULE_11, higher id) first
    #   ladder     -> rule priority  -> LADD_TOPPRIO (RULE_10 = rank 1) first
    # An earlier version inserted them the other way round, so both hypotheses
    # predicted the same winner and the mutant survived.
    conn = jpt_common.db_connection()
    at = conn.execute("SELECT datetime('now','-3 hours')").fetchone()[0]
    for rule, ticker in (("RULE_10", "LADDTOP"), ("RULE_11", "LADDLOW")):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score, created_at)
               VALUES (?, ?, 'HIGH', 'equal score, different rule', 50.0, ?)""",
            (rule, ticker, at),
        )
    conn.commit()
    conn.close()

    order = _digest_tickers()
    assert order.index("LADDLOW") < order.index("LADDTOP"), (
        f"RULE_10 outranked a higher-id row on equal score — rule priority is back: {order}")


def test_send_digest_keeps_its_severity_floor():
    """The floor is the entire reason ranking by score is safe here.

    `chat.py` was skipped precisely because it lacks this. Nothing tested that
    `send_digest` still has it — mutation testing showed removing the
    `WHERE severity IN ('CRITICAL','HIGH')` clause left all ten tests green.
    """
    import inspect

    from scripts import send_digest

    src = inspect.getsource(send_digest._gather_top_signals)
    assert "WHERE severity IN ('CRITICAL', 'HIGH')" in src, (
        "the severity floor is gone — ranking by opportunity_score alone lets the "
        "MEDIUM population evict CRITICALs; see the chat.py skip in this file"
    )

    # behavioural: a MEDIUM row scoring above everything must still not appear
    conn = jpt_common.db_connection()
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score, created_at)
           VALUES ('RULE_09','MEDONLY','MEDIUM','highest score but MEDIUM', 99.0,
                   datetime('now','-1 hours'))"""
    )
    conn.commit()
    conn.close()

    _seed()
    assert "MEDONLY" not in _digest_tickers()


def test_send_digest_null_score_is_coalesced_to_zero():
    """Exercises the COALESCE.

    Note: dropping `COALESCE` is *behaviourally equivalent for ordering* — SQLite
    already sorts NULL as the smallest value, so `opportunity_score DESC` puts it
    last either way (verified directly). What COALESCE does change is the value
    handed to the caller: `0` instead of `None`. That is what this asserts, rather
    than pretending an ordering test covers it.
    """
    conn = jpt_common.db_connection()
    # NULL must be written EXPLICITLY: `alerts.opportunity_score` is
    # `REAL DEFAULT 0.0`, so omitting the column yields 0.0 and never exercises
    # the COALESCE at all. An earlier version of this test did exactly that and
    # passed even with the COALESCE stripped out.
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score, created_at)
           VALUES ('RULE_09','NULLSC','HIGH','no score at all', NULL, datetime('now','-1 hours'))"""
    )
    conn.commit()
    stored = conn.execute("SELECT opportunity_score FROM alerts WHERE ticker='NULLSC'").fetchone()[0]
    conn.close()
    assert stored is None, "fixture failed to store a real NULL"

    from scripts import send_digest
    conn = jpt_common.db_connection()
    signals = send_digest._gather_top_signals(conn)
    conn.close()

    row = next(s for s in signals if s["ticker"] == "NULLSC")
    assert row["opportunity_score"] is not None, "COALESCE was dropped from the SELECT"
    assert row["opportunity_score"] == 0, row["opportunity_score"]


def test_send_digest_window_and_limit_are_unchanged():
    """Pins the 48h window and LIMIT 5 — both were silently mutable."""
    from scripts import send_digest

    code = _code_without_docstring(send_digest._gather_top_signals)
    assert "'-48 hours'" in code
    assert "LIMIT 5" in code

    conn = jpt_common.db_connection()
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score, created_at)
           VALUES ('RULE_09','TOOOLD','HIGH','outside the 48h window', 99.0,
                   datetime('now','-60 hours'))"""
    )
    # Enough in-window rows that LIMIT 5 and a larger LIMIT give different lengths —
    # with only five seeded rows the length assertion could not detect LIMIT 10.
    for n in range(4):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score, created_at)
               VALUES ('RULE_09', ?, 'HIGH', 'filler', ?, datetime('now','-5 hours'))""",
            (f"FILL{n}", 20.0 + n),
        )
    conn.commit()
    conn.close()

    _seed()                                     # 5 more in-window rows -> 9 total
    order = _digest_tickers()
    assert "TOOOLD" not in order, "the 48h window no longer bounds the digest"
    assert len(order) == 5, f"LIMIT 5 no longer holds: got {len(order)} -> {order}"


def test_send_digest_rendered_email_preserves_the_order():
    """The render layer must not reorder what the query ranked."""
    _seed()
    from scripts import send_digest
    conn = jpt_common.db_connection()
    signals = send_digest._gather_top_signals(conn)
    conn.close()

    html = send_digest._build_html(signals, "2026-07-26")
    positions = [html.index(t) for t in EXPECTED if t in html]
    # Without this length check the assertion below is vacuously true whenever the
    # fixture fails to seed — `[] == sorted([])`.
    assert len(positions) == len(EXPECTED), f"only {len(positions)} of {len(EXPECTED)} rendered"
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

    Measured read-only on the working DB over a 7-day window: ranking by score
    alone displaces most CRITICALs with MEDIUMs. With the window anchored to
    `max(created_at)` it was 21C+4H -> 19M+3H+3C (18 of 21 CRITICALs gone); with a
    date-only anchor, 12C+13H -> 14M+6H+5C (7 of 12). **The figures are
    anchor-dependent and come from the untrusted working DB — corroborative, not
    proven — but the direction holds under both windows.**

    Making it safe needs a severity floor, which is an *eligibility* change and out
    of scope for an ordering-only pass, so this surface was reverted for a human.
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

    # the constraint list was wider than the five paths above: no rule script, no
    # migration, no corroboration or gate logic either
    for path in changed:
        assert "rule_" not in os.path.basename(path), f"rule script touched: {path}"
        assert "migrat" not in path.lower(), f"migration touched: {path}"
        assert "corrobor" not in path.lower(), f"corroboration touched: {path}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
