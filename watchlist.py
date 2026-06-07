#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys

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


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace("$", "")


def add_symbol(symbol: str) -> None:
    symbol = normalize_symbol(symbol)

    if not symbol:
        print("ERROR: Symbol cannot be empty.")
        sys.exit(1)

    with db_connection() as conn:
        ensure_watchlist_table(conn)

        conn.execute(
            """
            INSERT OR IGNORE INTO watchlist (symbol)
            VALUES (?);
            """,
            (symbol,),
        )
        conn.commit()

        print(f"Added {symbol} to watchlist.")


def remove_symbol(symbol: str) -> None:
    symbol = normalize_symbol(symbol)

    if not symbol:
        print("ERROR: Symbol cannot be empty.")
        sys.exit(1)

    with db_connection() as conn:
        ensure_watchlist_table(conn)

        cursor = conn.execute(
            """
            DELETE FROM watchlist
            WHERE symbol = ?;
            """,
            (symbol,),
        )
        conn.commit()

        if cursor.rowcount:
            print(f"Removed {symbol} from watchlist.")
        else:
            print(f"{symbol} was not in watchlist.")


def list_symbols() -> None:
    with db_connection() as conn:
        ensure_watchlist_table(conn)

        rows = conn.execute(
            """
            SELECT symbol, added_at
            FROM watchlist
            ORDER BY symbol ASC;
            """
        ).fetchall()

    if not rows:
        print("Watchlist is empty.")
        return

    for row in rows:
        print(f"{row['symbol']}  added_at={row['added_at']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Scope ticker watchlist.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add ticker to watchlist.")
    add_parser.add_argument("symbol", help="Ticker symbol, e.g. MSTR")

    remove_parser = subparsers.add_parser("remove", help="Remove ticker from watchlist.")
    remove_parser.add_argument("symbol", help="Ticker symbol, e.g. MSTR")

    subparsers.add_parser("list", help="List watched tickers.")

    return parser


def main() -> None:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "add":
        add_symbol(args.symbol)
    elif args.command == "remove":
        remove_symbol(args.symbol)
    elif args.command == "list":
        list_symbols()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()