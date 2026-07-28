#!/usr/bin/env python3
"""
RULE_11 — Government Contracts (USASpending.gov)

Fetches federal contract awards, matches recipients to tickers via the curated
contractor resolver, stores them in `contracts`, and emits alerts.

WHAT THIS RULE MEANS, PRECISELY
-------------------------------
It reports **newly signed contract awards**. The USASpending search is filtered
with `date_type="new_awards_only"`, so every row is an award whose base
obligation falls inside the scan window — a real award event, dated by the
government, not by our ingest run.

That precision is the entire point of the 2026-07-28 repair. The previous
version asked for `Action Date` / `Period of Performance Start Date`, which are
**not valid Contract Award fields** on this endpoint (the API's own field list
omits them), so both always returned `null` and `award_date` fell back to the
run date. Three defects followed from that one fallback:

  1. Identity collapsed to `(recipient_name, award_date)` = `(recipient, run-day)`,
     so the SECOND and every later award to one recipient in a run was dropped by
     `UNIQUE(recipient_name, award_date)` + `INSERT OR IGNORE`.
  2. A backfill overwrote a stored row's `award_id` from a *different* award,
     leaving `amount` and `award_id` describing two different contracts.
  3. With no date filter that means anything, the default sort returned the
     largest *lifetime* contracts (a 1993 Lockheed award, expired in 2017,
     carrying $48B cumulative) headlined as "awarded $48B" — which is why every
     alert was CRITICAL.

Identity is now `generated_internal_id`, the API's stable per-award key (it
encodes the PIID, e.g. `CONT_AWD_W31P4Q26C0013_9700_-NONE-_-NONE-`). Two distinct
awards to one recipient on one day are two rows. Re-seeing an award updates it.

AMOUNT SEMANTICS (read before changing the headline)
----------------------------------------------------
`Award Amount` is the award's **total obligated value to date**, accumulated
across every modification of that PIID. For a *newly signed* award — all this
rule ingests — that figure is the value obligated on the award so far, so
"awarded $X" is a true statement about a real event. It is NOT the recipient's
lifetime total across all contracts, and for an old award it would NOT be a new
obligation. Do not reintroduce old awards without also changing this wording.

Storage tiers:
  - >= $50M newly-signed awards: always stored and alerted; swept COMPLETELY
    (paged to exhaustion within the page budget).
  - Sub-$50M: stored/alerted only on a watchlist ticker match, from an explicitly
    BOUNDED best-effort sweep (see WATCHLIST_MAX_PAGES) — the >=$1M population is
    ~12.7k awards YTD, far past any per-run budget. Truncation is logged, never
    silent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection, resolve_contractor, CONTRACTOR_MIN_CONFIDENCE

BASE_URL   = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
AWARD_URL  = "https://api.usaspending.gov/api/v2/awards/{award_id}/"
USER_AGENT = "Scope/1.0 (political-market intelligence; contracts rule)"

TODAY      = datetime.now(timezone.utc).strftime("%Y-%m-%d")
YEAR_START = f"{datetime.now(timezone.utc).year}-01-01"

MIN_AMOUNT           = 1_000_000     # $1M floor for the watchlist sweep
LARGE_THRESHOLD      = 50_000_000    # $50M: always store/alert
PAGE_SIZE            = 100           # API maximum (101 => HTTP 422)
MAX_PAGES            = 25            # 2,500 awards/run — far above the ~270 >=$50M YTD
WATCHLIST_MAX_PAGES  = 5             # bounded best-effort; truncation is LOGGED
REQUEST_PAUSE        = 0.35          # fair-access pacing between requests
LOOKBACK_DAYS        = 30            # re-scan window: USASpending posts awards late

# Contract Award field names, verbatim from the endpoint's own mapping list.
# `Action Date` and `Period of Performance Start Date` are NOT in it — that
# omission is the original bug. Do not re-add them.
FIELDS = [
    "Award ID",                 # PIID
    "Recipient Name",
    "Award Amount",
    "Awarding Agency",
    "Base Obligation Date",     # the real award date
    "Start Date",
    "End Date",
    "Description",
    "Contract Award Type",
    "generated_internal_id",    # stable per-award identity
]


class ContractsFetchError(RuntimeError):
    """Upstream failed. Raised so the failure is LOUD — never an empty list."""


# ─────────────────────────── schema (contracts only) ──────────────────────────
def ensure_contracts_schema(conn) -> bool:
    """Make `contracts` keyed on the award, not on (recipient, run-day).

    Idempotent. Rebuilds the table only when the legacy
    `UNIQUE(recipient_name, award_date)` constraint is still present, because
    that constraint silently drops a recipient's 2nd..Nth award in a run —
    the collapse survives fixing the dates unless the constraint goes too.

    Adds `verified_at`: the timestamp at which a row's amount/award_id were
    confirmed against USASpending. NULL means "not confirmed" — it is never
    filled in without a source response. Returns True if a rebuild ran.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='contracts'"
    ).fetchone()
    if not row:
        return False
    ddl = (row[0] or "").replace("\n", " ")
    if "UNIQUE(recipient_name, award_date)" not in ddl:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(contracts)").fetchall()}
        if "verified_at" not in cols:
            conn.execute("ALTER TABLE contracts ADD COLUMN verified_at TEXT")
            conn.commit()
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_contracts_award_id
                        ON contracts(award_id) WHERE award_id IS NOT NULL""")
        conn.commit()
        return False

    print("[RULE_11] migrating `contracts`: dropping UNIQUE(recipient_name, award_date), "
          "keying on award_id")
    before = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]

    # Check the new key BEFORE the destructive step. If duplicates exist the
    # rebuild would commit and then the unique index would raise, leaving a
    # migrated-but-unindexed table and a rule that fails on every later run.
    # Collapse duplicates first, keeping the lowest id (the original ingest).
    dupes = conn.execute(
        """SELECT award_id, COUNT(*) n FROM contracts
           WHERE COALESCE(TRIM(award_id),'') != ''
           GROUP BY award_id HAVING n > 1"""
    ).fetchall()
    if dupes:
        victims = conn.execute(
            """SELECT id, award_id, recipient_name, amount FROM contracts
               WHERE id NOT IN (SELECT MIN(id) FROM contracts
                                WHERE COALESCE(TRIM(award_id),'') != ''
                                GROUP BY award_id)
                 AND COALESCE(TRIM(award_id),'') != ''"""
        ).fetchall()
        print(f"[RULE_11] {len(dupes)} award_id(s) duplicated — collapsing "
              f"{len(victims)} row(s) to the earliest of each before indexing")
        for v in victims:
            print(f"[RULE_11]   removing id={v['id']} {v['award_id']} "
                  f"{v['recipient_name']}")
        conn.execute("""DELETE FROM contracts WHERE id NOT IN (
                            SELECT MIN(id) FROM contracts
                            WHERE COALESCE(TRIM(award_id),'') != ''
                            GROUP BY award_id)
                        AND COALESCE(TRIM(award_id),'') != ''""")
        conn.commit()
        # This is the only row-deleting step in the rule. It runs unattended on
        # deploy, so it leaves a permanent audit row rather than only a log line
        # that scrolls away.
        try:
            from jpt_common import record_activity
            record_activity(
                "RULE_11_MIGRATION", scanned=len(victims), flagged=len(dupes),
                emitted=0, duration_seconds=0.0,
                notes=("collapsed duplicate award_id rows before unique index; "
                       "removed ids=" + ",".join(str(v["id"]) for v in victims)))
        except Exception as e:                                   # noqa: BLE001
            print(f"[RULE_11] (could not record migration activity: {e})")
    conn.executescript("""
        PRAGMA foreign_keys=off;
        BEGIN;
        CREATE TABLE contracts_new (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_name TEXT NOT NULL,
            ticker         TEXT,
            amount         REAL,
            agency         TEXT,
            award_date     TEXT,
            award_id       TEXT,
            description    TEXT,
            ingested_at    TEXT DEFAULT (datetime('now')),
            verified_at    TEXT
        );
        INSERT INTO contracts_new
            (id, recipient_name, ticker, amount, agency, award_date, award_id,
             description, ingested_at, verified_at)
        SELECT id, recipient_name, ticker, amount, agency, award_date,
               NULLIF(TRIM(COALESCE(award_id,'')), ''),  -- '' collides under UNIQUE; NULL does not
               description, ingested_at, NULL
        FROM contracts;
        DROP TABLE contracts;
        ALTER TABLE contracts_new RENAME TO contracts;
        COMMIT;
        PRAGMA foreign_keys=on;
    """)
    # Partial unique index: one row per real award, unlimited legacy NULL-id rows.
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_contracts_award_id
                    ON contracts(award_id) WHERE award_id IS NOT NULL""")
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    print(f"[RULE_11] migration complete — {before} rows in, {after} preserved")
    return True


