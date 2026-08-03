#!/usr/bin/env python3
"""
rule_02_cluster.py

Detects when 3+ members of Congress trade the same ticker DIRECTIONALLY within
a 7-day rolling window and emits a RULE_02 cluster alert.

"Directionally" is load-bearing: a member whose only activity on the ticker in
the window is an exchange is present but expresses no direction, and counting
them inflated both the headline count and its verb.
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
    # Prefix, not equality. The two arms were asymmetric — `startswith("sale")`
    # against `== "purchase"` — so "Purchase (Partial)" read neutral while
    # "Sale (Partial)" read sell. Harmless while a neutral row merely weakened
    # the verb; a SUPPRESSION path once neutral members stopped being counted.
    # `ingest_senate.transaction_verb` already prefixes both.
    if tt.startswith("purchase"):
        return "buy"
    return "neutral"


def member_direction(rows: list[dict]) -> str:
    """buy / sell / mixed / neutral for ONE member, over all their rows on the ticker.

    Composes direction() rather than testing a literal set of transaction_type
    strings. rule_cluster._member_direction keeps its own {"sale", "sale_partial"}
    literal, which is exactly how it came to miss "sale_full"; anything direction()
    learns to classify, this classifies too.

    "neutral" means the member traded but said nothing directional (exchange-only).
    They are present in the window and must NOT be counted as consensus.
    """
    dirs = {direction(r["transaction_type"]) for r in rows}
    has_buy = "buy" in dirs
    has_sell = "sell" in dirs
    if has_buy and has_sell:
        return "mixed"
    if has_buy:
        return "buy"
    if has_sell:
        return "sell"
    return "neutral"


#: A member counts toward the cluster if they traded directionally at all.
#: "mixed" counts — they did trade on a direction — matching how
#: rule_cluster._cluster_direction folds mixed members into its consensus set.
COUNTED_DIRECTIONS = ("buy", "sell", "mixed")


def net_direction(member_dirs: list[str]) -> str:
    """Cluster verb from the per-member directions of the COUNTED members.

    NOTE the buy-vs-sell contest is a MAJORITY, not unanimity — it is deliberately
    NOT the same rule as rule_cluster._cluster_direction, which returns "mixed"
    unless every member agrees. A 3-buy/2-sell cluster still reports NET_LONG here.
    That is pre-existing and out of scope for this fix; it is pinned as a residual
    in tests/test_rule02_directional_count.py.

    An individually MIXED member is different, and does force MIXED. They are
    counted — they did trade directionally — but they are not described by
    "bought" or "sold", and letting them merely abstain reintroduced the exact bug
    this fix exists to remove: on the real corpus, MSFT (one buyer + one member who
    both bought and sold) reported "2 members bought MSFT" on the strength of a
    single buyer. The count and the verb must describe the same members.
    """
    if any(d == "mixed" for d in member_dirs):
        return "MIXED"
    buys = sum(1 for d in member_dirs if d == "buy")
    sells = sum(1 for d in member_dirs if d == "sell")
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

            # Group every row the member has in this window BEFORE deciding
            # their direction. The old code kept each member's FIRST row and
            # voted on that alone, so a member who exchanged on Monday and sold
            # on Tuesday voted "neutral" — the vote depended on row order.
            rows_by_member: dict[str, list[dict]] = defaultdict(list)
            for t in in_window:
                if t["member_id"]:
                    rows_by_member[t["member_id"]].append(t)

            member_dirs = {
                mid: member_direction(rows) for mid, rows in rows_by_member.items()
            }

            # THE FIX: count only members who actually traded directionally.
            # An exchange-only member is present in the window but says nothing
            # about direction, and counting them inflated both the headline
            # number and the verb — "3 members sold WAT" over two exchanges and
            # one partial sale.
            counted = {
                mid for mid, d in member_dirs.items() if d in COUNTED_DIRECTIONS
            }
            if len(counted) < min_members:
                continue

            # Deduplicate windows with the same member set + date span to
            # avoid reporting the same cluster from every anchor point.
            # Keyed on the COUNTED set: two windows differing only by an
            # uncounted member are the same cluster.
            window_key = (
                ticker,
                anchor_date,
                frozenset(counted),
            )
            if window_key in seen_windows:
                continue
            seen_windows.add(window_key)

            # One row per counted member, for naming only.
            seen_ids: set[str] = set()
            deduped: list[dict] = []
            for t in in_window:
                mid = t["member_id"]
                if mid in counted and mid not in seen_ids:
                    seen_ids.add(mid)
                    deduped.append(t)

            nd = net_direction([member_dirs[mid] for mid in counted])
            member_count = len(counted)
            action_word = "bought" if nd == "NET_LONG" else "sold" if nd == "NET_SHORT" else "traded"
            headline = (
                f"Cluster: {member_count} members {action_word} {ticker} "
                f"within 7 days ({nd})"
            )
            severity = "MEDIUM" if nd == "MIXED" else "HIGH"
            # Names are the COUNTED members only — the tag list and the count
            # have to describe the same set, or the receipt contradicts the
            # headline it is meant to evidence.
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
