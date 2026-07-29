#!/usr/bin/env python3
"""COLLECTOR — reddit as COVERAGE, not signal.

A wide net that drops low-cap ticker names into Scope's universe so the real instruments
— insider clusters, congressional trades, contracts — have names to cross-reference
against. That is the entire purpose.

⚠️ A COLLECTED TICKER IS NOT "WATCH THIS". IT IS MERELY "THIS NAME EXISTS."
It contributes literally nothing on its own. It produces no alert, no signal, no score,
no ranking, and no gate instrument. Cross-referencing fires only when a REAL instrument
independently lands on the name — never because reddit collected it. If anything
downstream ever reads this list as evidence that reddit "found" something, that is a
defect in the reader, and this docstring is the specification it violated.

WHAT THIS REPLACED. An earlier version of this module computed a per-ticker mention
baseline and flagged "unusual traction" into a watch pool. That framing is gone: it
implied reddit could tell you something was interesting, which it cannot, and the
deviation math was the part with no ground truth. What survives is the infrastructure
the collector actually needs — cashtag extraction, market-cap classification, the
per-mention table — with the inference removed. See
`vault/Scope/02_Sessions/SESSION-2026-07-27-reddit-discovery.md` for why the deviation
approach was abandoned (its guard opened at the maximum of its own false-positive curve).

THREE GATES, ALL BLUNT ON PURPOSE. None of them ranks anything.

  1. CASHTAG REQUIRED. `$MOBX` counts, a bare "MOBX" does not. Bare tokens are ambiguous
     prose that happen to match a symbol — collecting on them would refill the universe
     with the English words the extraction fix spent a session removing.
  2. ENGAGEMENT FLOOR. A pulse check, not a quality bar: it exists to drop bot posts and
     dead threads, nothing more. A post with zero upvotes AND zero comments is not a
     post anyone saw.
  3. NOT A CONFIRMED LARGE-CAP. $AAPL does not need discovering. Note the asymmetry:
     only a CONFIRMED large cap is excluded — an unknown cap is COLLECTED AND FLAGGED,
     because the cost of a missing name in a cross-reference universe is higher than the
     cost of an extra one. This is the opposite of the fail-closed direction the old
     watch pool used, and deliberately so: that list was surfaced to a human, this one
     is a lookup table.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection, normalize_ticker, record_activity  # noqa: E402

RULE = "RULE_COLLECTOR"

# ── Collection gates — blunt, tunable, and NOT a ranking ─────────────────────
# The engagement floor is deliberately crude. It is a pulse check to drop bots and dead
# threads; it is not a quality signal and must never become one. Raising it does not make
# the universe "better", it makes it smaller.
MIN_SCORE = 3               # upvotes
MIN_COMMENTS = 1            # at least one human replied
# ...and a post must clear BOTH? No — either is enough evidence someone saw it.
REQUIRE_BOTH = False

# Only a CONFIRMED large cap is excluded. $10B is the conventional large-cap floor.
# Everything below it, and everything unpriceable, is collected.
LARGE_CAP_MIN = 10_000_000_000
CAP_TTL_DAYS = 30           # market caps move slowly; do not re-fetch per run

# ── PLAUSIBILITY BOUNDS — both discovered live, in production ────────────────
#
# `classify_cap` checked only a CEILING, so any MIS-SCALED cap sailed through as "small"
# and the small-cap gate was simply wrong. Two real cases, both from `ticker_meta` on main:
#
#   CLBK  $1,085          a savings institution with 15 insiders and $4.5M of buys
#   TSM   $10,349,411,116,118   ~5x its real value
#
# A listed company is not worth $1,085, and none is worth $10T. These are backstops for
# arithmetic that has already gone wrong — the root causes are fixed in `market_cap`
# below, and these catch the next mis-scale nobody has seen yet.
MIN_PLAUSIBLE_CAP = 1_000_000            # $1M. ⚠️ THIS FLOOR IS NOT FREE — an earlier
                                         # comment here claimed "genuine nano-caps sit
                                         # well above this", and that is FALSE. Measured
                                         # live: real listed securities with FRESH share
                                         # facts price below it — ADTX $2,448, SXTC
                                         # $52,311, RUBI $840,392 (re-derived at today's
                                         # closes; these drift with price) — so the floor
                                         # does reject real things.
                                         #
                                         # It is acceptable anyway because of the
                                         # asymmetry the whole module is built on: unknown
                                         # is COLLECTED (flagged), so the collector loses
                                         # no coverage — it loses only a confident
                                         # "small" LABEL on a number it cannot stand
                                         # behind. The fail-closed cluster surface does
                                         # drop them, which is the correct direction there:
                                         # a sub-$1M cap on a surface a human reads is the
                                         # CLBK case verbatim.
                                         #
                                         # The old anchor cited MOBX at $5.6M. Do not
                                         # reuse it as a FLOOR anchor — MOBX's newest of
                                         # TEN share facts is 2,778,912 at end=2023-11-14
                                         # (CIK 0001855467, "MOBIX LABS, INC", verified by
                                         # the name the CIK returns), so that figure is a
                                         # STALE product, not a current micro-cap. The
                                         # arithmetic was always right; the input was old.
                                         # See MAX_SHARES_AGE_DAYS.
MAX_PLAUSIBLE_CAP = 10_000_000_000_000   # $10T. The largest real company is ~$5T
                                         # (AAPL $4.9T, NVDA $4.8T), so this is 2x
                                         # headroom and flags order-of-magnitude errors.
                                         # ⚠️ TSM's bad value was $10.35T — only just
                                         # over. The ceiling is a BACKSTOP; the real
                                         # protection for that class is the
                                         # foreign-issuer check in `market_cap`.
MAX_SHARES_AGE_DAYS = 540                # 18 months.
                                         # ⚠️ POPULATION MATTERS, AND THE FIRST VERSION OF
                                         # THIS COMMENT NAMED THE WRONG ONE. It claimed
                                         # "100% of 5,109 listed filers within 365 days,
                                         # stalest 343, drops ZERO real filers" — measured
                                         # over `xbrl/frames` for recent quarters, a sample
                                         # that BY CONSTRUCTION contains only filers who
                                         # reported recently, so it could not contain the
                                         # staleness it was meant to bound.
                                         # POPULATION: every CIK in SEC's
                                         # `company_tickers.json`, queried per-CIK through
                                         # the same `companyconcept` endpoint the code
                                         # uses. On that population the cover-page tag is
                                         # years stale for hundreds of real filers
                                         # (Comcast end=2009-12-31). So the threshold is
                                         # NOT load-bearing on its own — the fallback
                                         # concepts in `_SHARE_CONCEPTS` and the
                                         # "only fail closed on a SMALL cap" rule in
                                         # `market_cap` are what keep real large-caps out
                                         # of `unknown`. See the session note for the
                                         # full-census distribution.
MIN_PLAUSIBLE_SHARES = 100_000           # CLBK reported 100. A listed company does not
                                         # have a hundred shares outstanding.
# A FAILED lookup is cached too, on a shorter clock. Unknowns were not cached at all, so
# every still-unknown row re-hit SEC AND Yahoo on EVERY run — measured at 4 SEC + 4 Yahoo
# calls per unpriceable row per day, growing without bound, and flatly contradicting the
# scheduling rationale ("at most one lookup per NEW ticker"). Shorter than the success
# TTL because an unpriceable name is often newly listed and worth retrying sooner.
UNKNOWN_TTL_DAYS = 7

# Mentions recorded before the extraction fix are ~45% English-word false positives
# (BACK, HERE, POST...). Collecting them would fill the universe with words.
COLLECTION_EPOCH = "2026-07-27 00:00:00"

_SEC_HEADERS = {"User-Agent": "Scope research sloppysecondstbb@gmail.com"}

# Share-count concepts, in preference order. The cover-page tag is authoritative WHEN
# FRESH; the rest are fallbacks for filers whose undimensioned cover-page fact is years
# stale (see `_shares_outstanding`). Weighted-average is last — it is a period average,
# not a point-in-time count, so it is used only when nothing better exists.
_SHARE_CONCEPTS = (
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesIssued"),
    ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"),
)
_YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


_REQUIRED_MENTION_COLS = {"cashtagged", "score", "num_comments", "collected_at"}


def _tables_exist(conn) -> bool:
    """Table AND the columns the collector reads.

    Checking the NAME alone was not enough: the working DB carried a five-column
    `reddit_mentions` from before the collector existed, so `--dry-run` — which
    deliberately skips `ensure_tables` — walked straight into
    `OperationalError: no such column: score` and exited 1. The one path that never
    repairs the schema is the one that has to tolerate an unrepaired schema. Same class
    as the `tickers.updated_at` bug, in the branch that fixed it.
    """
    if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reddit_mentions'"
    ).fetchone():
        return False
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reddit_mentions)").fetchall()}
    return _REQUIRED_MENTION_COLS <= cols


def ensure_tables(conn) -> None:
    """Additive only. Never drops, never rewrites."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reddit_mentions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id      TEXT NOT NULL,
            ticker       TEXT NOT NULL,
            subreddit    TEXT,
            mentioned_at TEXT DEFAULT (datetime('now')),
            cashtagged   INTEGER DEFAULT 0,
            score        INTEGER DEFAULT 0,
            num_comments INTEGER DEFAULT 0,
            collected_at TEXT,
            UNIQUE(post_id, ticker)
        )""")
    # Guarded adds, for databases created before these columns existed — the
    # `CREATE TABLE IF NOT EXISTS` above is a no-op on them, which is exactly how the
    # `tickers.updated_at` bug shipped.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reddit_mentions)").fetchall()}
    for col, decl in (("cashtagged", "INTEGER DEFAULT 0"),
                      ("score", "INTEGER DEFAULT 0"),
                      ("num_comments", "INTEGER DEFAULT 0"),
                      # NULL = not yet evaluated. A marker, not a time window: a window
                      # still re-counts the same mention on every run inside it, and the
                      # question "have I already seen this row" has an exact answer.
                      ("collected_at", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE reddit_mentions ADD COLUMN {col} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mentions_ticker_at "
                 "ON reddit_mentions(ticker, mentioned_at)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticker_universe (
            ticker             TEXT PRIMARY KEY,
            first_collected_at TEXT DEFAULT (datetime('now')),
            last_seen_at       TEXT DEFAULT (datetime('now')),
            times_seen         INTEGER DEFAULT 1,
            market_cap         INTEGER,
            cap_status         TEXT,      -- small | unknown | excluded
            source             TEXT
        )""")
    conn.commit()


