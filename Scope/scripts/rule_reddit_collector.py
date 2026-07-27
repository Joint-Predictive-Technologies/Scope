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
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection, record_activity  # noqa: E402

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

# Mentions recorded before the extraction fix are ~45% English-word false positives
# (BACK, HERE, POST...). Collecting them would fill the universe with words.
COLLECTION_EPOCH = "2026-07-27 00:00:00"

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
            cashtagged   INTEGER DEFAULT 0,
            score        INTEGER DEFAULT 0,
            num_comments INTEGER DEFAULT 0,
            UNIQUE(post_id, ticker)
        )""")
    # Guarded adds, for databases created before these columns existed — the
    # `CREATE TABLE IF NOT EXISTS` above is a no-op on them, which is exactly how the
    # `tickers.updated_at` bug shipped.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reddit_mentions)").fetchall()}
    for col, decl in (("cashtagged", "INTEGER DEFAULT 0"),
                      ("score", "INTEGER DEFAULT 0"),
                      ("num_comments", "INTEGER DEFAULT 0")):
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


def classify_cap(conn, ticker: str, cache: bool = True) -> tuple[str, int | None]:
    """(cap_status, market_cap). Only a CONFIRMED large cap is excluded.

    Note the direction, and that it is the OPPOSITE of the old watch pool's. That list
    failed CLOSED — an unknown cap was rejected, because a wrong name in a surface a
    human reads is expensive. This is a lookup table nobody reads for its own sake, so a
    MISSING name is the expensive failure: it would silently remove a ticker from the
    universe the real instruments cross-reference against. Unknown therefore collects,
    flagged, so the gap is visible rather than absent.
    """
    cap = market_cap(conn, ticker, cache=cache)
    if cap is None:
        return "unknown", None
    if cap >= LARGE_CAP_MIN:
        return "excluded", cap
    return "small", cap


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
    """Cashtagged, post-epoch mentions. No window, no baseline — coverage accrues."""
    rows = conn.execute(
        """SELECT ticker, subreddit, MAX(score) score, MAX(num_comments) num_comments
           FROM reddit_mentions
           WHERE cashtagged = 1 AND mentioned_at >= ?
           GROUP BY ticker""", (COLLECTION_EPOCH,)).fetchall()
    return [{"ticker": r["ticker"], "subreddit": r["subreddit"],
             "score": r["score"] or 0, "num_comments": r["num_comments"] or 0}
            for r in rows]


def upsert_universe(conn, ticker: str, cap_status: str, cap: int | None,
                    source: str = "reddit") -> None:
    """Re-collection UPDATES. One row per ticker, never a duplicate.

    `times_seen` is a COUNT, not a score. Nothing ranks on it and nothing should — a
    name mentioned often is not a better name, it is a more-mentioned name.
    """
    conn.execute(
        """INSERT INTO ticker_universe
             (ticker, market_cap, cap_status, source) VALUES (?,?,?,?)
           ON CONFLICT(ticker) DO UPDATE SET
             last_seen_at = datetime('now'),
             times_seen   = ticker_universe.times_seen + 1,
             market_cap   = COALESCE(excluded.market_cap, ticker_universe.market_cap),
             cap_status   = excluded.cap_status""",
        (ticker, cap, cap_status, source))


def collect(conn, cache_caps: bool = True) -> dict:
    """Walk cashtagged mentions and upsert the ones that clear all three gates."""
    counts = {"collected": 0, "flagged_unknown": 0, "excluded_large": 0,
              "below_engagement": 0}
    for m in pending_mentions(conn):
        ok, status, cap = collectable(conn, m["ticker"], 1, m["score"],
                                      m["num_comments"], cache=cache_caps)
        if not ok:
            counts["below_engagement" if status == "below_engagement"
                   else "excluded_large"] += 1
            continue
        upsert_universe(conn, m["ticker"], status, cap)
        counts["collected"] += 1
        if status == "unknown":
            counts["flagged_unknown"] += 1
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
        print(f"[{RULE}] [dry] tables do not exist yet — nothing to inspect, and a dry "
              f"run will not create them.")
        conn.close()
        return {"collected": 0, "dry_run": True}

    if dry_run:
        counts = {"collected": 0, "flagged_unknown": 0, "excluded_large": 0,
                  "below_engagement": 0}
        for m in pending_mentions(conn):
            ok, status, _cap = collectable(conn, m["ticker"], 1, m["score"],
                                           m["num_comments"], cache=False)
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
    notes = (f"collected={counts['collected']}, unknown_cap={counts['flagged_unknown']}, "
             f"excluded_large={counts['excluded_large']}, "
             f"below_engagement={counts['below_engagement']}, universe={total}, "
             f"dry_run={dry_run}")
    if not dry_run:
        record_activity(RULE, scanned=0, flagged=0, emitted=counts["collected"],
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
                   help="report candidates without writing to the watch pool")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    run(dry_run=args.dry_run)
