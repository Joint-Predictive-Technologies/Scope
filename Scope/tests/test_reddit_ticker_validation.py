"""RULE_REDDIT must not store English words as tickers.

The subtlety: the universe check was NEVER the bug. `BACK`, `HERE`, `POST`, `MOVE`,
`BEAT` and `FIVE` are all GENUINE listed symbols (IMAC Holdings, Here Group, Post
Holdings...), so `t in known` passes them happily and ordinary sentences became
tickers. A blocklist of non-tickers cannot win — the collisions ARE tickers.

So the rule inverts: a bare common English word needs an explicit `$` cashtag,
however real the symbol is.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.rule_reddit import _COMMON_WORDS, _extract_tickers

KNOWN = {"BACK", "HERE", "POST", "MOVE", "BEAT", "FIVE", "GME", "NVDA", "CRWV", "PLTR"}


@pytest.mark.parametrize("word", ["BACK", "HERE", "POST", "MOVE", "BEAT", "FIVE"])
def test_bare_common_word_is_rejected_even_though_it_is_a_real_ticker(word):
    assert word in KNOWN, "fixture must model these as REAL symbols — that is the point"
    assert _extract_tickers(f"i saw the {word} of it", KNOWN) == []


def test_the_original_failing_sentence():
    assert _extract_tickers("i went BACK and read the POST HERE", KNOWN) == []


def test_cashtag_rescues_a_common_word():
    """$POST is an explicit claim about a ticker; 'post' is a noun."""
    got = _extract_tickers("$POST beat earnings and $BACK ripped", KNOWN)
    assert "POST" in got and "BACK" in got


def test_real_tickers_are_unaffected():
    assert _extract_tickers("NVDA and CRWV both ran", KNOWN) == ["NVDA", "CRWV"]
    assert _extract_tickers("PLTR up again", KNOWN) == ["PLTR"]


def test_curated_three_char_meme_tickers_still_work():
    assert _extract_tickers("GME to the moon", KNOWN) == ["GME"]


def test_unknown_symbols_are_still_rejected():
    """The universe check must still hold — this fix does not loosen it."""
    assert _extract_tickers("ZZZZ is mooning", KNOWN) == []
    assert _extract_tickers("$ZZZZ is mooning", KNOWN) == []


def test_the_words_reddit_actually_stored_are_all_covered():
    """Every bogus ticker found in the live alerts table."""
    for w in ("HERE", "MOVE", "BEAT", "FIVE", "BETA"):
        assert w in _COMMON_WORDS, f"{w} was stored as a ticker in prod and is still allowed"


def test_forward_only_no_history_rewrite():
    src = open(os.path.join(os.path.dirname(__file__), "..", "scripts",
                            "rule_reddit.py"), encoding="utf-8").read()
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    for stmt in ("UPDATE ", "DELETE "):
        assert stmt not in code.upper(), f"rule_reddit rewrites history via {stmt.strip()}"
