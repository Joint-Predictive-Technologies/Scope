#!/usr/bin/env python3
"""
RULE_11 — Government Contracts
Fetches federal contracts from USASpending.gov, matches to tickers,
stores in contracts table, and emits alerts.

- $50M+ contracts are always stored and trigger alerts.
- Sub-$50M contracts are stored/alerted only if ticker is on the watchlist.
"""
from __future__ import annotations

import argparse
import difflib
import os
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection

BASE_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
FUZZY_THRESHOLD = 0.55
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
YEAR_START = f"{datetime.now(timezone.utc).year}-01-01"
MIN_AMOUNT = 1_000_000    # $1M floor for API fetch
LARGE_THRESHOLD = 50_000_000  # $50M: always store/alert


def _fetch_contracts(pages: int = 3) -> list[dict]:
    results: list[dict] = []
    for page in range(1, pages + 1):
        payload = {
            "filters": {
                "time_period": [{"start_date": YEAR_START, "end_date": TODAY}],
                "award_type_codes": ["A", "B", "C", "D"],
                "award_amounts": [{"lower_bound": MIN_AMOUNT}],
            },
            "fields": [
                "Recipient Name",
                "Award Amount",
                "Awarding Agency",
                "Action Date",
                "Period of Performance Start Date",
                "Description",
            ],
            "sort": "Award Amount",
            "order": "desc",
            "limit": 50,
            "page": page,
        }
        try:
            r = requests.post(BASE_URL, json=payload, timeout=30)
            r.raise_for_status()
            batch = r.json().get("results", [])
            results.extend(batch)
            if len(batch) < 50:
                break
        except Exception as e:
            print(f"[RULE_11] USASpending fetch error (page {page}): {e}")
            break
    return results


