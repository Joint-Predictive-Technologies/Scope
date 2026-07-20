#!/usr/bin/env python3
"""
rule_02_cluster.py

Detects when 3+ members of Congress trade the same ticker within a 7-day
rolling window and emits a RULE_02 cluster alert.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta

from jpt_common import db_connection


RULE = "RULE_02"
WINDOW_DAYS = 7


def fetch_transactions(conn, days: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            t.member_id,
            t.raw_ticker_string  AS ticker,
            t.transaction_type,
            t.transaction_date,
            m.full_name
        FROM transactions t
        LEFT JOIN members m ON m.bioguide_id = t.member_id
        WHERE t.raw_ticker_string IS NOT NULL
          AND t.transaction_date >= date('now', ?)
          AND t.transaction_date <= date('now')
        ORDER BY t.raw_ticker_string, t.transaction_date
        """,
        (f"-{days} days",),
    ).fetchall()
    return [dict(r) for r in rows]


def direction(transaction_type: str | None) -> str:
    if not transaction_type:
        return "neutral"
    tt = transaction_type.lower()
    if tt.startswith("sale"):
        return "sell"
    if tt == "purchase":
        return "buy"
    return "neutral"


def net_direction(members_in_window: list[dict]) -> str:
    buys = sum(1 for m in members_in_window if direction(m["transaction_type"]) == "buy")
    sells = sum(1 for m in members_in_window if direction(m["transaction_type"]) == "sell")
    if buys > sells:
        return "NET_LONG"
    if sells > buys:
        return "NET_SHORT"
    return "MIXED"


def find_clusters(
    transactions: list[dict], min_members: int
) -> list[dict]:
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for txn in transactions:
        by_ticker[txn["ticker"]].append(txn)

    clusters = []

    for ticker, txns in by_ticker.items():
        txns_sorted = sorted(
            txns,
            key=lambda x: x["transaction_date"] or "",
        )

        # Slide a 7-day window: for each txn as window start, collect all
        # txns within WINDOW_DAYS, then check distinct member count.
        seen_windows: set[tuple] = set()

        for i, anchor in enumerate(txns_sorted):
            anchor_date_str = anchor["transaction_date"]
            if not anchor_date_str:
                continue
            try:
                anchor_date = date.fromisoformat(anchor_date_str)
            except ValueError:
                continue

            window_end = anchor_date + timedelta(days=WINDOW_DAYS - 1)

            in_window: list[dict] = []
            for txn in txns_sorted[i:]:
                txn_date_str = txn["transaction_date"]
                if not txn_date_str:
                    continue
                try:
                    txn_date = date.fromisoformat(txn_date_str)
                except ValueError:
                    continue
                if txn_date > window_end:
                    break
                in_window.append(txn)

            distinct_members = {t["member_id"] for t in in_window if t["member_id"]}
            if len(distinct_members) < min_members:
                continue

            # Deduplicate windows with the same member set + date span to
            # avoid reporting the same cluster from every anchor point.
            window_key = (
                ticker,
                anchor_date,
                frozenset(distinct_members),
            )
            if window_key in seen_windows:
                continue
            seen_windows.add(window_key)

            member_rows = [t for t in in_window if t["member_id"] in distinct_members]
            # One row per distinct member (keep first occurrence each).
            seen_ids: set[str] = set()
            deduped: list[dict] = []
            for t in member_rows:
                if t["member_id"] not in seen_ids:
                    seen_ids.add(t["member_id"])
                    deduped.append(t)

            nd = net_direction(deduped)
            member_count = len(distinct_members)
            action_word = "bought" if nd == "NET_LONG" else "sold" if nd == "NET_SHORT" else "traded"
            headline = (
                f"Cluster: {member_count} members {action_word} {ticker} "
                f"within 7 days ({nd})"
            )
            severity = "MEDIUM" if nd == "MIXED" else "HIGH"
            names = sorted(
                t["full_name"] or t["member_id"]
                for t in deduped
                if t["full_name"] or t["member_id"]
            )
            tags = ",".join(names)

            clusters.append(
                {
                    "ticker": ticker,
                    "headline": headline,
                    "severity": severity,
                    "tags": tags,
                    "member_count": member_count,
                    "net_direction": nd,
                }
            )

    return clusters


def alert_exists(conn, ticker: str, headline: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE rule = ?
          AND ticker = ?
          AND headline = ?
          AND datetime(created_at) >= datetime('now', '-7 days')
        LIMIT 1
        """,
        (RULE, ticker, headline),
    ).fetchone()
    return row is not None


def emit_alerts(conn, clusters: list[dict]) -> int:
    emitted = 0
    for cluster in clusters:
        if alert_exists(conn, cluster["ticker"], cluster["headline"]):
            continue
        conn.execute(
            """
            INSERT INTO alerts (rule, headline, severity, tags, ticker)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                RULE,
                cluster["headline"],
                cluster["severity"],
                cluster["tags"],
                cluster["ticker"],
            ),
        )
        emitted += 1
    conn.commit()
    return emitted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect congressional trading clusters (RULE_02)."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Lookback window in days. Default: 90.",
    )
    parser.add_argument(
        "--min-members",
        type=int,
        default=3,
        help="Minimum distinct members to form a cluster. Default: 3.",
    )
    # Accepted (and ignored) for scheduler-runner uniformity — the scheduler
    # invokes every job with --emit-alerts; without this, argparse would reject it.
    parser.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> None:
    import time as _time
    parser = build_parser()
    args = parser.parse_args()

    _t0 = _time.time()
    with db_connection() as conn:
        transactions = fetch_transactions(conn, args.days)
        clusters = find_clusters(transactions, args.min_members)
        emitted = emit_alerts(conn, clusters)

    print(f"{len(clusters)} clusters found, {emitted} alerts emitted")
    from jpt_common import record_activity
    record_activity("RULE_02", scanned=len(transactions), flagged=len(clusters),
                    emitted=emitted, duration_seconds=round(_time.time() - _t0, 2))


if __name__ == "__main__":
    main()
