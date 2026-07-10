#!/usr/bin/env python3
"""
RULE_01B — First Touch
Fires when a member of Congress makes their very first disclosed trade in a
ticker (no prior record for this member+ticker combination in transactions).

Tags format (pipe-separated to avoid comma collisions with member names):
    full_name|tx_type|delay_days|transaction_date|filing_date
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection


def _is_above_15k(band: str) -> bool:
    return bool(band) and band not in {"$1,001 - $15,000", "$1k–15k", "$1,001-$15,000"}


def run(emit: bool = False, dry_run: bool = False) -> None:
    conn = db_connection()

    rows = conn.execute("""
        SELECT
            t.id,
            t.member_id,
            t.raw_ticker_string  AS ticker,
            t.transaction_type,
            t.amount_band,
            t.transaction_date,
            t.filing_date,
            CAST(julianday(t.filing_date) - julianday(t.transaction_date) AS INTEGER) AS filing_delay,
            m.full_name,
            m.party,
            m.state
        FROM transactions t
        JOIN members m ON t.member_id = m.bioguide_id
        WHERE t.raw_ticker_string IS NOT NULL
          AND trim(t.raw_ticker_string) != ''
          AND t.member_id IS NOT NULL
          AND date(t.transaction_date) >= date('now', '-90 days')
          AND NOT EXISTS (
              SELECT 1 FROM transactions t2
              WHERE t2.member_id = t.member_id
                AND t2.raw_ticker_string = t.raw_ticker_string
                AND t2.id < t.id
          )
        ORDER BY t.transaction_date DESC
        LIMIT 500
    """).fetchall()

    print(f"[RULE_01B] {len(rows)} first-touch transactions found")
    emitted = 0

    for r in rows:
        ticker     = r["ticker"]
        member_id  = r["member_id"]
        name       = r["full_name"]
        party      = r["party"] or "?"
        state      = r["state"] or "?"
        tx_type    = (r["transaction_type"] or "trade").replace("_", " ")
        amount     = r["amount_band"] or "undisclosed amount"
        delay      = r["filing_delay"]
        tx_date    = r["transaction_date"] or ""
        filed_date = r["filing_date"] or ""

        exists = conn.execute(
            "SELECT 1 FROM alerts WHERE rule='RULE_01B' AND ticker=? AND member_id=?",
            (ticker, member_id),
        ).fetchone()
        if exists:
            continue

        severity = "HIGH" if _is_above_15k(amount) else "MEDIUM"
        headline = f"First Touch — {name} opens new position in {ticker}"
        detail = (
            f"{name} ({party}-{state}) has no prior disclosed trade in {ticker}. "
            f"Transaction: {tx_type}, {amount}"
            + (f", filed {delay}d after transaction." if delay is not None else ".")
        )
        tags = f"{name}|{tx_type}|{delay if delay is not None else '?'}|{tx_date}|{filed_date}"

        print(f"[RULE_01B] {'[dry]' if dry_run else '[emit]'} {ticker} — {name} ({severity})")

        if not dry_run and emit:
            conn.execute(
                """INSERT INTO alerts (rule, headline, severity, tags, ticker, member_id, detail)
                   VALUES ('RULE_01B', ?, ?, ?, ?, ?, ?)""",
                (headline, severity, tags, ticker, member_id, detail),
            )
            conn.commit()
            emitted += 1

    print(f"[RULE_01B] Done — {emitted} new alerts emitted")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_01B — First-touch congressional trades")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
