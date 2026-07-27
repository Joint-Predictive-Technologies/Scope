#!/usr/bin/env python3
"""DISCOVERY — small-cap tickers getting unusual reddit traction.

A CANDIDATE GENERATOR, NOT A SIGNAL. This is the first thing in Scope with no ground
truth: "was this spike worth surfacing?" has no clean answer until months of watching.
So it produces a REVIEW SURFACE — a pool of tickers a human might look at — and
deliberately carries NO confidence score, no predicted direction, no implied buy.

⚠️ NEVER A GATE LEG. Discovery adds no instrument to RULE_10. RULE_REDDIT stays in
RULE_10_EXCLUDED, the watch pool is a separate table that no corroboration path reads,
and a ticker sitting in the pool contributes NOTHING to a convergence. That is asserted
in tests/test_reddit_discovery.py, not merely intended. The whole value of the
convergence moat is that its legs are independent sources; a social-buzz leg would be
the fastest way to destroy it.

"UNUSUAL" MEANS UNUSUAL FOR THIS TICKER — a deviation from its OWN baseline, not
"popular". Ranking by raw mention volume just re-finds GME and NVDA every day, which is
information nobody needs. A ticker mentioned 40 times when it is normally mentioned 40
times is not a discovery; the same ticker going 2 -> 40 is.

MEDIAN, NOT MEAN, for the baseline. A prior spike inflates a mean and then suppresses
detection of the next one — the metric quietly blinds itself exactly where it matters.
The median is unmoved by one outlier day.

════════════════════════════════════════════════════════════════════════════════
⚠️ THE PARAMETERS BELOW ARE PROVISIONAL AND UNTUNED. READ THIS BEFORE TRUSTING THEM.
════════════════════════════════════════════════════════════════════════════════
The work order asked for them to be tuned against the real reddit history. That
history does not exist yet. Measured 2026-07-27:

    reddit_posts       93 rows, 47 distinct tickers
    total span         12.3 HOURS across 2 calendar days (2026-07-19 .. 07-20)
    staleness          data ends 7 days ago
    grain              ONE ticker per post (93 rows = 93 post_ids) — a post naming
                       three tickers recorded one, so counts were lossy
    contamination      42/93 rows (45.2%) are English-word false positives across 15
                       tickers (BACK, HERE, POST, MOVE, BEAT, FIVE, CASH, OPEN...).
                       An earlier draft said "46/93 (49%) ... BACK/HERE/POST/RYAN/TECH";
                       a verifier recomputed it against the actual blocklist — the five
                       named account for 26 rows, and RYAN and TECH are not blocklisted
                       at all (they are caught by the new frequency list, not the old
                       one). Directionally right, arithmetically wrong.

A rolling 14-day median cannot be computed, let alone tuned, from 12 hours of
45%-contaminated data. Any K or floor stated as "measured" would be a guess wearing a
lab coat. So the numbers below are DECLARED GUESSES, and MIN_HISTORY_DAYS makes the rule
structurally unable to fire until real history exists — the same shape as RULE_15's
cold-start quarantine. Until then it correctly emits nothing and says so in activity_log.

Retune when ~30 days of post-DISCOVERY_EPOCH history has accumulated. The honest first
step is to look at what it WOULD have fired on (`--dry-run`) before letting it write.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection, record_activity  # noqa: E402

RULE = "RULE_DISCOVERY"

# ── Provisional parameters (see the module docstring) ────────────────────────
BASELINE_DAYS = 14          # trailing window the median is taken over
# STRUCTURAL GUARD. Must be >= BASELINE_DAYS, and a test enforces that.
#
# It was 7, and a verifier measured what that did. `daily_counts` zero-fills every slot
# it has no data for, and those zeros mean "NO DATA", not "no mentions" — so a partial
# baseline reads as a near-zero baseline and everything looks like a spike. With the
# guard at 7 the rule opened exactly one week before 14 days of countable history could
# exist. Null control, 4000 trials per row, `today` drawn from the ticker's OWN history
# so every firing is by construction a false positive:
#
#     days since epoch     false-fire rate     median reported deviation
#            7 (guard!)         38.3 %                23.0x
#            8                  46.9 %                11.3x
#            9                  36.2 %                 6.0x
#           12                   5.8 %                 3.6x
#           15+ (steady)         1.2 %                 3.3x
#
# The guard opened at the maximum of its own false-positive curve. A ticker sitting at
# a flat 20 mentions/day — which has never moved — was written into the pool at "20x".
MIN_HISTORY_DAYS = BASELINE_DAYS
DEVIATION_K = 3.0           # today >= median * K
MIN_MENTIONS = 5            # absolute floor, so 1 -> 3 cannot fire
COLD_START_MENTIONS = 12    # no baseline at all -> needs a high absolute count
SMALL_CAP_MAX = 2_000_000_000   # $2B ceiling. Above this, discovery is pointless:
                                # a mega-cap's reddit buzz is not news to anyone.
CAP_TTL_DAYS = 30           # market caps move slowly; do not re-fetch per run

# Mentions recorded before the extraction fix are ~45% English-word false positives
# (BACK, HERE, POST, RYAN...). Counting them would build baselines for words. Same
# quarantine pattern as rule_15_earnings_nlp.REPAIR_EPOCH.
DISCOVERY_EPOCH = "2026-07-27 00:00:00"

_SEC_HEADERS = {"User-Agent": "Scope research sloppysecondstbb@gmail.com"}
_YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _tables_exist(conn) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reddit_mentions'"
    ).fetchone())


def ensure_tables(conn) -> None:
    """Additive only. Never drops, never rewrites."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reddit_mentions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id      TEXT NOT NULL,
            ticker       TEXT NOT NULL,
            subreddit    TEXT,
            mentioned_at TEXT DEFAULT (datetime('now')),
            UNIQUE(post_id, ticker)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mentions_ticker_at "
                 "ON reddit_mentions(ticker, mentioned_at)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovery_watch (
            ticker            TEXT PRIMARY KEY,
            first_discovered  TEXT DEFAULT (datetime('now')),
            last_discovered   TEXT DEFAULT (datetime('now')),
            times_discovered  INTEGER DEFAULT 1,
            mention_count     INTEGER,
            baseline          REAL,
            deviation         REAL,
            market_cap        INTEGER,
            trigger           TEXT,
            subreddits        TEXT,
            sample_days       INTEGER
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