# ── market cap: SEC shares outstanding x Yahoo close ─────────────────────────
#
# There is NO market-cap data in Scope — `ticker_meta` has the column and zero rows.
# Yahoo's quoteSummary endpoint (the obvious source) now returns 401, so the cap is
# derived from two sources Scope already uses and neither needs a key:
#   SEC   data.sec.gov companyconcept -> EntityCommonStockSharesOutstanding
#   Yahoo v8/finance/chart            -> last close (same endpoint label_outcomes uses)
# Verified end to end: AAPL $4.9T, NVDA $4.8T, LMT $134B, GME $9.7B, ABSI $1.2B,
# MOBX $5.6M. Cached in `ticker_meta` with a TTL — caps move slowly and this must not
# become a per-run API storm.

_cik_cache: dict[str, str] = {}


def _cik_for(symbol: str) -> str | None:
    if not _cik_cache:
        try:
            r = requests.get("https://www.sec.gov/files/company_tickers.json",
                             headers=_SEC_HEADERS, timeout=15)
            for v in r.json().values():
                _cik_cache[v["ticker"].upper()] = str(v["cik_str"]).zfill(10)
        except Exception as exc:
            print(f"[{RULE}] SEC ticker map unavailable: {type(exc).__name__}")
            return None
    return _cik_cache.get(symbol.upper())


