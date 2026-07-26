#!/usr/bin/env python3
"""
Fixed per-rule slots: a rule must not be guaranteed a section it did not earn.

Two surfaces reserved a slot for a rule regardless of what fired, which no
`ORDER BY` change could fix because the promotion lived in the page skeleton and
the prompt schema rather than in a query:

  * `api/static/brief.html` rendered all seven cards unconditionally, so RULE_11
    held a permanent "Gov Contracts" card, and the generator's prompt mandated a
    `contracts` one-liner with filler text when nothing fired.
  * `api/routers/digest.py` builds one hardcoded query per rule feeding fixed keys
    that `api/static/digest.html` renders as fixed cards.

These were deliberately deferred from the ordering-only passes and changed later
with explicit approval, since they move a page layout and a prompt schema together.

Runs under pytest or standalone:
    python3 tests/test_fixed_slot_promotions.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import jpt_common                                   # noqa: E402
from scripts import generate_brief                  # noqa: E402

STATIC = pathlib.Path(__file__).resolve().parent.parent / "api" / "static"


# ---------------------------------------------------------------------------
# the brief page: cards follow content, not a fixed list
# ---------------------------------------------------------------------------

def test_brief_page_only_renders_sections_that_have_content():
    text = (STATIC / "brief.html").read_text()
    # the render path filters on a non-empty one_liner before mapping
    assert "SECTIONS.filter" in text
    assert "typeof sec.one_liner === 'string'" in text
    # and no longer substitutes filler for a missing section
    assert "'No data for this section.'" not in text


def test_brief_prompt_forbids_filler_sections():
    """A rule that did not fire must produce no section at all."""
    conn = jpt_common.db_connection()
    data = generate_brief._gather_data(conn, days=2)
    conn.close()
    prompt = generate_brief._build_prompt(data, "2026-07-26")

    assert "OMIT any \"sections\" key whose rule produced NO signal" in prompt
    assert "Emitting a section for a rule that did not fire is an error" in prompt
    # the old filler instructions are gone
    for filler in ("'No new contracts.' if absent",
                   "'No unusual insider activity.' if absent",
                   "'No new filings.' if absent"):
        assert filler not in prompt, filler


def test_brief_keeps_the_corroboration_absence_statement():
    """RULE_10's absence is itself information and must still be stated."""
    conn = jpt_common.db_connection()
    data = generate_brief._gather_data(conn, days=2)
    conn.close()
    prompt = generate_brief._build_prompt(data, "2026-07-26")
    assert "Only \"corroborations\" is different" in prompt
    assert "no cross-source corroborations" in prompt


def test_brief_prompt_and_page_agree_on_section_keys():
    """`prediction` vs `prediction_markets` was a silent key mismatch.

    The page rendered a permanent "No data" card for a key the generator never
    produced. Every key the prompt asks for must exist in the page's SECTIONS.
    """
    conn = jpt_common.db_connection()
    data = generate_brief._gather_data(conn, days=2)
    conn.close()
    prompt = generate_brief._build_prompt(data, "2026-07-26")

    schema = prompt.split('"sections": {')[1].split("\n  }")[0]
    prompt_keys = set(re.findall(r'"(\w+)":\s*\{\{?\s*"one_liner"', schema))
    page_keys = set(re.findall(r"key:\s*'(\w+)'", (STATIC / "brief.html").read_text()))

    assert prompt_keys, "could not parse the prompt's section keys"
    assert prompt_keys <= page_keys, f"prompt emits keys the page cannot render: {prompt_keys - page_keys}"
    assert "prediction_markets" in prompt_keys, "the prediction key still mismatches the page"


def test_brief_contracts_card_is_no_longer_guaranteed():
    """The headline of this change: RULE_11 has no reserved slot."""
    text = (STATIC / "brief.html").read_text()
    # 'contracts' is still a possible card...
    assert "key: 'contracts'" in text
    # ...but nothing renders it unconditionally
    assert "SECTIONS.map(" not in text, "cards are still rendered from the fixed list"


# ---------------------------------------------------------------------------
# the weekly digest: slots pick on merit, plus one rule-agnostic section
# ---------------------------------------------------------------------------