def _shares_outstanding(cik: str) -> float | None:
    try:
        r = requests.get(
            f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}"
            f"/dei/EntityCommonStockSharesOutstanding.json",
            headers=_SEC_HEADERS, timeout=15)
        if not r.ok:
            return None
        units = r.json()["units"]
        rows = units[list(units)[0]]
        return float(max(rows, key=lambda x: x["end"])["val"])
    except Exception:
        return None


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


def market_cap(conn, symbol: str, cache: bool = True) -> int | None:
    """Cached market cap, or None if it cannot be determined.

    None is NOT treated as small — an unknown cap must not sneak a mega-cap into the
    pool, so callers reject it. Failing closed is the right direction here: the cost of
    a missed candidate is one missed review, the cost of a false one is noise in the
    only surface a human is asked to read.
    """
    row = conn.execute(
        "SELECT market_cap, cap_updated FROM ticker_meta WHERE symbol = ?",
        (symbol,)).fetchone()
    if row and row[0] and row[1]:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row[1]).replace(
                tzinfo=timezone.utc)
            if age < timedelta(days=CAP_TTL_DAYS):
                return int(row[0])
        except (TypeError, ValueError):
            pass

    cik = _cik_for(symbol)
    if not cik:
        return None
    shares = _shares_outstanding(cik)
    price = _last_close(symbol)
    if not shares or not price:
        return None
    cap = int(shares * price)
    if cache:
        conn.execute(
            "INSERT INTO ticker_meta (symbol, market_cap, cap_updated) VALUES (?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET market_cap=excluded.market_cap, "
            "cap_updated=excluded.cap_updated",
            (symbol, cap, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    return cap


# ── the traction test ────────────────────────────────────────────────────────

def daily_counts(conn, ticker: str, days: int = BASELINE_DAYS) -> list[int]:
    """Mentions per day over the trailing `days`, EXCLUDING today. Zero-filled.

    Zero-filling matters: a ticker mentioned on 2 of the last 14 days has a median of 0,
    not the median of its two active days. Without the zeros the baseline would be the
    ticker's *typical active day*, which is a completely different question and would
    make quiet tickers look like they had a high normal.
    """
    rows = conn.execute(
        """SELECT date(mentioned_at) d, COUNT(*) n FROM reddit_mentions
           WHERE ticker = ? AND mentioned_at >= ?
             AND date(mentioned_at) >= date('now', ?)
             AND date(mentioned_at) < date('now')
           GROUP BY d""",
        (ticker, DISCOVERY_EPOCH, f"-{int(days)} days")).fetchall()
    by_day = {r[0]: r[1] for r in rows}
    today = datetime.now(timezone.utc).date()
    return [by_day.get(str(today - timedelta(days=i)), 0) for i in range(1, days + 1)]


def today_count(conn, ticker: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM reddit_mentions WHERE ticker = ? AND mentioned_at >= ? "
        "AND date(mentioned_at) = date('now')",
        (ticker, DISCOVERY_EPOCH)).fetchone()[0]


def history_days(conn) -> int:
    """Distinct days with ingestion data INSIDE the baseline window.

    Windowed, not global. The global form counted days anywhere post-epoch, so after an
    ingestion outage longer than BASELINE_DAYS the guard read "31 days of history" while
    the actual baseline window held nothing — and every ticker with >= COLD_START_MENTIONS
    cold-started into the pool on the first day back, at its own normal volume, reporting
    a sample size of 31 for a baseline built from zero real days. Measured by a verifier.

    Counting only days that fall in the window the median is actually taken over makes
    the guard measure the thing it is guarding.
    """
    return conn.execute(
        """SELECT COUNT(DISTINCT date(mentioned_at)) FROM reddit_mentions
           WHERE mentioned_at >= ?
             AND date(mentioned_at) >= date('now', ?)
             AND date(mentioned_at) <  date('now')""",
        (DISCOVERY_EPOCH, f"-{BASELINE_DAYS} days")).fetchone()[0]


def evaluate(counts_today: int, baseline_days: list[int]) -> tuple[bool, str, float | None]:
    """(fires, trigger, deviation). Pure — no DB, no network, so it is testable.

    Three ways to fail, in order:
      floor       below MIN_MENTIONS, nothing else matters. 1 -> 3 is not a discovery.
      cold start  no baseline at all -> needs COLD_START_MENTIONS. Brand-new buzz is
                  interesting, but with nothing to deviate FROM it needs a stricter bar
                  than a deviation would impose.
      deviation   today >= median * K.
    """
    if counts_today < MIN_MENTIONS:
        return False, "below_floor", None

    median = statistics.median(baseline_days) if baseline_days else 0.0

    # BASELINE_DAYS is even, so the median is the mean of two slots and can be 0.5.
    # That created a cliff where MORE history meant a STRICTLY EASIER bar:
    #     6 active days  -> median 0.0 -> cold start, needs 12 mentions
    #     7 active days  -> median 0.5 -> deviation,  needs only the floor of 5, and
    #                                     reports it as "10x"
    # A ticker mentioned once every other day fired on 5 mentions. Anything below one
    # mention a day is not a baseline to deviate from, so it takes the cold-start path.
    if median < 1.0:
        # No normal to deviate from — cold start.
        # None, NOT the raw count. Returning the count here made `deviation` mean two
        # different things on two branches, and the pool sorts on that column — so raw
        # counts and true multiples were ranked against each other in one list, which is
        # the rank-by-popularity failure this module exists to avoid.
        if counts_today >= COLD_START_MENTIONS:
            return True, "cold_start", None
        return False, "cold_start_below_bar", None

    deviation = counts_today / median
    if deviation >= DEVIATION_K:
        return True, "deviation", round(deviation, 2)
    return False, "within_normal", round(deviation, 2)


def find_candidates(conn, cache_caps: bool = True) -> list[dict]:
    """Tickers whose traction today is unusual FOR THEM. Read-only except cap caching."""
    have = history_days(conn)
    if have < MIN_HISTORY_DAYS:
        print(f"[{RULE}] insufficient history: {have} day(s) of post-epoch mentions, "
              f"need {MIN_HISTORY_DAYS}. Emitting nothing — a baseline built on this "
              f"would be noise.")
        return []

    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM reddit_mentions WHERE mentioned_at >= ? "
        "AND date(mentioned_at) = date('now')", (DISCOVERY_EPOCH,)).fetchall()]

    out = []
    for t in tickers:
        n = today_count(conn, t)
        fires, trigger, deviation = evaluate(n, daily_counts(conn, t))
        if not fires:
            continue
        cap = market_cap(conn, t, cache=cache_caps)
        # Unknown cap is NOT small — fail closed.
        if cap is None or cap > SMALL_CAP_MAX:
            continue
        subs = ",".join(sorted({r[0] for r in conn.execute(
            "SELECT DISTINCT subreddit FROM reddit_mentions WHERE ticker=? "
            "AND date(mentioned_at)=date('now')", (t,)).fetchall() if r[0]}))
        out.append({
            "ticker": t, "mention_count": n,
            "baseline": statistics.median(daily_counts(conn, t)),
            "deviation": deviation, "market_cap": cap, "trigger": trigger,
            # `have` is days WITH DATA inside the baseline window — the number a
            # reviewer needs to judge how much the baseline is worth. It used to be a
            # GLOBAL count reported as though per-baseline, so an outage could show
            # "31 days" behind a baseline built from zero.
            "subreddits": subs, "sample_days": have,
            "baseline_window_days": BASELINE_DAYS,
        })
    # TWO SCALES, NEVER COMPARED. Deviation-triggered candidates rank by their multiple;
    # cold starts have no multiple at all and rank among THEMSELVES by raw count, after.
    # The previous sort put a raw mention count and a true multiple in one ordering,
    # which is the rank-by-popularity failure this module exists to avoid.
    return sorted(out, key=lambda c: (c["deviation"] is None,
                                      -(c["deviation"] or 0.0),
                                      -c["mention_count"]))