def _is_foreign_private_issuer(cik: str) -> bool | None:
    """Does this filer report on 20-F/6-K rather than 10-K?

    THE TSM BUG. SEC reports the ORDINARY share count (25,932,524,521 for TSM); Yahoo
    prices the ADR ($399.09). They are not commensurable — an ADS represents N ordinary
    shares — so multiplying them overstates the cap by exactly the ADR ratio, which for
    TSM is 5: $10.35T reported against ~$2.07T implied.

    THE RATIO IS NOT IN SEC DATA, so it cannot be recovered here. Rather than guess one,
    a foreign private issuer's cap is treated as UNKNOWN and the caller fails closed.
    That costs coverage of ADRs; inventing a ratio would cost correctness on all of them.

    Returns None when the lookup itself fails, so a network error is not mistaken for
    "domestic".
    """
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                         headers=_SEC_HEADERS, timeout=15)
        if not r.ok:
            return None
        forms = set(r.json().get("filings", {}).get("recent", {}).get("form", []) or [])
    except Exception:
        return None
    if "10-K" in forms or "10-Q" in forms:
        return False
    return bool(forms & {"20-F", "40-F", "6-K"})


def _fact_age_days(as_of: str) -> int | None:
    """Days between `as_of` and today. NEGATIVE if the date is in the future."""
    try:
        return (datetime.now(timezone.utc).date() - date.fromisoformat(as_of)).days
    except (TypeError, ValueError):
        return None


def _concept_fact(cik: str, taxonomy: str, concept: str) -> tuple[float, str] | None:
    """The newest (shares, as_of) for one XBRL concept, or None.

    ⚠️ THE SORT KEY IS `(end, filed, start)`, AND `start` IS LOAD-BEARING. A weighted-average
    concept publishes several rows that tie on BOTH `end` and `filed` and differ only in the
    period they average over. MOBX has exactly this:

        start 2025-10-01  end 2026-03-31  filed 2026-07-09  val 8,058,263   (six months)
        start 2026-01-01  end 2026-03-31  filed 2026-07-09  val 9,838,724   (three months)

    Sorting on `(end, filed)` alone left the winner to JSON row order — a 22% swing in the
    published cap decided by nothing. The later `start` is the shorter, more current period,
    so it wins.
    """
    try:
        r = requests.get(
            f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}"
            f"/{taxonomy}/{concept}.json", headers=_SEC_HEADERS, timeout=15)
        if not r.ok:
            return None
        units = r.json().get("units") or {}
        rows = units[list(units)[0]] if units else []
        if not rows:
            return None
        best = max(rows, key=lambda x: (x["end"], x.get("filed") or "",
                                        x.get("start") or "", x.get("val") or 0))
        return float(best["val"]), best["end"]
    except Exception:
        return None


def _shares_outstanding(cik: str) -> tuple[float, str] | None:
    """(shares, as_of_date), or None. THE DATE IS PART OF THE ANSWER.

    Returning a bare number was an instance of this module's bug class — a value carrying
    no information about the dimension that invalidates it. The date travels with it so
    the caller cannot silently forget to check.

    ⚠️ THE FALLBACK EXISTS BECAUSE THE COVER-PAGE TAG GOES STALE FOR REAL COMPANIES.
    Measured over the FULL per-CIK census (every CIK in SEC's `company_tickers.json`, not
    a recent-quarter sample), `dei:EntityCommonStockSharesOutstanding` is years out of
    date for hundreds of filers — Comcast's newest is `end=2009-12-31`, Visa's
    `2010-01-27`, Ford's `2011-04-28`. `companyconcept` returns only the ancient
    UNDIMENSIONED fact for those filers; the current number lives in a dimensioned or
    us-gaap concept.

    So: try the cover-page tag first and STOP if it is fresh — which it is for ~90% of
    filers, so the extra requests are paid only where they are needed. Otherwise keep
    looking and take the FRESHEST fact found.
    """
    facts: list[tuple[float, str]] = []
    for i, (taxonomy, concept) in enumerate(_SHARE_CONCEPTS):
        fact = _concept_fact(cik, taxonomy, concept)
        if fact:
            facts.append(fact)
            # SHORT-CIRCUIT ONLY ON THE COVER-PAGE TAG. It is the authoritative
            # point-in-time count, so when it is fresh — ~90% of filers — there is nothing
            # better to find and three SEC requests are saved.
            #
            # ⚠️ IT USED TO SHORT-CIRCUIT ON *ANY* FRESH CONCEPT, which is not the same
            # thing and published wrong numbers. HEICO: the cover-page tag is stale, the
            # next concept (55,143,000 @2025-10-31) is inside the window so the loop
            # stopped there — and never saw 139,464,000 @2026-04-30. Published $19.9B
            # against a real ~$50B, with no flag, because "first fresh" is not "freshest".
            # `is not None`, NOT `or` — an age of 0 (a fact dated TODAY, the freshest
            # possible) is FALSY, so `age or 10**6` scored the best fact as the worst.
            _a = _fact_age_days(fact[1])
            if i == 0 and _a is not None and abs(_a) <= MAX_SHARES_AGE_DAYS:
                break
    if not facts:
        return None

    # ⚠️ RANKED BY DISTANCE FROM TODAY, NOT BY LATEST `end`. A string/date comparison on
    # `end` means a BOGUS FUTURE DATE ALWAYS WINS, and those are real: ASLE `end=2034-03-05`,
    # THM `2033-10-31`, AXR `2033-09-12`, REPX `2029-04-03`. Each of those filers has a
    # perfectly good current fact one concept away, and picking "the latest end" buried it
    # behind the typo — so all four were dropped to `unknown` with the right number in hand.
    # |age| puts the real fact first and the typo last.
    #
    # Pre-sorting by (filed, end) descending makes `min` — which returns the FIRST minimal
    # element — resolve an |age| tie toward the later filing rather than toward list order.
    def _distance(f: tuple[float, str]) -> int:
        a = _fact_age_days(f[1])
        return 10**6 if a is None else abs(a)      # unparseable ranks last, never first

    facts.sort(key=lambda f: f[1], reverse=True)   # later `end` first, so ties are stable
    return min(facts, key=_distance)