def _seed_week() -> None:
    """A high-opportunity non-defence signal plus lower-scoring rule-specific ones."""
    conn = jpt_common.db_connection()
    rows = [
        # rule,      ticker, sev,        opp,  age,        headline
        ("RULE_08", "ZWAR", "HIGH",     93.0, "-40 hours", "Zephyr rule change"),
        ("RULE_06", "OLDBIG", "HIGH",   30.0, "-100 hours", "older insider, low score"),
        ("RULE_06", "NEWTOP", "HIGH",   80.0, "-140 hours", "much older insider, TOP score"),
        ("RULE_02", "CLUS",  "HIGH",    40.0, "-50 hours", "congressional cluster"),
        ("RULE_11", "RTX",   "CRITICAL", 9.0, "-2 hours",  "RTX contract"),
    ]
    for rule, ticker, sev, opp, age, headline in rows:
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, detail,
                                   opportunity_score, created_at)
               VALUES (?, ?, ?, ?, 'd', ?, datetime('now', ?))""",
            (rule, ticker, sev, headline, opp, age),
        )
    conn.commit()
    conn.close()


def _digest_data() -> dict:
    from api.routers import digest
    conn = jpt_common.db_connection()
    data = digest._gather_data(conn)
    conn.close()
    return data


def test_digest_slots_pick_highest_opportunity_not_most_recent():
    """The code said "most recent"; the prompt asked for "most notable"."""
    _seed_week()
    data = _digest_data()
    # NEWTOP is the OLDEST insider row but the highest-scoring one
    assert data["biggest_insider"]["headline"] == "much older insider, TOP score"


def test_digest_has_a_rule_agnostic_top_section():
    """Every other section is bound to one rule by construction."""
    _seed_week()
    data = _digest_data()

    assert "top_signals" in data
    tickers = [s["ticker"] for s in data["top_signals"]]
    assert tickers[0] == "ZWAR", tickers          # highest score, RULE_08
    scores = [s["opportunity_score"] for s in data["top_signals"]]
    assert scores == sorted(scores, reverse=True), scores
    assert tickers.index("ZWAR") < tickers.index("RTX")


def test_digest_top_signals_order_is_not_ambient():
    _seed_week()
    assert _digest_data()["top_signals"][0]["ticker"] == "ZWAR"

    conn = jpt_common.db_connection()
    conn.execute("UPDATE alerts SET opportunity_score=99.0 WHERE ticker='RTX'")
    conn.execute("UPDATE alerts SET opportunity_score=1.0  WHERE ticker='ZWAR'")
    conn.commit()
    conn.close()

    tickers = [s["ticker"] for s in _digest_data()["top_signals"]]
    assert tickers[0] == "RTX", tickers
    assert tickers[-1] == "ZWAR", tickers


def test_digest_top_signals_respects_a_severity_floor():
    """Same precondition as the other score-ranked surfaces."""
    _seed_week()
    conn = jpt_common.db_connection()
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, detail,
                               opportunity_score, created_at)
           VALUES ('RULE_09','MEDHI','MEDIUM','top score but MEDIUM','d', 99.0,
                   datetime('now','-1 hours'))"""
    )
    conn.commit()
    conn.close()

    assert "MEDHI" not in [s["ticker"] for s in _digest_data()["top_signals"]]


def test_digest_prompt_leads_with_the_rule_agnostic_section():
    from api.routers import digest

    _seed_week()
    conn = jpt_common.db_connection()
    data = digest._gather_data(conn)
    conn.close()

    # rebuild the prompt exactly as _generate does, without calling Groq
    prompt = f"""Input data:\n{json.dumps(data, indent=2)}"""
    assert "top_signals" in prompt
    src = __import__("inspect").getsource(digest._generate)
    assert '"signal_of_week"' in src
    assert "lead with it" in src
    assert "OMIT any key whose input data is null or empty" in src


def test_digest_page_renders_in_declared_order_and_skips_omitted():
    text = (STATIC / "digest.html").read_text()
    assert "signal_of_week:" in text
    # deterministic order: iterate the meta map, not the model's key order
    assert "Object.keys(SECTION_META)" in text
    assert "Object.entries(parsed)" not in text
    # signal_of_week must be declared first
    keys = re.findall(r"^\s{4}(\w+):\s*\{ label:", text, re.M)
    assert keys[0] == "signal_of_week", keys


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