# ────────────────────────────────── fetch ─────────────────────────────────────
def _post(payload: dict) -> dict:
    r = requests.post(BASE_URL, json=payload, timeout=45,
                      headers={"User-Agent": USER_AGENT})
    if r.status_code != 200:
        raise ContractsFetchError(
            f"HTTP {r.status_code} from spending_by_award: {r.text[:300]}")
    return r.json()


def _fetch_awards(start_date: str, end_date: str, min_amount: int,
                  max_pages: int, archive_dir: str | None = None
                  ) -> tuple[list[dict], bool]:
    """Newly-signed awards in [start_date, end_date] at or above min_amount.

    Returns (results, truncated). `truncated` is True when the page budget ran
    out before the API stopped saying hasNext — the caller MUST report it, so a
    partial sweep is never mistaken for a complete one.

    Raises ContractsFetchError on any non-200: an upstream failure must not look
    like "no new awards".
    """
    results: list[dict] = []
    truncated = False
    for page in range(1, max_pages + 1):
        payload = {
            "filters": {
                # date_type=new_awards_only: awards whose BASE obligation falls in
                # the window. Without it the window filters transaction activity,
                # which is how a 1993 award surfaced in a 2026 scan.
                "time_period": [{"start_date": start_date, "end_date": end_date,
                                 "date_type": "new_awards_only"}],
                "award_type_codes": ["A", "B", "C", "D"],
                "award_amounts": [{"lower_bound": min_amount}],
            },
            "fields": FIELDS,
            "sort": "Award Amount",
            "order": "desc",
            "limit": PAGE_SIZE,
            "page": page,
        }
        data = _post(payload)
        batch = data.get("results", [])
        results.extend(batch)
        if archive_dir:
            os.makedirs(archive_dir, exist_ok=True)
            fname = f"awards_{min_amount}_{start_date}_{end_date}_p{page}.json"
            with open(os.path.join(archive_dir, fname), "w") as fh:
                json.dump(data, fh, indent=1)
        if not data.get("page_metadata", {}).get("hasNext"):
            break
        if page == max_pages:
            truncated = True
        time.sleep(REQUEST_PAUSE)
    return results, truncated