def _last_close(symbol: str) -> float | None:
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?interval=1d&range=5d", headers=_YF_HEADERS, timeout=12)
        if not r.ok:
            return None
        closes = [c for c in
                  r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c]
        return float(closes[-1]) if closes else None
    except Exception:
        return None


def _cache_unknown(conn, symbol: str, cache: bool) -> None:
    """Remember a FAILED lookup, so it is not retried on every single run."""
    if not cache:
        return
    conn.execute(
        "INSERT INTO ticker_meta (symbol, market_cap, cap_updated) VALUES (?,NULL,?) "
        "ON CONFLICT(symbol) DO UPDATE SET market_cap=NULL, "
        "cap_updated=excluded.cap_updated",
        (symbol, datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _market_cap_core(resolve_cik, price_symbols, cache_read, cache_write):
    """THE plausibility guards. ONE implementation, two entry points.

    ⚠️ EVERY GUARD BELOW WAS MOVED HERE UNCHANGED — the share floor, the two-sided
    staleness bound, the foreign-issuer check, the computed-cap bounds and the
    implausible-cache self-heal. They are NOT re-implemented, because "several call sites
    each re-deriving the same rule from a slightly different projection" is the exact bug
    class this codebase has paid for five times. The regression proof is that
    `test_market_cap_plausibility.py` passes UNCHANGED.

    The caller supplies the cache accessors, so the core knows nothing about whether it is
    keyed on a symbol or a CIK.

    ⚠️ `resolve_cik` IS A CALLABLE, NOT A VALUE, and the difference is behavioural. Passing
    the resolved CIK meant `_cik_for` ran on every call INCLUDING a cache hit — a live SEC
    request per cached lookup. `test_a_failed_cap_lookup_is_cached_so_it_is_not_retried_every_run`
    caught it (4 calls where 1 is correct), which is what that regression suite is for.
    """
    row = cache_read()
    # STALE IMPLAUSIBLE VALUES ARE RE-RESOLVED, not served from cache. Without this the
    # fix would never reach the rows that motivated it: CLBK=1085 and
    # TSM=10349411116118 are already cached with fresh timestamps, so a TTL check alone
    # would keep serving them for 30 days. `ticker_meta` is a CACHE — re-resolving it is
    # a refresh, not a rewrite of detection-time data.
    if row and row[0] and not (MIN_PLAUSIBLE_CAP <= row[0] <= MAX_PLAUSIBLE_CAP):
        row = None
    if row and row[1]:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row[1]).replace(
                tzinfo=timezone.utc)
            ttl = CAP_TTL_DAYS if row[0] else UNKNOWN_TTL_DAYS
            if age < timedelta(days=ttl):
                return int(row[0]) if row[0] else None
        except (TypeError, ValueError):
            pass

    cik = resolve_cik()          # AFTER the cache check — see the docstring
    if not cik:
        cache_write(None)
        return None
    # `is not False` — NOT a bare truth test. `_is_foreign_private_issuer` returns None
    # when the SEC lookup itself fails, and None is FALSY, so `if _is_foreign_...(cik):`
    # silently read "lookup failed" as "domestic" and re-enabled the ADR mis-scale during
    # exactly the SEC degradation the None was introduced to signal. The docstring
    # claimed the opposite of what the call site did.
    #
    # Failing closed here is the module's stated priority — a mis-scaled cap must never
    # pass a gate — and the cost is bounded: unknown is collected (flagged), and
    # `repair_unknown_caps` retries it on the next run.
    if _is_foreign_private_issuer(cik) is not False:
        # SEC ordinary shares x a US ADR price is not a market cap. Fail closed.
        cache_write(None)
        return None

    shares_fact = _shares_outstanding(cik) if cik else None
    # THE FIRST SYMBOL THAT PRICES. The shares leg is authoritative from the CIK, but
    # Yahoo prices by SYMBOL, so one is still needed — and the typed one may not be
    # priceable. `NYT.A` returns no close where `NYT` returns 72.37, and `BRK.B` returns
    # none where `BRK-B` returns 497.18. For the symbol entry point this list is a single
    # element, so the behaviour is unchanged.
    price = None
    for _sym in price_symbols:
        if not _sym:
            continue
        price = _last_close(_sym)
        if price:
            break
    if not shares_fact or not price:
        cache_write(None)
        return None
    shares, as_of = shares_fact

    # THE CLBK BUG, guarded where shares ENTER THE PRODUCT rather than inside the fetcher.
    # Columbia Financial, Inc./MD/ (CIK 0002115119) is a freshly reorganised holding
    # company — S-1, 8-K12B, POS AM — whose ONLY cover-page share tag reports the shell's
    # nominal **100** shares, and `_shares_outstanding`'s fallbacks are absent for it too.
    # So the fix cannot be "use a better tag" — a share count this small is not a real
    # float.
    #
    # It lives HERE, not in `_shares_outstanding`, deliberately: a guard inside one
    # fetcher protects one code path, and vanishes silently the day a second share source
    # is added or that fetcher is replaced. This is the only place shares become a cap.
    if shares < MIN_PLAUSIBLE_SHARES:
        cache_write(None)
        return None

    cap = int(shares * price)

    # ── STALENESS ────────────────────────────────────────────────────────────────
    #
    # A share count can be perfectly plausible in MAGNITUDE and still years out of date,
    # and the product is then wrong by however much the float has moved. MOBX is the
    # case — CIK 0001855467, "MOBIX LABS, INC" (name verified against the CIK, which is
    # how the previous attempt at this comment went wrong): its newest of TEN share facts
    # is 2,778,912 at end=2023-11-14, so pricing it today publishes a stale ~$5.6M
    # "micro-cap".
    #
    # ⚠️ TWO-SIDED, because `age > MAX` alone let a FUTURE `end` date through
    # unchallenged — and those exist: THM `end=2033-10-31`, AXR `2033-09-12`,
    # REPX `2029-04-03`, declared ages of -2652/-2603/-980 days masking counts that are
    # really 993/1049/846 days old and were publishing as confident small caps. The most
    # obviously malformed input in the corpus was the one input the guard ignored.
    #
    # ⚠️ AND IT ONLY FAILS CLOSED ON A CAP THAT WOULD READ *SMALL*. A blanket rejection
    # dropped 443 real filers to `unknown` — Comcast, Visa, UPS, Mastercard, Accenture,
    # Ford — and because the collector FAILS OPEN on unknown it then COLLECTED them,
    # defeating gate 3, whose whole job is "$AAPL does not need discovering". Staleness
    # only misleads in the direction that matters here: a stale count still yielding
    # >= LARGE_CAP_MIN is a confirmed large cap either way and excluding it is right; a
    # stale count yielding a SMALL cap is the mis-scale.
    age = _fact_age_days(as_of)
    if age is None or (abs(age) > MAX_SHARES_AGE_DAYS and cap < LARGE_CAP_MIN):
        cache_write(None)
        return None

    # An implausible COMPUTED cap is never cached. Two reasons: a bad value must not enter
    # the cache at all, and — since the read path above re-resolves implausible cached
    # values — caching one would re-fetch SEC and Yahoo on every single run, forever.
    if not (MIN_PLAUSIBLE_CAP <= cap <= MAX_PLAUSIBLE_CAP):
        cache_write(None)
        return None

    cache_write(cap)
    return cap




def market_cap(conn, symbol: str, cache: bool = True) -> int | None:
    """Cached market cap by SYMBOL. Behaviour unchanged; the guards now live in the core.

    ⚠️ THIS FUNCTION FAILS CLOSED (returns None); ITS CALLER FAILS OPEN. Read both.
    `classify_cap` treats None as `unknown` and COLLECTS it, because a missing name in a
    cross-reference universe is the expensive failure. During a SEC or Yahoo outage EVERY
    lookup returns None at once, so gate 3 stops excluding anything — `repair_unknown_caps`
    exists to undo exactly that.
    """
    def _read():
        return conn.execute(
            "SELECT market_cap, cap_updated FROM ticker_meta WHERE symbol = ?",
            (symbol,)).fetchone()

    def _write(cap):
        if not cache:
            return
        conn.execute(
            "INSERT INTO ticker_meta (symbol, market_cap, cap_updated) VALUES (?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET market_cap=excluded.market_cap, "
            "cap_updated=excluded.cap_updated",
            (symbol, cap, datetime.now(timezone.utc).isoformat()))
        conn.commit()

    return _market_cap_core(lambda: _cik_for(symbol), (symbol,), _read, _write)


# ── the traction test ────────────────────────────────────────────────────────

# ── the three gates ─────────────────────────────────────────────────────────

def clears_engagement(score: int, num_comments: int) -> bool:
    """A pulse check. NOT a quality bar and NOT a ranking.

    It exists to drop bot posts and dead threads — a post with zero upvotes and zero
    comments is not a post anyone saw. Either signal alone is enough evidence of a human
    on the other end; requiring both would silently make this a quality filter, which is
    the thing this module must not become.
    """
    score = score or 0
    num_comments = num_comments or 0
    if REQUIRE_BOTH:
        return score >= MIN_SCORE and num_comments >= MIN_COMMENTS
    return score >= MIN_SCORE or num_comments >= MIN_COMMENTS


def _ensure_issuer_cap(conn) -> None:
    """The CIK-keyed cap cache. Additive; `ticker_meta` is untouched.

    A separate table rather than a column on `ticker_meta`, whose PRIMARY KEY is the
    symbol — the whole point here is to stop keying a company on its typed symbol.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS issuer_cap (
            cik          TEXT PRIMARY KEY,
            market_cap   INTEGER,
            cap_updated  TEXT
        )""")
    conn.commit()


