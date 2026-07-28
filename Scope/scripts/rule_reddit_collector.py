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
from datetime import datetime, timedelta, timezone

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


def market_cap(conn, symbol: str, cache: bool = True) -> int | None:
    """Cached market cap, or None if it cannot be determined.

    ⚠️ THIS FUNCTION FAILS CLOSED (returns None); ITS CALLER FAILS OPEN. Read both.

    An earlier version of this docstring claimed "callers reject it… failing closed is
    the right direction", which was true of the watch pool that no longer exists and is
    FALSE for the collector. `classify_cap` treats None as `unknown` and COLLECTS it,
    because a missing name in a cross-reference universe is the expensive failure.

    The consequence, which a verifier measured: during a SEC or Yahoo outage EVERY
    lookup returns None at once, so gate 3 stops excluding anything and AAPL/NVDA/MSFT
    all enter the universe. `repair_unknown_caps` exists to undo exactly that — without
    it those rows were permanent, because `collect()` skips anything already excluded
    and nothing ever deleted.
    """
    row = conn.execute(
        "SELECT market_cap, cap_updated FROM ticker_meta WHERE symbol = ?",
        (symbol,)).fetchone()
    if row and row[1]:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row[1]).replace(
                tzinfo=timezone.utc)
            ttl = CAP_TTL_DAYS if row[0] else UNKNOWN_TTL_DAYS
            if age < timedelta(days=ttl):
                return int(row[0]) if row[0] else None
        except (TypeError, ValueError):
            pass

    cik = _cik_for(symbol)
    if not cik:
        _cache_unknown(conn, symbol, cache)
        return None
    shares = _shares_outstanding(cik) if cik else None
    price = _last_close(symbol)
    if not shares or not price:
        _cache_unknown(conn, symbol, cache)
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
    cap = market_cap(conn, normalize_ticker(ticker) or ticker, cache=cache)
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
    out = {"repaired": 0, "evicted": 0, "still_unknown": 0}
    stale = [r[0] for r in conn.execute(
        "SELECT ticker FROM ticker_universe "
        "WHERE cap_status IN ('unknown','excluded')").fetchall()]
    for t in stale:
        status, cap = classify_cap(conn, t, cache=cache_caps)
        if status == "unknown":
            out["still_unknown"] += 1
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