def fetch_award_detail(award_id: str) -> dict | None:
    """Authoritative record for one award, keyed by generated_internal_id.

    Used by the coherence repair to re-derive a row from source. Returns None on
    404 (award withdrawn/re-keyed upstream); raises on other failures.
    """
    r = requests.get(AWARD_URL.format(award_id=award_id), timeout=45,
                     headers={"User-Agent": USER_AGENT})
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise ContractsFetchError(f"HTTP {r.status_code} for award {award_id}")
    return r.json()


# ─────────────────────────────── normalize ────────────────────────────────────
def _award_date(rec: dict) -> str | None:
    """The government's date for this award. NEVER the run date.

    Base Obligation Date is the award's own signing/obligation date; Start Date
    is the period-of-performance start. If both are absent the row is dated NULL
    — an unknown date is recorded as unknown, not as today.
    """
    for key in ("Base Obligation Date", "Start Date"):
        v = rec.get(key)
        if v:
            return str(v)[:10]
    return None


def normalize(rec: dict) -> dict | None:
    """API record -> storable row. None if it lacks a usable identity."""
    award_id = (rec.get("generated_internal_id") or "").strip()
    recipient = (rec.get("Recipient Name") or "").strip()
    if not award_id or not recipient:
        return None
    return {
        "award_id":   award_id,
        "piid":       (rec.get("Award ID") or "").strip(),
        "recipient":  recipient,
        "amount":     float(rec.get("Award Amount") or 0),
        "agency":     (rec.get("Awarding Agency") or "").strip(),
        "award_date": _award_date(rec),
        "desc":       (rec.get("Description") or "").strip()[:500],
        "type":       (rec.get("Contract Award Type") or "").strip(),
    }


def _severity(amount: float, on_watchlist: bool) -> str:
    """Tiers over a single award's obligated value.

    These are the original thresholds, unchanged. They previously produced
    100% CRITICAL only because every amount was a lifetime cumulative total on
    a mega-contract; over real newly-signed award values they spread out.
    """
    if amount >= 500_000_000:
        return "CRITICAL"
    if amount >= 100_000_000:
        return "HIGH"
    if amount >= 10_000_000:
        return "HIGH" if on_watchlist else "MEDIUM"
    if amount >= 1_000_000:
        return "MEDIUM"
    return "LOW"


# ──────────────────────────────── alerts ──────────────────────────────────────
def _ensure_alert_key(conn) -> None:
    """`alerts.award_key` — RULE_11's exact-match dedup column.

    The old dedup did `tags LIKE '%<award_id>%'` OR `ticker + LIKE '%date%'`,
    which over-matched (any alert whose tags happened to contain that date
    string). Additive and nullable, so no other rule is affected.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    if "award_key" not in cols:
        conn.execute("ALTER TABLE alerts ADD COLUMN award_key TEXT")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_alerts_award_key
                        ON alerts(award_key) WHERE award_key IS NOT NULL""")
        conn.commit()