def _tickers_for_cik(cik: str) -> tuple[str, ...]:
    """Every symbol SEC lists for this CIK. Used ONLY to find a priceable symbol."""
    norm = str(cik or "").strip().lstrip("0")
    try:
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                         headers=_SEC_HEADERS, timeout=15)
        if not r.ok:
            return ()
        return tuple(v["ticker"] for v in r.json().values()
                     if str(v.get("cik_str", "")).lstrip("0") == norm)
    except Exception:
        return ()


def market_cap_by_cik(conn, cik: str, price_hints: tuple = (),
                      cache: bool = True) -> int | None:
    """Market cap keyed on the AUTHORITATIVE CIK. The fifth entity's fix.

    ⚠️ WHY THIS EXISTS. The cluster surface's cap gate is its TERMINAL filter and it fails
    CLOSED — so a symbol that will not resolve does not mis-display a cluster, it silently
    DELETES it. And the gate was keyed on the free-text `issuerTradingSymbol` while
    `issuer_cik` sat one line up as the group key. Measured: same issuer, same trades,
    label `NYT` -> 1 cluster, label `NYT.A` -> 0.

    Two hops broke on the typed symbol, and this entry point removes the first entirely:
      * `_cik_for("NYT.A")` -> None, `_cik_for("BRK.B")` -> None. **Not called here** —
        the CIK comes from the Form 4, which declares its own issuer.
      * `_last_close("NYT.A")` -> None, `_last_close("BRK.B")` -> None. Still needs a
        SYMBOL, so `price_hints` is tried first and SEC's own symbols for the CIK second.

    ⚠️ AND IT SIDESTEPS SEC'S TICKER MAP. That map sends `XOM` to CIK 2115436,
    "ExxonMobil Holdings Corp" — a factless holdco — while the real filer is 34088. A
    Form 4's `issuer_cik` cannot have that failure mode because the filing states it.

    The guards are NOT re-implemented: this shares `_market_cap_core` with `market_cap`.
    """
    key = str(cik or "").strip()
    if not key:
        return None
    _ensure_issuer_cap(conn)

    def _read():
        return conn.execute(
            "SELECT market_cap, cap_updated FROM issuer_cap WHERE cik = ?",
            (key,)).fetchone()

    def _write(cap):
        if not cache:
            return
        conn.execute(
            "INSERT INTO issuer_cap (cik, market_cap, cap_updated) VALUES (?,?,?) "
            "ON CONFLICT(cik) DO UPDATE SET market_cap=excluded.market_cap, "
            "cap_updated=excluded.cap_updated",
            (key, cap, datetime.now(timezone.utc).isoformat()))
        conn.commit()

    symbols = tuple(dict.fromkeys([h for h in price_hints if h]))
    return _market_cap_core(lambda: key, symbols + _tickers_for_cik(key), _read, _write)