def upsert_watch(conn, c: dict) -> None:
    """Re-discovery UPDATES. One row per ticker, never a duplicate."""
    conn.execute(
        """INSERT INTO discovery_watch
             (ticker, mention_count, baseline, deviation, market_cap, trigger,
              subreddits, sample_days)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(ticker) DO UPDATE SET
             last_discovered  = datetime('now'),
             times_discovered = discovery_watch.times_discovered + 1,
             mention_count    = excluded.mention_count,
             baseline         = excluded.baseline,
             deviation        = excluded.deviation,
             market_cap       = excluded.market_cap,
             trigger          = excluded.trigger,
             subreddits       = excluded.subreddits,
             sample_days      = excluded.sample_days""",
        (c["ticker"], c["mention_count"], c["baseline"], c["deviation"],
         c["market_cap"], c["trigger"], c["subreddits"], c["sample_days"]))


def run(dry_run: bool = False) -> dict:
    """`--dry-run` writes NOTHING. Not the tables, not ticker_meta, not activity_log.

    It used to write all three: `ensure_tables` created two tables, `market_cap` cached
    to ticker_meta, and record_activity logged a row — against the working database,
    while the run reported itself as dry. A dry run that mutates state is worse than no
    dry run, because it is used precisely when someone is being careful.
    """
    t0 = time.time()
    conn = db_connection()
    if not dry_run:
        ensure_tables(conn)
    elif not _tables_exist(conn):
        print(f"[{RULE}] [dry] tables do not exist yet — nothing to inspect, and a dry "
              f"run will not create them.")
        conn.close()
        return {"candidates": 0, "dry_run": True}

    cands = find_candidates(conn, cache_caps=not dry_run)

    if not dry_run:
        for c in cands:
            upsert_watch(conn, c)
        conn.commit()

    for c in cands:
        print(f"[{RULE}] {'[dry] ' if dry_run else ''}{c['ticker']}: "
              f"{c['mention_count']} today vs baseline {c['baseline']} "
              f"({c['trigger']}, x{c['deviation']}), cap ${c['market_cap']:,}")

    notes = (f"candidates={len(cands)}, baseline_days_with_data={history_days(conn)}, "
             f"dry_run={dry_run}")
    if not dry_run:
        record_activity(RULE, scanned=0, flagged=len(cands), emitted=len(cands),
                        duration_seconds=round(time.time() - t0, 2), notes=notes)
    print(f"[{RULE}] {notes}")
    conn.close()
    return {"candidates": len(cands), "dry_run": dry_run}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # The scheduler passes --emit-alerts to every job. Discovery emits NO alerts by
    # design — it is not a signal — but the flag must parse or argparse exits 2, which
    # is how the morning brief silently failed 100% of its runs.
    p.add_argument("--emit-alerts", action="store_true",
                   help="accepted for scheduler compatibility; discovery emits no alerts")
    p.add_argument("--dry-run", action="store_true",
                   help="report candidates without writing to the watch pool")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    run(dry_run=args.dry_run)