def _emit_alert(conn, row: dict, ticker: str, on_watchlist: bool,
                parent: str = "", confidence: int = 0) -> bool:
    """One alert per award. Dedup is on the award id — exactly, not LIKE."""
    sev = _severity(row["amount"], on_watchlist)
    wl = " 👁 Watchlist" if on_watchlist and row["amount"] < LARGE_THRESHOLD else ""
    when = f" on {row['award_date']}" if row["award_date"] else ""
    headline = (f"Gov Contract — {row['recipient']} awarded "
                f"${row['amount']:,.0f} by {row['agency']}{when}{wl}")
    detail = row["desc"] or (f"${row['amount']:,.0f} awarded{when}"
                             + (f" · {row['type']}" if row["type"] else ""))
    tags = "|".join([row["recipient"], row["award_date"] or "", row["award_id"],
                     parent or "", str(confidence or ""), row["piid"] or ""])

    already = conn.execute(
        "SELECT id FROM alerts WHERE rule='RULE_11' AND award_key = ?",
        (row["award_id"],)).fetchone()
    if already:
        return False

    conn.execute(
        """INSERT INTO alerts (rule, headline, severity, tags, ticker, detail,
                               event_date, award_key)
           VALUES ('RULE_11', ?, ?, ?, ?, ?, ?, ?)""",
        (headline, sev, tags, ticker, detail, row["award_date"], row["award_id"]),
    )
    print(f"[RULE_11] alert {ticker} — {row['recipient']} "
          f"${row['amount']:,.0f} ({sev}) {row['piid']}")
    return True


