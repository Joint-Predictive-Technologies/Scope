#!/usr/bin/env python3
"""
RULE_11 — Government Contracts
Fetches $50M+ federal contracts from USASpending.gov, matches to tickers,
emits alerts, and stores raw data in the contracts table.
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


def _fetch_contracts(pages: int = 3) -> list[dict]:
    results: list[dict] = []
    for page in range(1, pages + 1):
        payload = {
            "filters": {
                "time_period": [{"start_date": YEAR_START, "end_date": TODAY}],
                "award_type_codes": ["A", "B", "C", "D"],
                "award_amounts": [{"lower_bound": 50_000_000}],
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
    """Return best matching ticker symbol for a recipient name."""
    name_lower = name.lower()

    # Direct substring match first (fast path)
    for symbol, company in ticker_map:
        if not company:
            continue
        comp_lower = company.lower()
        # Strip common suffixes for comparison
        comp_clean = comp_lower.replace(" inc", "").replace(" corp", "").replace(" llc", "").replace(" ltd", "").strip()
        name_clean  = name_lower.replace(" inc", "").replace(" corp", "").replace(" llc", "").replace(" ltd", "").strip()
        if comp_clean and (comp_clean in name_clean or name_clean in comp_clean):
            return symbol

    # Fuzzy fallback
    best_ratio  = 0.0
    best_symbol = None
    for symbol, company in ticker_map:
        if not company:
            continue
        ratio = difflib.SequenceMatcher(None, name_lower, company.lower()).ratio()
        if ratio > best_ratio:
            best_ratio  = ratio
            best_symbol = symbol
    return best_symbol if best_ratio >= FUZZY_THRESHOLD else None


def run(emit: bool = False, dry_run: bool = False) -> None:
    conn = db_connection()

    # Load tickers for matching
    ticker_map: list[tuple[str, str]] = [
        (r["symbol"], r["company_name"] or "")
        for r in conn.execute("SELECT symbol, company_name FROM tickers").fetchall()
    ]

    contracts = _fetch_contracts()
    print(f"[RULE_11] {len(contracts)} contracts fetched from USASpending.gov")

    emitted = skipped = 0

    for c in contracts:
        recipient  = (c.get("Recipient Name") or "").strip()
        amount     = c.get("Award Amount") or 0
        agency     = (c.get("Awarding Agency") or "").strip()
        # Date fallbacks: Action Date → Period start → today
        award_date = (
            c.get("Action Date")
            or c.get("Period of Performance Start Date")
            or TODAY
        )
        award_date = award_date[:10] if award_date else TODAY
        desc       = (c.get("Description") or "").strip()[:500]
        internal_id = str(c.get("internal_id") or c.get("generated_internal_id") or "")

        if not recipient:
            continue

        # Dedup by internal_id when available, else (recipient, award_date)
        if internal_id:
            exists = conn.execute(
                "SELECT 1 FROM contracts WHERE recipient_name = ? AND description LIKE ?",
                (recipient, f"%{internal_id}%"),
            ).fetchone()
        else:
            exists = conn.execute(
                "SELECT 1 FROM contracts WHERE recipient_name = ? AND award_date = ?",
                (recipient, award_date),
            ).fetchone()
        if exists:
            skipped += 1
            continue

        ticker = _match_ticker(recipient, ticker_map)

        if dry_run:
            print(f"[RULE_11] [dry] {recipient} → {ticker or '?'} ${amount:,.0f} {agency}")
            continue

        # Store raw contract — embed internal_id in description for dedup
        store_desc = f"{desc}|{internal_id}" if internal_id else desc
        conn.execute(
            """INSERT OR IGNORE INTO contracts
               (recipient_name, ticker, amount, agency, award_date, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (recipient, ticker, amount, agency, award_date, store_desc),
        )

        if ticker and emit:
            sev = "CRITICAL" if amount > 500_000_000 else "HIGH" if amount > 100_000_000 else "MEDIUM"
            headline = f"Gov Contract — {recipient} awarded ${amount:,.0f} by {agency}"
            detail   = desc or f"${amount:,.0f} contract awarded {award_date}"

            already = conn.execute(
                "SELECT 1 FROM alerts WHERE rule='RULE_11' AND ticker=? AND tags LIKE ?",
                (ticker, f"%{award_date}%"),
            ).fetchone()
            if not already:
                conn.execute(
                    """INSERT INTO alerts (rule, headline, severity, tags, ticker, detail)
                       VALUES ('RULE_11', ?, ?, ?, ?, ?)""",
                    (headline, sev, f"{recipient},{award_date}", ticker, detail),
                )
                emitted += 1
                print(f"[RULE_11] [emit] {ticker} — {recipient} ${amount:,.0f} ({sev})")

        conn.commit()

    print(f"[RULE_11] Done — {emitted} alerts emitted, {skipped} duplicates skipped")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_11 — Government Contracts")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