def _match_ticker(name: str, ticker_map: list[tuple[str, str]]) -> str | None:
    name_lower = name.lower()
    for symbol, company in ticker_map:
        if not company:
            continue
        comp_clean = company.lower().replace(" inc","").replace(" corp","").replace(" llc","").replace(" ltd","").strip()
        name_clean  = name_lower.replace(" inc","").replace(" corp","").replace(" llc","").replace(" ltd","").strip()
        if comp_clean and (comp_clean in name_clean or name_clean in comp_clean):
            return symbol
    best_ratio, best_symbol = 0.0, None
    for symbol, company in ticker_map:
        if not company:
            continue
        ratio = difflib.SequenceMatcher(None, name_lower, company.lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best_symbol = ratio, symbol
    return best_symbol if best_ratio >= FUZZY_THRESHOLD else None


def _severity(amount: float, on_watchlist: bool) -> str:
    if amount >= 500_000_000:
        return "CRITICAL"
    if amount >= 100_000_000:
        return "HIGH"
    if amount >= 10_000_000:
        return "HIGH" if on_watchlist else "MEDIUM"
    if amount >= 1_000_000:
        return "MEDIUM"
    return "LOW"


def _emit_alert(conn, ticker: str, recipient: str, amount: float,
                agency: str, award_date: str, award_id: str,
                desc: str, on_watchlist: bool) -> bool:
    sev = _severity(amount, on_watchlist)
    wl_badge = " 👁 Watchlist" if on_watchlist and amount < LARGE_THRESHOLD else ""
    headline = f"Gov Contract — {recipient} awarded ${amount:,.0f} by {agency}{wl_badge}"
    detail   = desc or f"${amount:,.0f} contract awarded {award_date}"
    tags     = f"{recipient}|{award_date}|{award_id}"

    # Check by award_id OR by ticker+date to avoid duplicates across runs
    already = conn.execute(
        """SELECT id FROM alerts WHERE rule='RULE_11' AND (
               (? != '' AND tags LIKE ?)
               OR (ticker = ? AND tags LIKE ?)
           )""",
        (award_id, f"%{award_id}%", ticker, f"%{award_date}%"),
    ).fetchone()

    if already:
        # Backfill award_id into tags if it's missing (old-style tags lack it)
        if award_id:
            conn.execute(
                "UPDATE alerts SET tags = ? WHERE id = ? AND tags NOT LIKE ?",
                (tags, already[0], f"%{award_id}%"),
            )
        return False

    conn.execute(
        """INSERT INTO alerts (rule, headline, severity, tags, ticker, detail)
           VALUES ('RULE_11', ?, ?, ?, ?, ?)""",
        (headline, sev, tags, ticker, detail),
    )
    print(f"[RULE_11] Emitted alert for {ticker} — {recipient} ${amount:,.0f} ({sev})")
    return True


def run(dry_run: bool = False) -> None:
    conn = db_connection()

    ticker_map: list[tuple[str, str]] = [
        (r["symbol"], r["company_name"] or "")
        for r in conn.execute("SELECT symbol, company_name FROM tickers").fetchall()
    ]
    watchlist_tickers: set[str] = {
        r[0].upper() for r in conn.execute("SELECT symbol FROM watchlist").fetchall()
    }

    contracts = _fetch_contracts()
    print(f"[RULE_11] {len(contracts)} contracts fetched from USASpending.gov")

    stored = skipped = emitted = 0

    for c in contracts:
        recipient  = (c.get("Recipient Name") or "").strip()
        amount     = c.get("Award Amount") or 0
        agency     = (c.get("Awarding Agency") or "").strip()
        award_date = (
            c.get("Action Date")
            or c.get("Period of Performance Start Date")
            or TODAY
        )
        award_date = award_date[:10] if award_date else TODAY
        desc       = (c.get("Description") or "").strip()[:500]
        award_id   = (c.get("generated_internal_id") or "").strip()

        if not recipient:
            continue

        ticker = _match_ticker(recipient, ticker_map)
        on_watchlist = bool(ticker and ticker.upper() in watchlist_tickers)

        # Store only if: above $50M threshold OR watchlist match
        if amount < LARGE_THRESHOLD and not on_watchlist:
            continue

        # Dedup check — prefer award_id, fall back to recipient+date
        exists = conn.execute(
            "SELECT id FROM contracts WHERE award_id = ?", (award_id,)
        ).fetchone() if award_id else None
        if not exists:
            exists = conn.execute(
                "SELECT id FROM contracts WHERE recipient_name = ? AND award_date = ?",
                (recipient, award_date),
            ).fetchone()
            # Backfill award_id on the matched row if we now know it
            if exists and award_id:
                conn.execute("UPDATE contracts SET award_id = ? WHERE id = ?",
                             (award_id, exists[0]))
                conn.commit()

        if exists:
            skipped += 1
            # Still try to emit alert if missing (e.g. after alert purge)
            if ticker and not dry_run:
                if _emit_alert(conn, ticker, recipient, amount, agency,
                               award_date, award_id, desc, on_watchlist):
                    emitted += 1
                    conn.commit()
            continue

        if dry_run:
            wl = " [WATCHLIST]" if on_watchlist else ""
            print(f"[RULE_11] [dry] {recipient} → {ticker or '?'} ${amount:,.0f} {agency}{wl}")
            continue

        conn.execute(
            """INSERT OR IGNORE INTO contracts
               (recipient_name, ticker, amount, agency, award_date, award_id, description)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (recipient, ticker, amount, agency, award_date, award_id, desc),
        )
        stored += 1

        if ticker:
            if _emit_alert(conn, ticker, recipient, amount, agency,
                           award_date, award_id, desc, on_watchlist):
                emitted += 1

        conn.commit()

    # Retroactive: emit alerts for existing contracts that have none
    retro = 0
    rows = conn.execute("""
        SELECT c.id, c.ticker, c.recipient_name, c.amount, c.agency,
               c.award_date, c.award_id, c.description
        FROM contracts c
        WHERE c.ticker IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM alerts a
              WHERE a.rule = 'RULE_11'
                AND (
                    (c.award_id IS NOT NULL AND c.award_id != '' AND a.tags LIKE '%' || c.award_id || '%')
                    OR (COALESCE(c.award_id,'') = '' AND a.ticker = c.ticker AND a.tags LIKE '%' || c.award_date || '%')
                )
          )
    """).fetchall()
    for row in rows:
        on_wl = row["ticker"] in watchlist_tickers
        if not dry_run and _emit_alert(
            conn, row["ticker"], row["recipient_name"], row["amount"] or 0,
            row["agency"] or "", row["award_date"] or TODAY,
            row["award_id"] or "", row["description"] or "", on_wl
        ):
            retro += 1
            conn.commit()

    if retro:
        print(f"[RULE_11] Retroactive: emitted {retro} missing alerts")

    print(f"[RULE_11] Done — {stored} stored, {emitted} new alerts, {skipped} skipped")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_11 — Government Contracts")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(dry_run=args.dry_run)
