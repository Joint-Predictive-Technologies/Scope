"""RULE_REDDIT must not store English words as tickers.

The subtlety: the universe check was NEVER the bug. `BACK`, `HERE`, `POST`, `MOVE`,
`BEAT` and `FIVE` are all GENUINE listed symbols (IMAC Holdings, Here Group, Post
Holdings...), so `t in known` passes them happily and ordinary sentences became
tickers. A blocklist of non-tickers cannot win — the collisions ARE tickers.

THE CASHTAG IS THE DISAMBIGUATOR. A bare token that is ordinary English prose is
rejected however real the symbol is; a cashtagged one is accepted however common the
word is. `$POST beat earnings` counts, `I saw this post` does not.

Two earlier attempts enlarged a hand-maintained blocklist and claimed to have solved
the class. They had not — the collisions ARE tickers, so enumeration never converges.
These tests therefore measure against the REAL universe (see `_real_universe`), never
a fixture, because the toy `KNOWN` set below is what let the earlier claims stand.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3

from scripts import rule_reddit as reddit
from scripts.rule_reddit import _COMMON_WORDS, _extract_tickers

_REAL_DB = os.path.join(os.path.dirname(__file__), "..", "data", "jpt.db")


def _real_universe() -> set[str]:
    """The REAL ~10,600-symbol `tickers` table, read-only.

    Deliberately NOT the disposable test DB and NOT a fixture. Every previous attempt at
    this rule was validated against a toy `KNOWN` set, which is exactly why a verifier
    measuring against the real universe found 414 uncovered symbols. Opened `mode=ro`
    so the suite cannot write to it.
    """
    if not os.path.exists(_REAL_DB):
        pytest.skip("real tickers universe unavailable — cannot verify against a fixture")
    conn = sqlite3.connect(f"file:{_REAL_DB}?mode=ro", uri=True)
    try:
        return {r[0].strip().upper() for r in
                conn.execute("SELECT symbol FROM tickers WHERE symbol IS NOT NULL")}
    finally:
        conn.close()

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


def test_no_originally_protected_word_became_extractable():
    """A rewrite once dropped HIGH from the list without anyone noticing.

    STRONGER THAN THE TEST IT REPLACES. The old version pinned the original word set
    against the blocklist and passed forever — including for words that were safe only
    because they were not listed symbols. HIGH was exactly that case, and the old test
    could never have caught the day it listed.

    A word is safe if EITHER the extractor rejects it as prose, OR it is not a symbol at
    all. This asserts the disjunction, and names any word that relies on the second leg —
    so if one of them ever lists, this fails with the reason.
    """
    ORIGINAL = {"YOLO", "CALL", "PUTS", "HIGH", "HOLD", "MOON", "BULL", "BEAR", "LONG",
                "GAIN", "LOSS", "FOMO", "HODL", "TLDR", "THETA", "GAMMA", "DELTA",
                "ELON", "MUSK", "GUYS", "THIS", "THAT", "WITH", "FROM", "HAVE", "WILL",
                "WHAT", "WHEN", "SOON", "OVER", "INTO", "JUST", "LIKE", "ALSO", "BEEN",
                "GOOD", "BEST", "HUGE", "NEXT", "MORE", "MOST", "SAME", "THAN", "THEN",
                "THEY", "WEEK", "YEAR", "TODAY", "CEO", "CFO", "IPO", "ETF", "USA",
                "GDP", "FED", "SEC", "FDA", "USD", "CPI", "TOP"}
    known = _real_universe()
    assert known, "no real ticker universe — this test must not pass vacuously"

    extractable = sorted(w for w in ORIGINAL
                         if w in known and w not in reddit._TICKER_WORDS)
    assert not extractable, (
        f"these were protected before and are now extractable bare: {extractable}. "
        "Either the word is ordinary prose (belongs in the generated list) or it is "
        "domain jargon (belongs in DOMAIN_JARGON).")

    # Informational, and the reason this test is stronger: these are safe ONLY because
    # they are not listed symbols today. If one lists, the assertion above starts firing.
    by_absence = sorted(w for w in ORIGINAL if w not in known)
    assert len(by_absence) < len(ORIGINAL), "sanity: not every word can be unlisted"


def test_the_recall_tradeoff_is_documented_not_hidden():
    """The fix BUYS precision WITH recall, and the module must say so.

    A bare mention of a common-word ticker is now missed by design. That is defensible
    for a gate-excluded discovery feed and indefensible if it is not written down, which
    is the failure this project keeps repeating — the previous version of this rule
    claimed to "invert" the blocklist when it had merely enlarged one.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "scripts",
                            "rule_reddit.py"), encoding="utf-8").read()
    assert "RECALL COST" in src, "the tradeoff must be stated in the module"
    assert "BY DESIGN" in src, "the miss must be labelled deliberate, not incidental"
    assert "$POST" in src, "the cashtag escape hatch should be shown concretely"
    # the honest framing: hand-maintenance is REDUCED, not eliminated
    assert "DOMAIN_JARGON" in src
    assert "414" in src, "the measured gap the old approach left should stay on record"