def _band(cap: int | None) -> tuple[str, int | None]:
    """The plausibility band. ONE implementation, shared by both classify entry points."""
    if cap is None:
        return "unknown", None
    # IMPLAUSIBLE IN EITHER DIRECTION -> UNKNOWN, never "small" and never a confident
    # "excluded". CLBK at $1,085 was classified SMALL and published as the top insider
    # cluster; a mis-scaled cap must never again pass a gate.
    if cap < MIN_PLAUSIBLE_CAP or cap > MAX_PLAUSIBLE_CAP:
        return "unknown", None
    if cap >= LARGE_CAP_MIN:
        return "excluded", cap
    return "small", cap


def classify_cap_by_cik(conn, cik: str, price_hints: tuple = (),
                        cache: bool = True) -> tuple[str, int | None]:
    """(cap_status, market_cap) for an authoritative CIK. Same band as `classify_cap`."""
    return _band(market_cap_by_cik(conn, cik, price_hints, cache=cache))


def classify_cap(conn, ticker: str, cache: bool = True) -> tuple[str, int | None]:
    """(cap_status, market_cap). Only a CONFIRMED large cap is excluded.

    Note the direction, and that it is the OPPOSITE of the old watch pool's. That list
    failed CLOSED — an unknown cap was rejected, because a wrong name in a surface a
    human reads is expensive. This is a lookup table nobody reads for its own sake, so a
    MISSING name is the expensive failure: it would silently remove a ticker from the
    universe the real instruments cross-reference against. Unknown therefore collects,
    flagged, so the gap is visible rather than absent.
    """
    # The band lives in `_band`, shared with `classify_cap_by_cik`. Two copies of a
    # threshold rule is how the tiers and the gate drifted apart once already.
    return _band(market_cap(conn, normalize_ticker(ticker) or ticker, cache=cache))


def collectable(conn, ticker: str, cashtagged: int, score: int, num_comments: int,
                cache: bool = True) -> tuple[bool, str, int | None]:
    """(collect, cap_status, market_cap). Cashtag, then engagement, then cap."""
    if not cashtagged:
        return False, "not_cashtagged", None
    if not clears_engagement(score, num_comments):
        return False, "below_engagement", None
    status, cap = classify_cap(conn, ticker, cache=cache)
    return status != "excluded", status, cap


# ── collection ──────────────────────────────────────────────────────────────

