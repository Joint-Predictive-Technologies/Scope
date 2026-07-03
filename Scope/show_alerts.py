#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from dotenv import load_dotenv

from jpt_common import db_connection


def ensure_watchlist_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol    TEXT NOT NULL UNIQUE,
            added_at  TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()


def load_watchlist(conn) -> set[str]:
    rows = conn.execute(
        """
        SELECT symbol
        FROM watchlist;
        """
    ).fetchall()

    return {str(row["symbol"]).strip().upper() for row in rows if row["symbol"]}


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    value = str(value).strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(value[:19] if "T" in value else value[:10] if fmt == "%Y-%m-%d" else value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def time_ago(value: str | None) -> str:
    dt = parse_datetime(value)

    if dt is None:
        return "unknown"

    now = datetime.now(timezone.utc)
    delta = now - dt

    seconds = max(0, int(delta.total_seconds()))
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24

    if days > 0:
        return f"{days}d ago"
    if hours > 0:
        return f"{hours}h ago"
    if minutes > 0:
        return f"{minutes}m ago"
    return "just now"


def clean_rule(rule: str | None) -> str:
    if not rule:
        return "RULE ??"

    value = str(rule).strip().upper()

    if value.startswith("RULE"):
        return value

    if value.isdigit():
        return f"RULE {int(value):02d}"

    return value


def clean_symbol(symbol: str | None) -> str:
    if not symbol:
        return "----"

    return str(symbol).strip().upper()


def clean_member(row) -> str:
    full_name = row["full_name"]

    if not full_name:
        return "Unknown Member"

    party = row["party"] or ""
    state = row["state"] or ""

    suffix = ""
    if party or state:
        suffix = f" ({party}-{state})".replace("(-", "(").replace("-)", ")")

    return f"{full_name}{suffix}"


def clean_headline(row) -> str:
    headline = row["headline"]

    if headline:
        return str(headline).strip()

    severity = row["severity"]
    tags = row["tags"]

    parts = []

    if severity:
        parts.append(str(severity).strip())

    if tags:
        parts.append(str(tags).strip())

    return " ".join(parts) if parts else "Alert"


def fetch_alerts(conn, days: int, watchlist_only: bool, watchlist: set[str]):
    rows = conn.execute(
        """
        SELECT
            alerts.id,
            alerts.rule,
            alerts.headline,
            alerts.severity,
            alerts.tags,
            alerts.ticker,
            alerts.member_id,
            alerts.created_at,
            members.full_name,
            members.party,
            members.state,
            members.chamber
        FROM alerts
        LEFT JOIN members
            ON members.bioguide_id = alerts.member_id
        WHERE datetime(alerts.created_at) >= datetime('now', ?)
        ORDER BY datetime(alerts.created_at) DESC;
        """,
        (f"-{days} days",),
    ).fetchall()

    if not watchlist_only:
        return rows

    return [
        row
        for row in rows
        if clean_symbol(row["ticker"]) in watchlist
    ]


def print_alert_feed(rows, watchlist: set[str]) -> None:
    if not rows:
        print("No alerts found.")
        return

    for row in rows:
        symbol = clean_symbol(row["ticker"])
        is_watchlist = symbol in watchlist

        prefix = "★ WATCHLIST" if is_watchlist else ""
        rule = clean_rule(row["rule"])
        member = clean_member(row)
        headline = clean_headline(row)
        ago = time_ago(row["created_at"])

        print(
            f"{prefix:<12} | "
            f"{rule:<7} | "
            f"{symbol:<6} | "
            f"{member:<28} | "
            f"{headline:<20} | "
            f"{ago}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show Scope alert feed.")
    parser.add_argument(
        "--watchlist-only",
        action="store_true",
        help="Only show alerts for watched tickers.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Lookback window in days. Default: 30.",
    )
    return parser


def main() -> None:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    if args.days <= 0:
        print("ERROR: --days must be greater than 0.")
        raise SystemExit(1)

    with db_connection() as conn:
        ensure_watchlist_table(conn)

        watchlist = load_watchlist(conn)
        rows = fetch_alerts(
            conn=conn,
            days=args.days,
            watchlist_only=args.watchlist_only,
            watchlist=watchlist,
        )

    print_alert_feed(rows, watchlist)


if __name__ == "__main__":
    main()