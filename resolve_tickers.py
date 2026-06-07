#!/usr/bin/env python3

from __future__ import annotations

import difflib
import json
import sys
import urllib.error
import urllib.request
from typing import Any

from dotenv import load_dotenv

from jpt_common import db_connection


SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_USER_AGENT = "Scope/0.1 contact@example.com"
FUZZY_CUTOFF = 0.7


def ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tickers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT NOT NULL UNIQUE,
            company_name TEXT,
            cik          TEXT,
            updated_at   TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()


def download_sec_tickers() -> dict[str, Any]:
    print(f"Downloading SEC ticker mapping from {SEC_TICKER_URL}...")

    request = urllib.request.Request(
        SEC_TICKER_URL,
        headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                print(f"ERROR: SEC request failed with HTTP {response.status}")
                sys.exit(1)

            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as exc:
        print(f"ERROR: SEC request failed with HTTP {exc.code}: {exc.reason}")
        sys.exit(1)

    except urllib.error.URLError as exc:
        print(f"ERROR: Could not reach SEC endpoint: {exc.reason}")
        sys.exit(1)

    except json.JSONDecodeError as exc:
        print(f"ERROR: SEC response was not valid JSON: {exc}")
        sys.exit(1)


def upsert_sec_tickers(conn, data: dict[str, Any]) -> int:
    rows = []

    for _, item in data.items():
        symbol = str(item.get("ticker", "")).strip().upper()
        company_name = str(item.get("title", "")).strip()
        cik = str(item.get("cik_str", "")).strip()

        if not symbol:
            continue

        rows.append(
            {
                "symbol": symbol,
                "company_name": company_name or None,
                "cik": cik or None,
            }
        )

    if not rows:
        print("ERROR: No tickers found in SEC response.")
        sys.exit(1)

    conn.executemany(
        """
        INSERT INTO tickers (symbol, company_name, cik, updated_at)
        VALUES (:symbol, :company_name, :cik, datetime('now'))
        ON CONFLICT(symbol) DO UPDATE SET
            company_name = excluded.company_name,
            cik = excluded.cik,
            updated_at = datetime('now');
        """,
        rows,
    )
    conn.commit()

    return len(rows)


def load_ticker_maps(conn) -> tuple[dict[str, int], dict[str, int], list[str]]:
    rows = conn.execute(
        """
        SELECT id, symbol, company_name
        FROM tickers
        WHERE symbol IS NOT NULL;
        """
    ).fetchall()

    symbol_to_id: dict[str, int] = {}
    company_to_id: dict[str, int] = {}
    company_names: list[str] = []

    for row in rows:
        ticker_id = int(row["id"])
        symbol = str(row["symbol"] or "").strip().upper()
        company_name = str(row["company_name"] or "").strip()

        if symbol:
            symbol_to_id[symbol] = ticker_id

        if company_name:
            key = company_name.casefold()
            company_to_id[key] = ticker_id
            company_names.append(company_name)

    return symbol_to_id, company_to_id, company_names


def normalize_raw_ticker(value: str | None) -> str:
    if not value:
        return ""

    cleaned = value.strip().upper()
    cleaned = cleaned.replace("$", "")
    cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace(",", "")
    return cleaned


def resolve_by_symbol(raw_ticker_string: str | None, symbol_to_id: dict[str, int]) -> int | None:
    symbol = normalize_raw_ticker(raw_ticker_string)

    if not symbol:
        return None

    return symbol_to_id.get(symbol)


def resolve_by_company_name(
    raw_description: str | None,
    company_to_id: dict[str, int],
    company_names: list[str],
) -> int | None:
    if not raw_description:
        return None

    description = raw_description.strip()

    if not description:
        return None

    matches = difflib.get_close_matches(
        description,
        company_names,
        n=3,
        cutoff=FUZZY_CUTOFF,
    )

    if not matches:
        return None

    best_match = matches[0]
    best_score = difflib.SequenceMatcher(
        None,
        description.casefold(),
        best_match.casefold(),
    ).ratio()

    ambiguous_matches = [
        match
        for match in matches[1:]
        if abs(
            best_score
            - difflib.SequenceMatcher(
                None,
                description.casefold(),
                match.casefold(),
            ).ratio()
        )
        < 0.03
    ]

    if ambiguous_matches:
        return None

    return company_to_id.get(best_match.casefold())


def resolve_transactions(conn) -> tuple[int, int]:
    symbol_to_id, company_to_id, company_names = load_ticker_maps(conn)

    transactions = conn.execute(
        """
        SELECT id, raw_ticker_string, raw_description
        FROM transactions
        WHERE ticker_id IS NULL;
        """
    ).fetchall()

    print(f"Resolving tickers for {len(transactions)} unresolved transactions...")

    resolved = 0
    unresolved = 0

    for tx in transactions:
        tx_id = tx["id"]
        raw_ticker_string = tx["raw_ticker_string"]
        raw_description = tx["raw_description"]

        ticker_id = resolve_by_symbol(raw_ticker_string, symbol_to_id)

        if ticker_id is None:
            ticker_id = resolve_by_company_name(
                raw_description,
                company_to_id,
                company_names,
            )

        if ticker_id is not None:
            conn.execute(
                """
                UPDATE transactions
                SET ticker_id = ?
                WHERE id = ?;
                """,
                (ticker_id, tx_id),
            )
            resolved += 1
        else:
            unresolved += 1
            print(
                "WARNING: Could not resolve transaction "
                f"id={tx_id}, raw_ticker_string={raw_ticker_string!r}, "
                f"raw_description={raw_description!r}"
            )

    conn.commit()
    return resolved, unresolved


def main() -> None:
    load_dotenv()

    with db_connection() as conn:
        ensure_tables(conn)

        data = download_sec_tickers()
        ticker_count = upsert_sec_tickers(conn, data)
        print(f"Upserted {ticker_count} SEC tickers.")

        resolved, unresolved = resolve_transactions(conn)
        print(f"Done. {resolved} resolved, {unresolved} unresolved.")


if __name__ == "__main__":
    main()