def test_the_domain_residue_stays_bounded():
    """DOMAIN_JARGON is the one hand-maintained set left. Keep it honest.

    Every entry must be a REAL listed symbol — otherwise `t in known` already rejects it
    and the entry is decoration that makes the set look load-bearing. That is precisely
    what the old ~125-word blocklist was: 15 of its entries (CFO, CPI, DELTA, ETF, FDA,
    GDP, IPO, USD, YOLO, ELON, MUSK, FOMO, GAMMA, THETA, TLDR) are not symbols at all.
    """
    known = _real_universe()
    assert known
    decorative = sorted(w for w in reddit.DOMAIN_JARGON if w not in known)
    assert not decorative, (
        f"DOMAIN_JARGON entries that are not listed symbols: {decorative}. "
        "`known` already rejects these; remove them.")
    assert len(reddit.DOMAIN_JARGON) <= 15, (
        f"DOMAIN_JARGON has grown to {len(reddit.DOMAIN_JARGON)} — if it keeps growing "
        "it is becoming the blocklist this change removed. Check whether the generated "
        "frequency list should cover it instead.")


# ── measured against the REAL universe, not a fixture ────────────────────────
#
# The previous two attempts were validated against the toy KNOWN set above and both
# claimed to have solved the class. A verifier measuring against the real 10,619-symbol
# universe found 414 uncovered symbols. Everything below uses the real table.

_ORDINARY_PROSE = [
    "I saw this post earlier and came back here to check",
    "Does anyone else feel the same way about this",
    "Ryan Cohen is on the board now",
    "China's New AI Model Triggers Fresh Tech Selloff",
    "OGs please help me understand the PSA connection",
    "The Acquisition Math, Dilution, and the Reflexivity of it all",
    "Form 425 for the big interview, the shares are owned now",
    "It only took a bit to pump the warrants up today",
    "BUY, HODL, DRS, Pure BOOK, SHOP, VOTE",
    "I have been a member since 2010 and I still hold",
    "This is a good week for the team, more news soon",
    "Just wait and see what happens next year",
    "My cost basis is too high to sell right now",
    "Edit: thanks for the gold kind stranger",
    "Well that was a wild ride, hope everyone is okay",
]

_GENUINE_MENTIONS = [
    ("NVDA is up 4% after earnings", ["NVDA"]),
    ("loading up on PLTR before the print", ["PLTR"]),
    ("$POST beat earnings this quarter", ["POST"]),
    ("$BACK is a real position for me", ["BACK"]),
    ("GME squeeze thesis still alive", ["GME"]),
    ("TCNNF has been consolidating for weeks", ["TCNNF"]),
]


def test_ordinary_prose_yields_no_tickers_against_the_real_universe():
    """Was 13 of 15 sentences producing a false ticker."""
    known = _real_universe()
    offenders = {s: _extract_tickers(s, known) for s in _ORDINARY_PROSE}
    offenders = {s: t for s, t in offenders.items() if t}
    assert not offenders, (
        f"{len(offenders)}/{len(_ORDINARY_PROSE)} ordinary sentences still yield "
        f"tickers: {offenders}")


def test_genuine_mentions_still_extract_including_cashtagged_common_words():
    """Precision must not have been bought by extracting nothing."""
    known = _real_universe()
    for text, expected in _GENUINE_MENTIONS:
        got = _extract_tickers(text, known)
        for e in expected:
            assert e in got, f"lost a genuine mention: {e!r} from {text!r} (got {got})"


def test_the_cashtag_is_what_flips_a_common_word():
    """The mechanism itself: same token, same universe, `$` is the only difference."""
    known = _real_universe()
    for word in ("POST", "BACK", "HERE", "LIVE", "TEAM"):
        if word not in known:
            continue
        assert _extract_tickers(f"talking about {word} today", known) == [], \
            f"bare {word} was extracted"
        assert word in _extract_tickers(f"talking about ${word} today", known), \
            f"cashtagged ${word} was NOT extracted"


def test_no_unambiguous_ticker_is_over_rejected():
    """The recall guard. A frequency list must not swallow real symbols."""
    known = _real_universe()
    for t in ("NVDA", "GME", "AMD", "PLTR", "TSLA", "AAPL", "MSFT", "LMT", "RTX",
              "XOM", "ABBV", "AMZN", "SPCX", "TCNNF", "CRWV"):
        if t not in known:
            continue
        assert t not in reddit._TICKER_WORDS, (
            f"{t} is treated as an English word — the frequency cutoff is too high")


def test_the_bare_extractable_common_words_are_measured_not_assumed():
    """The residual false-positive surface, stated as a number.

    Of the 4-5 char symbols reachable bare, how many are words a person would write in
    ordinary prose? This is the metric the old blocklist failed at 414. It is pinned so
    a regression in the word list shows up as a number, not a vibe.
    """
    import re
    known = _real_universe()
    reachable = {t for t in known if re.fullmatch(r"[A-Z]{4,5}", t)}
    still_bare = {t for t in reachable if t not in reddit._TICKER_WORDS}
    # everything the generated list DOES cover, i.e. the words prose actually uses
    covered = reachable - still_bare
    # MEASURED, not guessed: 149 of the 7,750 bare-reachable symbols (1.9%) are words
    # prose actually uses. An earlier draft asserted 250 here, from a 2-5 char count —
    # but BARE_TICKER_RE only reaches 4-5, so that figure was never the right one.
    assert len(covered) >= 140, (
        f"only {len(covered)} reachable symbols are treated as prose — the generated "
        "word list may have failed to load")
    # the honest residual: rare dictionary words remain extractable BY DESIGN
    assert still_bare, "sanity: not every symbol should require a cashtag"
