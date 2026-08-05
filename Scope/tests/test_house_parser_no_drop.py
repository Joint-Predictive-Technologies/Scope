"""The House PTR parser must never lose a transaction to an unusable ticker.

🔴 DATA-LOSS class. `is_blocklisted` (`parse_house_pdfs.py:58`) is True for ANY
one-character ticker, and both call sites used to `continue` — discarding the whole
row: member, date, amount and asset name, not just the symbol. So every

    Ford Motor Company (F)      Visa Inc. (V)      AT&T Inc. (T)
    Citigroup (C)               General Electric (GE)

line vanished from `transactions` entirely, invisible to every rule, score and audit.

⭐ AND IT IS FAR WIDER THAN SINGLE LETTERS. `ST` — the ordinary House stock asset-type
bracket `[ST]` — is itself in the blocklist, and the fallback lifts it whenever a line
has no parenthesised ticker. So on HEAD, EVERY such line was discarded whole:
`Tesla Inc Common Stock [ST]`, `Alphabet Inc Class A [ST]`,
`Costco Wholesale Corporation [ST]`, `Microsoft Corporation Common Stock [ST]`.
Verified against HEAD. This is the strongest argument for the fix.

⚠️ Corroborating counts, stated as what they are: 207 single-letter rows exist, all
dated 2026-06-09 (Visa 56, Ford 31, AT&T 25, Agilent 20, Citigroup 17), and none among
the 441 House rows since. But the parser AND the blocklist landed in the same commit
(`a5538d8`, 2026-07-05) and those rows predate the parser in tracked history, so this is
a base rate, NOT a controlled before/after. Local working DB, untrusted; prod UNVERIFIED.

⭐ THE SAFETY POINT, AND ITS LIMIT. Dropping the SYMBOL is right: a bare "A" lifted out
of a company name is not Agilent. Dropping the HOLDING is not. A kept row carries
`raw_ticker_string=None`, so RULE_01B (`:209`), RULE_02 (`rule_02_cluster.py:33`) and
RULE_CLUSTER (`_gather:121-122`) all skip it — none can key on it.

⚠️ BUT `raw_ticker_string` IS NOT THE ONLY KEY CHANNEL, and the verifier proved it.
`ticker_id` is a second one, and RULE_CLUSTER's `COALESCE(tk.symbol, raw)` PREFERS it.
A row this fix newly keeps is later handed to the linker's fuzzy
`resolve_by_company_name`, which can attach a WRONG symbol:

    Agilent Technologies Inc (A) [ST]   HEAD: row dropped
                                        FIXED: kept, raw=None
                                        -> linker resolves ticker_id -> 'SINT' (SiNtx)

That key did not exist on HEAD because HEAD threw the row away. Mitigating, and all
real: `resolve_transactions` is human-gated and NEVER scheduled, RULE_01B and RULE_02
read only `raw_ticker_string` and stay inert, and preserving a holding beats destroying
it. But the honest claim is "carries no key-able RAW STRING", not "cannot become a
false key". The linker casefold fix is the thing that closes it.

⚠️ LAYOUT GOTCHA, and it matters. The House table puts the TYPE CODE BEFORE THE DATE:

    <asset description> [TYPE] P 01/15/2026 $1,001 - $15,000

The diagnosis's first fixture put the type after the date; its own control was dropped
too, so it proved nothing. Every test here asserts a control passes first.

⚠️ The pipe/tab path is ORDER-DEPENDENT, not "wholly non-functional": HEAD keeps every
row when the ticker column precedes the type column. It loses them only when the type
column comes first, because `TICKER_RE.fullmatch` then hits the single-letter type code.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import parse_house_pdfs as ph  # noqa: E402


def line(asset: str) -> str:
    """One House PTR table row. Type code precedes the date — see the gotcha above."""
    return f"{asset} P 01/15/2026 $1,001 - $15,000"


def parse_one(asset: str):
    out = ph.parse_table_like_lines(line(asset))
    return out[0] if out else None


# --------------------------------------------------------------------------
# The control must pass, or nothing below means anything
# --------------------------------------------------------------------------

@pytest.mark.parametrize("asset,symbol", [
    ("NVIDIA Corporation (NVDA) [ST]", "NVDA"),
    ("Apple Inc. (AAPL) [ST]", "AAPL"),
    ("Schlumberger N.V. - Common Stock (SLB) [ST]", "SLB"),
])
def test_CONTROL_a_normal_parenthesised_ticker_still_parses(asset, symbol):
    r = parse_one(asset)
    assert r is not None, "CONTROL FAILED — the harness is wrong, not the parser"
    assert r.raw_ticker_string == symbol
    assert r.transaction_date == "2026-01-15"
    assert r.amount_band == "$1,001 - $15,000"


# --------------------------------------------------------------------------
# A — the row survives
# --------------------------------------------------------------------------

@pytest.mark.parametrize("asset,rejected", [
    ("Ford Motor Company (F) [ST]", "F"),
    ("Visa Inc. Class A (V) [ST]", "V"),
    ("AT&T Inc. (T) [ST]", "T"),
    ("Citigroup Inc. (C) [ST]", "C"),
    ("General Electric Company (GE) [ST]", "GE"),
    ("US Treasury Bill [GS]", "US"),
])
def test_a_rejected_ticker_no_longer_costs_us_the_transaction(asset, rejected):
    """Each of these was DROPPED WHOLE before. The holding must survive."""
    r = parse_one(asset)
    assert r is not None, f"{rejected}: the transaction was discarded — DATA LOSS"
    assert r.transaction_date == "2026-01-15", "the date must survive"
    assert r.amount_band == "$1,001 - $15,000", "the amount must survive"
    assert r.raw_description, "the asset name must survive"


@pytest.mark.parametrize("asset", [
    "Ford Motor Company (F) [ST]",
    "Visa Inc. Class A (V) [ST]",
    "General Electric Company (GE) [ST]",
    "US Treasury Bill [GS]",
])
def test_a_kept_row_carries_NO_key_able_symbol(asset):
    """⭐ The load-bearing safety property.

    If a rejected ticker were left on the row, RULE_CLUSTER's validated keying would
    resolve a bare "A" to Agilent — a NEW false corroboration key created by the very
    fix meant to stop losing data.
    """
    r = parse_one(asset)
    assert r is not None
    assert r.raw_ticker_string is None, "a rejected symbol must not be carried"


def test_a_symbol_less_row_is_inert_to_the_REAL_rule_queries():
    """Run the rules' own fetch functions, not a paraphrase of their SQL.

    An earlier version of this test hand-built an in-memory table and asserted SQLite
    semantics. It never touched a rule, passed unchanged on HEAD, and mis-stated
    RULE_02's predicate. This inserts a genuine symbol-less row and asks the real code.
    """
    from jpt_common import db_connection
    import rule_02_cluster as r02
    import scripts.rule_cluster as rc

    with db_connection() as conn:
        conn.execute("INSERT INTO members (bioguide_id, full_name) VALUES ('Z000001','Z')")
        conn.execute(
            "INSERT INTO transactions (member_id, ticker_id, raw_ticker_string, "
            "raw_description, transaction_type, transaction_date) "
            "VALUES ('Z000001', NULL, NULL, 'Ford Motor Company (F)', 'purchase', "
            "date('now','-2 days'))")
        conn.commit()
        assert r02.fetch_transactions(conn, 90) == [], "RULE_02 must not see it"
        assert rc._gather(conn) == {}, "RULE_CLUSTER must not group it"


def test_the_carrier_is_None_and_never_an_empty_ish_STRING():
    """Why `None` rather than `""` or `"   "`.

    ⚠️ Rewritten after RULE_02's ticker-resolution fix landed on main. The original
    version asserted that RULE_02 CLUSTERS on `''` and `'   '` — true when this branch
    was written, because its predicate was a bare `IS NOT NULL` with no trim. That is
    no longer so: `resolve_key` now maps `''`, `'   '`, `'\t'` and `None` alike to
    `("", False)` and `fetch_transactions` drops them on `if not key`. Verified below
    rather than assumed.

    The choice of `None` stands regardless — it is the one carrier no consumer can
    misread, and it does not depend on another rule continuing to be careful.
    """
    import rule_02_cluster as r02
    for raw in ("", "   ", "  \t "):
        assert r02.resolve_key(raw, None, {"NVDA"}) == ("", False)

    from jpt_common import db_connection
    with db_connection() as conn:
        for i, raw in enumerate(("", "   ", "  ")):
            conn.execute("INSERT INTO members (bioguide_id, full_name) VALUES (?,?)",
                         (f"W{i:06d}", f"W{i}"))
            conn.execute(
                "INSERT INTO transactions (member_id, raw_ticker_string, transaction_type, "
                "transaction_date) VALUES (?,?,?,date('now','-2 days'))",
                (f"W{i:06d}", raw, "purchase"))
        conn.commit()
        assert r02.fetch_transactions(conn, 90) == [], \
            "RULE_02 now drops whitespace rows — the hazard this guarded against is closed"

    assert parse_one("Ford Motor Company (F) [ST]").raw_ticker_string is None


def test_before_and_after_no_row_is_discarded():
    """Seed every shape at once: dropped -> 0, and the control still parses."""
    assets = [
        "NVIDIA Corporation (NVDA) [ST]",      # control
        "Ford Motor Company (F) [ST]",
        "Visa Inc. Class A (V) [ST]",
        "AT&T Inc. (T) [ST]",
        "General Electric Company (GE) [ST]",
        "US Treasury Bill [GS]",
    ]
    text = "\n".join(line(a) for a in assets)
    parsed = ph.parse_table_like_lines(text)
    assert len(parsed) == len(assets), (
        f"{len(assets) - len(parsed)} transaction(s) discarded; expected 0"
    )
    assert sum(1 for p in parsed if p.raw_ticker_string == "NVDA") == 1
    assert sum(1 for p in parsed if p.raw_ticker_string is None) == 5


# --------------------------------------------------------------------------
# C — the description keeps its whole name
# --------------------------------------------------------------------------

@pytest.mark.parametrize("asset,must_contain", [
    ("Arlington, TX Municipal Bond [GS]", "TX"),
    ("MACOM Technology Solutions Holdings [ST]", "MACOM"),
    ("JP Morgan 3-Year Auto Callable Note [CS]", "JP"),
    ("Bucks County, PA Industrial Dev [GS]", "PA"),
])
def test_the_matched_token_is_no_longer_deleted_from_the_description(asset, must_contain):
    """`:408` used to strip the token it had just guessed at.

    That is the exact text `resolve_by_company_name` fuzzy-matches on, so the parser
    was destroying the linker's evidence: "MACOM Technology Solutions" arrived as
    "Technology Solutions", which cannot match MACOM.
    """
    r = parse_one(asset)
    assert r is not None
    assert must_contain in r.raw_description, (
        f"{must_contain!r} was stripped from the description: {r.raw_description!r}"
    )


def test_a_blocklisted_row_keeps_even_its_parenthetical():
    """With the ticker cleared first, no description surgery runs at all.

    Best possible input for the linker — it sees "Ford Motor Company (F)".
    """
    r = parse_one("Ford Motor Company (F) [ST]")
    assert r.raw_ticker_string is None
    assert "Ford Motor Company" in r.raw_description
    assert "(F)" in r.raw_description


def test_a_valid_tickers_redundant_parenthetical_is_still_stripped():
    """`:407` is kept — "(NVDA)" is not part of the company name."""
    r = parse_one("NVIDIA Corporation - Common Stock (NVDA) [ST]")
    assert r.raw_ticker_string == "NVDA"
    assert "(NVDA)" not in r.raw_description
    assert "NVIDIA Corporation" in r.raw_description


# --------------------------------------------------------------------------
# The second parse path
# --------------------------------------------------------------------------

def test_the_pipe_or_tab_path_also_keeps_the_row():
    """`parse_pipe_or_tab_rows` had the identical `continue`."""
    row = "01/15/2026 | P | F | Ford Motor Company | $1,001 - $15,000"
    out = ph.parse_pipe_or_tab_rows(row)
    assert len(out) == 1, "the pipe/tab path discarded the transaction"
    assert out[0].raw_ticker_string is None
    assert "Ford Motor Company" in (out[0].raw_description or "")


def test_the_pipe_or_tab_path_dropped_EVERY_row_before_this_fix():
    """🔴 Bigger than the diagnosis found: that path was wholly non-functional.

    `TICKER_RE.fullmatch` is tried against every cell in order and does NOT exclude
    `type_cell`, so the single-letter type code "P"/"S" is picked as the ticker before
    the real symbol is reached (`:490-494`). `is_blocklisted("P")` is True, so the old
    `continue` discarded the row — measured, HEAD keeps 0 of 3 perfectly ordinary rows.

    ⚠️ The symbol is still not EXTRACTED here; "NVDA" lands in the description instead.
    That extraction defect is pre-existing and deliberately out of scope (option B/D) —
    this fix only stops the row being thrown away, and the description now carries the
    symbol so the linker can still resolve it.
    """
    rows = "\n".join([
        "01/15/2026 | P | NVDA | NVIDIA Corporation | $1,001 - $15,000",
        "01/16/2026 | S | AAPL | Apple Inc. | $15,001 - $50,000",
        "01/17/2026 | P | MSFT | Microsoft Corp | $1,001 - $15,000",
    ])
    out = ph.parse_pipe_or_tab_rows(rows)
    assert len(out) == 3, f"expected all 3 rows kept, got {len(out)}"
    for r, sym in zip(out, ("NVDA", "AAPL", "MSFT")):
        assert r.raw_ticker_string is None, "no key-able symbol on this path yet"
        assert sym in (r.raw_description or ""), "the symbol survives in the description"
        assert r.amount_band and r.transaction_date


# --------------------------------------------------------------------------
# What must NOT have changed — B was not done here
# --------------------------------------------------------------------------

def test_the_blocklist_and_extraction_regex_are_untouched():
    """Single-letter RECOVERY (option B) is a separate session with its own measurement.

    This fix stops losing rows; it does not change what counts as a ticker.
    """
    assert ph.TICKER_BLOCKLIST == frozenset({"US", "SP", "NA", "N/A", "ST", "CA", "GE"})
    assert ph.is_blocklisted("F") is True, "still rejected as a SYMBOL — just not dropped"
    assert ph.is_blocklisted("GE") is True
    assert ph.is_blocklisted("NVDA") is False
    assert ph.TICKER_RE.pattern == r"\b[A-Z]{1,5}(?:\.[A-Z])?\b"
    assert ph.PARENTHESIZED_TICKER_RE.pattern == r"\(([A-Z]{1,5}(?:\.[A-Z])?)\)"


# --------------------------------------------------------------------------
# Escaping mutations the verifier found — the guard was protected by statement
# order alone, and no test pinned that order.
# --------------------------------------------------------------------------

def test_a_ticker_lifted_from_the_CONTINUATION_line_is_also_rejected():
    """🔴 E5. Long names wrap, so the parenthesised ticker lands on the NEXT PDF line
    (`parse_house_pdfs.py:383-390`). That lookup sits ABOVE the blocklist guard today,
    which is the only reason a wrapped `(F)` is caught.

    Nothing pinned that ordering and no other test here uses a two-line fixture, so
    moving the guard — or adding a second continuation lookup below it — would leak a
    live blocklisted symbol with every other test still green.
    """
    text = ("Ford Motor Company Common Stock P 01/15/2026 $1,001 - $15,000\n"
            "(F) [ST]")
    out = ph.parse_table_like_lines(text)
    assert len(out) == 1, "the wrapped row must still be KEPT"
    assert out[0].raw_ticker_string is None, \
        "a blocklisted ticker lifted from the continuation line must not survive"
    assert "Ford Motor Company" in out[0].raw_description


def test_a_wrapped_VALID_ticker_still_resolves_from_the_continuation_line():
    """Control for the above — the continuation lookup itself must keep working."""
    text = ("D.R. Horton, Inc. Common Stock P 01/15/2026 $1,001 - $15,000\n"
            "(DHI) [ST]")
    out = ph.parse_table_like_lines(text)
    assert len(out) == 1 and out[0].raw_ticker_string == "DHI", \
        "CONTROL FAILED — the continuation lookup is broken, not the guard"


@pytest.mark.parametrize("symbol", sorted(ph.TICKER_BLOCKLIST))
def test_every_blocklist_member_is_rejected_on_the_pipe_path_too(symbol):
    """🔴 E6. Exempting a single member (e.g. 'US') from the pipe-path guard escaped
    every test. Each member is now pinned on that path individually.

    Ticker column first, so the blocklisted value really is the one selected — with the
    type column first, `TICKER_RE` grabs the type code instead.
    """
    row = f"{symbol} | Some Asset Name | P | 01/15/2026 | $1,001 - $15,000"
    out = ph.parse_pipe_or_tab_rows(row)
    assert len(out) == 1, f"{symbol}: the pipe-path row was discarded"
    assert out[0].raw_ticker_string is None, f"{symbol}: leaked a blocklisted symbol"


# --------------------------------------------------------------------------
# The under-claim the verifier corrected upward: `[ST]` is itself blocklisted
# --------------------------------------------------------------------------

@pytest.mark.parametrize("asset", [
    "Tesla Inc Common Stock [ST]",
    "Alphabet Inc Class A [ST]",
    "Costco Wholesale Corporation [ST]",
    "Microsoft Corporation Common Stock [ST]",
    "Berkshire Hathaway Inc Class B [ST]",
])
def test_ordinary_stocks_with_no_parenthesised_ticker_are_recovered(asset):
    """⭐ The single biggest effect of this fix, and I under-claimed it.

    `ST` is in `TICKER_BLOCKLIST`, and `[ST]` is the ordinary House stock asset-type
    bracket. With no parenthesised ticker the fallback lifts `ST` from that bracket,
    the blocklist rejects it, and HEAD then discarded the row — so EVERY House line
    lacking a parenthesised ticker was lost, whatever its real symbol.
    """
    r = parse_one(asset)
    assert r is not None, "an ordinary stock line was discarded — this is the big one"
    assert r.raw_ticker_string is None
    assert r.raw_description and r.raw_description.split()[0] in asset


def test_the_ST_bracket_is_what_the_fallback_lifts():
    """Names the mechanism so the test above cannot be mistaken for a coincidence."""
    assert ph.TICKER_RE.findall("Tesla Inc Common Stock [ST]") == ["ST"]
    assert ph.is_blocklisted("ST") is True