# ──────────────────────────────── upsert ──────────────────────────────────────
def upsert_award(conn, row: dict, ticker: str | None) -> str:
    """Insert a new award or update the one we already hold. Keyed on award_id.

    Returns 'inserted' | 'updated'. Never silently drops: two different awards
    to one recipient on one date are two different award_ids, hence two rows.
    """
    existing = conn.execute(
        "SELECT id FROM contracts WHERE award_id = ?", (row["award_id"],)
    ).fetchone()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if existing:
        conn.execute(
            """UPDATE contracts SET recipient_name=?, ticker=?, amount=?, agency=?,
                                    award_date=?, description=?, verified_at=?
               WHERE id=?""",
            (row["recipient"], ticker, row["amount"], row["agency"],
             row["award_date"], row["desc"], now, existing[0]),
        )
        return "updated"
    conn.execute(
        """INSERT INTO contracts (recipient_name, ticker, amount, agency,
                                  award_date, award_id, description, verified_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (row["recipient"], ticker, row["amount"], row["agency"],
         row["award_date"], row["award_id"], row["desc"], now),
    )
    return "inserted"


def _watermark(conn) -> str:
    """Scan start = newest confirmed award date minus a lookback.

    Derived from the contracts table itself, so there is no separate state to
    drift. The lookback exists because USASpending posts awards days-to-weeks
    after signing; without it, late-posted awards would fall behind the mark and
    never be seen (the RULE_01B failure mode).
    """
    row = conn.execute(
        "SELECT MAX(award_date) FROM contracts WHERE verified_at IS NOT NULL"
    ).fetchone()
    if not row or not row[0]:
        return YEAR_START
    try:
        mark = datetime.strptime(row[0][:10], "%Y-%m-%d") - timedelta(days=LOOKBACK_DAYS)
    except ValueError:
        return YEAR_START
    return max(mark.strftime("%Y-%m-%d"), YEAR_START)


# ───────────────────────────────── run ────────────────────────────────────────
def run(dry_run: bool = False, full_year: bool = False,
        archive_dir: str | None = None) -> dict:
    """One scan. Records activity on EVERY exit path, including failure."""
    t0 = time.time()
    conn = db_connection()
    stats = {"scanned": 0, "inserted": 0, "updated": 0, "emitted": 0,
             "large_incomplete": False, "watchlist_bounded": False,
             "status": "ok", "error": ""}
    try:
        ensure_contracts_schema(conn)
        _ensure_alert_key(conn)

        ticker_map = [(r["symbol"], r["company_name"] or "")
                      for r in conn.execute(
                          "SELECT symbol, company_name FROM tickers").fetchall()]
        watchlist = {r[0].upper() for r in
                     conn.execute("SELECT symbol FROM watchlist").fetchall()}

        start = YEAR_START if full_year else _watermark(conn)
        print(f"[RULE_11] scanning newly-signed awards {start} → {TODAY}")

        large, trunc_large = _fetch_awards(start, TODAY, LARGE_THRESHOLD,
                                           MAX_PAGES, archive_dir)
        if watchlist:
            small, trunc_small = _fetch_awards(start, TODAY, MIN_AMOUNT,
                                               WATCHLIST_MAX_PAGES, archive_dir)
        else:
            small, trunc_small = [], False
        # Reported separately on purpose: the >=$50M tier is meant to be COMPLETE,
        # so its truncation is a coverage defect. The watchlist tier is bounded by
        # design, so its truncation is expected. Collapsing both into one flag
        # would make an incomplete primary sweep look routine.
        stats["large_incomplete"] = bool(trunc_large)
        stats["watchlist_bounded"] = bool(trunc_small)
        if trunc_large:
            print(f"[RULE_11] ⚠ >=${LARGE_THRESHOLD:,} sweep hit the "
                  f"{MAX_PAGES}-page budget — coverage INCOMPLETE this run")
        if trunc_small:
            print(f"[RULE_11] watchlist sweep bounded at {WATCHLIST_MAX_PAGES} pages "
                  f"(by design; the >=${MIN_AMOUNT:,} population is ~12.7k/yr)")

        seen: dict[str, dict] = {}
        unusable = 0
        for rec in large + small:
            row = normalize(rec)
            if row:
                seen.setdefault(row["award_id"], row)
            else:
                # No id or no recipient: cannot be stored coherently. Counted and
                # reported — the >=$50M tier is meant to be provably complete, so
                # a discard here must never be invisible.
                unusable += 1
        stats["unusable"] = unusable
        if unusable:
            print(f"[RULE_11] ⚠ {unusable} record(s) lacked an award id or "
                  f"recipient and were not stored")
        stats["scanned"] = len(seen)
        print(f"[RULE_11] {len(large)} large + {len(small)} watchlist-tier records "
              f"→ {len(seen)} distinct awards")

        for row in seen.values():
            tkr, parent, conf = resolve_contractor(row["recipient"], ticker_map)
            ticker = tkr if (tkr and conf >= CONTRACTOR_MIN_CONFIDENCE) else None
            on_wl = bool(ticker and ticker.upper() in watchlist)
            if row["amount"] < LARGE_THRESHOLD and not on_wl:
                continue
            if dry_run:
                print(f"[RULE_11] [dry] {row['award_date']} "
                      f"{row['recipient'][:38]:<38} {ticker or '—':<6} "
                      f"${row['amount']:>14,.0f} {_severity(row['amount'], on_wl)}")
                continue
            action = upsert_award(conn, row, ticker)
            stats[action] += 1
            if ticker and _emit_alert(conn, row, ticker, on_wl, parent, conf):
                stats["emitted"] += 1
            conn.commit()

    except ContractsFetchError as e:
        stats["status"], stats["error"] = "fetch_failed", str(e)[:400]
        print(f"[RULE_11] ❌ upstream failure: {e}", file=sys.stderr)
    except Exception as e:                      # noqa: BLE001 — must still log
        stats["status"], stats["error"] = "error", f"{type(e).__name__}: {e}"[:400]
        print(f"[RULE_11] ❌ {type(e).__name__}: {e}", file=sys.stderr)
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
        # Every path logs — success, empty, upstream failure, crash. A failed run
        # must never be indistinguishable from a run that found nothing.
        from jpt_common import record_activity
        notes = (f"status={stats['status']}; inserted={stats['inserted']}; "
                 f"updated={stats['updated']}; "
                 f"large_tier_incomplete={stats['large_incomplete']}; "
                 f"watchlist_tier_bounded={stats['watchlist_bounded']}"
                 + (f"; error={stats['error']}" if stats["error"] else ""))
        record_activity("RULE_11", scanned=stats["scanned"],
                        flagged=stats["inserted"] + stats["updated"],
                        emitted=stats["emitted"],
                        duration_seconds=round(time.time() - t0, 2), notes=notes)
        print(f"[RULE_11] done — {stats['inserted']} new, {stats['updated']} updated, "
              f"{stats['emitted']} alerts, status={stats['status']}")
    return stats


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_11 — Government Contracts")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--full-year", action="store_true",
                   help="ignore the watermark and rescan from Jan 1")
    p.add_argument("--archive-dir", default=None,
                   help="persist raw API responses here")
    p.add_argument("--emit-alerts", action="store_true",
                   help="no-op; alerts are always emitted (scheduler contract)")
    args = p.parse_args()
    out = run(dry_run=args.dry_run, full_year=args.full_year,
              archive_dir=args.archive_dir)
    sys.exit(1 if out["status"] != "ok" else 0)