def pending_mentions(conn) -> list[dict]:
    """Cashtagged, post-epoch mentions NOT YET EVALUATED.

    `collected_at IS NULL` is the whole fix. Without it this returned every mention ever
    stored, so each run re-upserted every ticker: `last_seen_at` converged to "now" for
    all of them and `times_seen` counted COLLECTOR RUNS, not sightings. The API's
    "ordered by recency" was then a tie broken by nothing, and the oldest names carried
    the highest counts — a de facto age ranking in a list that must not rank at all.

    A marker rather than a time window, because a window still re-counts the same
    mention on every run that falls inside it. "Have I already evaluated this row" has
    an exact answer; approximating it with time would only shrink the error.
    """
    rows = conn.execute(
        """SELECT ticker, subreddit, MAX(score) score, MAX(num_comments) num_comments,
                  MAX(cashtagged) cashtagged, MAX(mentioned_at) last_mention,
                  -- THE EXACT ROWS READ. Marking by ticker was wrong in both
                  -- directions: a mention arriving mid-run for an already-evaluated
                  -- ticker got marked without ever being counted (a sighting lost
                  -- forever), and a crash mid-loop left rows unmarked whose universe
                  -- upsert had already been committed by market_cap's own commit()
                  -- (the same sighting counted twice on the next run — the very bug the
                  -- window fix exists to prevent, surviving on the crash path).
                  GROUP_CONCAT(id) ids
           FROM reddit_mentions
           WHERE cashtagged = 1 AND mentioned_at >= ? AND collected_at IS NULL
           GROUP BY ticker""", (COLLECTION_EPOCH,)).fetchall()
    return [{"ticker": r["ticker"], "subreddit": r["subreddit"],
             "score": r["score"] or 0, "num_comments": r["num_comments"] or 0,
             # CARRIED, not assumed. `collect()` used to pass a literal 1 here, which
             # made the `collectable()` cashtag check unreachable in production — a unit
             # test of a code path with no caller. Both checks now read the same value
             # this query selected, so the second is a real guard rather than decoration.
             "cashtagged": r["cashtagged"] or 0,
             "last_mention": r["last_mention"],
             "ids": [int(i) for i in (r["ids"] or "").split(",") if i]}
            for r in rows]


def upsert_universe(conn, ticker: str, cap_status: str, cap: int | None,
                    source: str = "reddit", last_seen: str | None = None) -> None:
    """Re-collection UPDATES. One row per ticker, never a duplicate.

    `times_seen` is a COUNT, not a score. Nothing ranks on it and nothing should — a
    name mentioned often is not a better name, it is a more-mentioned name.
    """
    # NORMALIZED, like every other ticker table in Scope. This was the only one that
    # was not — unreachable today because extraction upper-cases, but "unreachable via
    # the current caller" is exactly how the phantom-instrument and lossy-grain bugs
    # both started.
    ticker = normalize_ticker(ticker)
    if not ticker:
        return
    conn.execute(
        """INSERT INTO ticker_universe
             (ticker, market_cap, cap_status, source, first_collected_at, last_seen_at)
           -- first_collected_at takes NOW; last_seen_at takes the MENTION time. They
           -- were bound to the SAME parameter, so "first collected" silently held the
           -- mention's timestamp — a field whose name did not match what it held. The
           -- test only asserted `is not None`, which is unfalsifiable here: the column
           -- has a DEFAULT and both branches COALESCE to non-null.
           VALUES (?,?,?,?, datetime('now'), COALESCE(?, datetime('now')))
           ON CONFLICT(ticker) DO UPDATE SET
             -- the MENTION's timestamp, not "now": last_seen_at means "when was this
             -- name last mentioned", and stamping the run time made it mean "when did
             -- the collector last execute", which is the same for every row.
             last_seen_at = COALESCE(excluded.last_seen_at, datetime('now')),
             times_seen   = ticker_universe.times_seen + 1,
             -- Both COALESCEd together, or they disagree: market_cap was COALESCEd
             -- while cap_status was overwritten, so an unknown re-sighting produced
             -- cap_status='unknown' sitting on a resolved market_cap. A verifier built
             -- that row. If the new reading is unknown, keep BOTH old values.
             market_cap   = COALESCE(excluded.market_cap, ticker_universe.market_cap),
             cap_status   = CASE WHEN excluded.market_cap IS NULL
                                 THEN ticker_universe.cap_status
                                 ELSE excluded.cap_status END""",
        (ticker, cap, cap_status, source, last_seen))


def repair_unknown_caps(conn, cache_caps: bool = True) -> dict:
    """Re-resolve rows collected with an unknown cap, and evict any that were large.

    Without this, one SEC/Yahoo outage permanently seeded the universe with mega-caps:
    `collect()` re-upserts nothing it classifies as `excluded`, and nothing deleted, so
    an AAPL row collected during a five-minute outage stayed forever. Measured by a
    verifier — 4 mega-caps in, 0 out.

    Eviction is the right call for a name that is now CONFIRMED large: it fails gate 3
    on real data, so leaving it would mean the outage silently widened the universe's
    definition. Rows that are still unpriceable are left alone, flagged, to be retried.
    """
    out = {"repaired": 0, "evicted": 0, "still_unknown": 0, "implausible_recheck": 0}

    # THE PROD REMEDIATION for the mis-scale bug, and the reason this sweep's original
    # WHERE clause was not enough. CLBK was stored with cap_status **'small'** — the whole
    # defect is that a mis-scaled cap looks like a confident small-cap — so a sweep over
    # ('unknown','excluded') would never revisit the very rows the bug created.
    #
    # Re-checking on stored VALUE rather than status catches them, and it needs no manual
    # prod surgery: the next scheduled collect() heals CLBK and TSM on its own.
    implausible = [r[0] for r in conn.execute(
        "SELECT ticker FROM ticker_universe WHERE market_cap IS NOT NULL "
        "AND (market_cap < ? OR market_cap > ?)",
        (MIN_PLAUSIBLE_CAP, MAX_PLAUSIBLE_CAP)).fetchall()]
    out["implausible_recheck"] = len(implausible)

    stale = [r[0] for r in conn.execute(
        "SELECT ticker FROM ticker_universe "
        "WHERE cap_status IN ('unknown','excluded')").fetchall()]
    stale = list(dict.fromkeys(stale + implausible))
    for t in stale:
        status, cap = classify_cap(conn, t, cache=cache_caps)
        if status == "unknown":
            out["still_unknown"] += 1
            # An implausible row must be CLEARED even when the re-check cannot price it.
            # Falling through to `continue` here would leave CLBK sitting at
            # cap_status='small', market_cap=1085 — the bad value surviving its own
            # remediation. An honest unknown is the correct resting state.
            if t in set(implausible):
                conn.execute("UPDATE ticker_universe SET cap_status='unknown', "
                             "market_cap=NULL WHERE ticker = ?", (t,))
            continue
        # MARKED, NOT DELETED. Deleting made a wrongful eviction PERMANENT: one bad
        # price tick removed a genuine small cap, and it could not return without both a
        # fresh mention AND the 30-day cap cache expiring. That is failing CLOSED in a
        # module that argues everywhere else that a missing name is the expensive
        # failure. Excluded rows stay, are re-checked on every pass, and are filtered out
        # of the API — so an exclusion is now reversible and auditable.
        conn.execute("UPDATE ticker_universe SET cap_status = ?, market_cap = ? "
                     "WHERE ticker = ?", (status, cap, t))
        out["evicted" if status == "excluded" else "repaired"] += 1
    return out


