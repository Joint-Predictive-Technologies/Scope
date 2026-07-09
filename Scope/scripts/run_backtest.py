#!/usr/bin/env python3
"""
Batch backtest runner.
Fetches Yahoo Finance prices for all historical alerts (older than 30 days)
that don't yet have backtest data. Safe to run daily via cron.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection

_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def _fetch_prices(ticker: str, alert_ts: str) -> dict | None:
    try:
        dt = datetime.fromisoformat(alert_ts.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        p1 = int(dt.timestamp())
        p2 = p1 + 33 * 86400

        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?interval=1d&period1={p1}&period2={p2}"
        )
        r = requests.get(url, headers=_YF_HEADERS, timeout=10)
        data = r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None

        price_at  = round(closes[0], 4)
        price_7d  = round(closes[min(7,  len(closes) - 1)], 4)
        price_30d = round(closes[min(30, len(closes) - 1)], 4)
        ret_7d  = round(((price_7d  - price_at) / price_at) * 100, 2)
        ret_30d = round(((price_30d - price_at) / price_at) * 100, 2)
        return {
            "price_at": price_at, "price_7d": price_7d, "price_30d": price_30d,
            "return_7d": ret_7d, "return_30d": ret_30d,
        }
    except Exception:
        return None


def run(limit: int = 100, dry_run: bool = False) -> None:
    conn = db_connection()

    alerts = conn.execute("""
        SELECT a.id, a.ticker, a.created_at, a.rule
        FROM alerts a
        LEFT JOIN backtest_results b ON a.id = b.alert_id
        WHERE a.ticker IS NOT NULL
          AND a.ticker != ''
          AND a.ticker NOT LIKE '% %'
          AND b.alert_id IS NULL
          AND datetime(a.created_at) <= datetime('now', '-30 days')
        ORDER BY a.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()

    print(f"[run_backtest] {len(alerts)} alerts need price data")
    ok = skipped = 0

    for row in alerts:
        alert_id = row["id"]
        ticker   = (row["ticker"] or "").replace("$", "").split()[0]
        if not ticker:
            continue

        prices = _fetch_prices(ticker, row["created_at"])
        if not prices:
            skipped += 1
            print(f"[run_backtest] [skip] {ticker} #{alert_id} — no data from Yahoo")
            time.sleep(0.3)
            continue

        if dry_run:
            print(f"[run_backtest] [dry] {ticker} #{alert_id} — {prices['return_30d']:+.1f}% 30d")
            ok += 1
            continue

        conn.execute(
            """INSERT OR REPLACE INTO backtest_results
               (alert_id, ticker, price_at, price_7d, price_30d, return_7d, return_30d)
               VALUES (?,?,?,?,?,?,?)""",
            (alert_id, ticker, prices["price_at"], prices["price_7d"],
             prices["price_30d"], prices["return_7d"], prices["return_30d"]),
        )
        conn.commit()
        ok += 1
        print(f"[run_backtest] {ticker} #{alert_id} {row['rule']} — {prices['return_30d']:+.1f}% 30d")
        time.sleep(0.5)

    print(f"[run_backtest] Done — {ok} fetched, {skipped} skipped")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Batch backtest runner")
    p.add_argument("--limit",    type=int, default=100, help="Max alerts to process")
    p.add_argument("--dry-run",  action="store_true")
    p.add_argument("--emit-alerts", action="store_true", help="no-op; for scheduler compat")
    args = p.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)