def collect(conn, cache_caps: bool = True) -> dict:
    """Walk cashtagged mentions and upsert the ones that clear all three gates."""
    counts = {"collected": 0, "flagged_unknown": 0, "excluded_large": 0,
              "below_engagement": 0}
    counts.update(repair_unknown_caps(conn, cache_caps=cache_caps))
    evaluated_ids: list[int] = []
    for m in pending_mentions(conn):
        evaluated_ids.extend(m["ids"])
        ok, status, cap = collectable(conn, m["ticker"], m["cashtagged"], m["score"],
                                      m["num_comments"], cache=cache_caps)
        if not ok:
            counts["below_engagement" if status == "below_engagement"
                   else "excluded_large"] += 1
            continue
        upsert_universe(conn, m["ticker"], status, cap, last_seen=m["last_mention"])
        counts["collected"] += 1
        if status == "unknown":
            counts["flagged_unknown"] += 1

    # Mark EVERY evaluated mention, including the rejected ones — engagement is fixed at
    # ingest and never improves, so re-testing a rejected row is pure re-work that would
    # also re-hit the cap API. Keyed on the ROW IDS actually read, so a mention that
    # lands mid-run is left pending for the next one rather than marked unseen.
    if evaluated_ids:
        qs = ",".join("?" * len(evaluated_ids))
        conn.execute(
            f"UPDATE reddit_mentions SET collected_at = datetime('now') "
            f"WHERE id IN ({qs})", evaluated_ids)
    return counts


def run(dry_run: bool = False) -> dict:
    """`--dry-run` writes NOTHING. Not the tables, not ticker_meta, not activity_log.

    It used to write all three against the working database while reporting itself dry.
    A dry run that mutates state is worse than none, because it is used precisely when
    someone is being careful.
    """
    t0 = time.time()
    conn = db_connection()
    if not dry_run:
        ensure_tables(conn)
    elif not _tables_exist(conn):
        print(f"[{RULE}] [dry] schema not ready (table or the cashtagged/score/"
              f"num_comments columns are missing) — nothing to inspect, and a dry run "
              f"will not create or migrate anything. Run without --dry-run first.")
        conn.close()
        return {"collected": 0, "dry_run": True}

    if dry_run:
        counts = {"collected": 0, "flagged_unknown": 0, "excluded_large": 0,
                  "below_engagement": 0}
        for m in pending_mentions(conn):
            ok, status, _cap = collectable(conn, m["ticker"], m["cashtagged"],
                                           m["score"], m["num_comments"], cache=False)
            key = ("collected" if ok else
                   "below_engagement" if status == "below_engagement" else
                   "excluded_large")
            counts[key] += 1
            if ok and status == "unknown":
                counts["flagged_unknown"] += 1
            print(f"[{RULE}] [dry] {m['ticker']}: {status} "
                  f"(score={m['score']}, comments={m['num_comments']})")
    else:
        counts = collect(conn)
        conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM ticker_universe").fetchone()[0] \
        if _tables_exist(conn) else 0
    # repaired/evicted/still_unknown were computed and then dropped on the floor, so a
    # run that reclassified thirty rows logged nothing about it.
    notes = (f"collected={counts['collected']}, unknown_cap={counts['flagged_unknown']}, "
             f"excluded_large={counts['excluded_large']}, "
             f"below_engagement={counts['below_engagement']}, "
             f"repaired={counts.get('repaired', 0)}, evicted={counts.get('evicted', 0)}, "
             f"still_unknown={counts.get('still_unknown', 0)}, universe={total}, "
             f"dry_run={dry_run}")
    if not dry_run:
        # emitted=0 ON PURPOSE. `alerts_emitted` is rendered as "N new" in amber on the
        # homepage activity strip, and coverage is not news — filing collected names
        # there would present a lookup-table write as though the system had found
        # something. The real counts live in `notes`.
        record_activity(RULE, scanned=0, flagged=counts["collected"], emitted=0,
                        duration_seconds=round(time.time() - t0, 2), notes=notes)
    print(f"[{RULE}] {notes}")
    conn.close()
    return {**counts, "dry_run": dry_run}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # The scheduler passes --emit-alerts to every job. Discovery emits NO alerts by
    # design — it is not a signal — but the flag must parse or argparse exits 2, which
    # is how the morning brief silently failed 100% of its runs.
    p.add_argument("--emit-alerts", action="store_true",
                   help="accepted for scheduler compatibility; collection emits NO alerts")
    p.add_argument("--dry-run", action="store_true",
                   help="report what WOULD be collected, writing nothing at all")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    run(dry_run=args.dry_run)
