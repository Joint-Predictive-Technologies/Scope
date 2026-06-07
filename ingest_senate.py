#!/usr/bin/env python3

from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from datetime import datetime
from typing import Any

import requests
from dotenv import load_dotenv

from jpt_common import AMOUNT_BANDS, db_connection, severity_score


QUIVER_URL = "https://api.quiverquant.com/beta/bulk/congresstrading"
REQUEST_TIMEOUT_SECONDS = 60
HIGH_AMOUNT_THRESHOLD = 50_000
FUZZY_CUTOFF = 0.8


def get_api_key() -> str:
    load_dotenv()
    api_key = os.getenv("QUIVER_API_KEY")

    if not api_key:
        print("ERROR: QUIVER_API_KEY is missing. Add it to your .env file.")
        sys.exit(1)

    return api_key


def fetch_congress_trades(api_key: str) -> list[dict[str, Any]]:
    try:
        response = requests.get(
            QUIVER_URL,
            headers={
                "Authorization": f"Token {api_key}",
                "Accept": "application/json",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()

    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        print(f"ERROR: Quiver Quantitative request failed: HTTP {status_code}")
        sys.exit(1)

    except requests.exceptions.RequestException as exc:
        print(f"ERROR: Quiver Quantitative request failed: {exc}")
        sys.exit(1)

    except ValueError as exc:
        print(f"ERROR: Quiver Quantitative response was not valid JSON: {exc}")
        sys.exit(1)

    if not isinstance(data, list):
        print("ERROR: Unexpected response shape from Quiver Quantitative.")
        sys.exit(1)

    return data


def parse_record(record: dict[str, Any]) -> dict[str, Any] | None:
    representative = str(record.get("Representative") or "").strip()
    transaction_date = str(record.get("Date") or "").strip()

    if not representative or not transaction_date:
        return None

    return {
        "ticker": str(record.get("Ticker") or "").strip().upper() or None,
        "representative": representative,
        "transaction_type": str(record.get("Transaction") or "").strip() or None,
        "amount": str(record.get("Amount") or "").strip() or None,
        "transaction_date": transaction_date,
        "chamber": str(record.get("Chamber") or "").strip() or None,
    }


def normalize_amount_band(raw: str | None) -> str | None:
    if not raw:
        return None

    cleaned = re.sub(r"\s+", " ", raw).strip()

    match = re.match(r"^(\$[\d,]+)\s*-\s*(\$[\d,]+)$", cleaned)
    if match:
        return f"{match.group(1)} - {match.group(2)}"

    return cleaned


def amount_band_floor(amount_band: str | None) -> int | None:
    if not amount_band:
        return None

    match = re.search(r"\$([\d,]+)", amount_band)

    if not match:
        return None

    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def transaction_verb(transaction_type: str | None) -> str:
    value = (transaction_type or "").strip().casefold()

    if value.startswith("purchase"):
        return "bought"
    if value.startswith("sale"):
        return "sold"
    if value.startswith("exchange"):
        return "exchanged"

    return "transacted in"


def member_label_for(representative: str, chamber: str | None) -> str:
    chamber_value = (chamber or "").strip().casefold()

    if chamber_value == "senate":
        prefix = "Sen."
    elif chamber_value == "house":
        prefix = "Rep."
    else:
        prefix = ""

    return f"{prefix} {representative}".strip() or "Unknown member"


def match_member(conn, representative: str) -> str | None:
    normalized = " ".join(representative.strip().casefold().split())

    if not normalized:
        return None

    row = conn.execute(
        """
        SELECT bioguide_id
        FROM members
        WHERE LOWER(TRIM(full_name)) = ?;
        """,
        (normalized,),
    ).fetchone()

    if row is not None:
        return row["bioguide_id"]

    rows = conn.execute(
        """
        SELECT bioguide_id, full_name
        FROM members
        WHERE is_current = 1;
        """
    ).fetchall()

    candidates = {
        str(row["full_name"]).strip(): row["bioguide_id"]
        for row in rows
        if row["full_name"]
    }

    matches = difflib.get_close_matches(
        representative.strip(),
        candidates.keys(),
        n=1,
        cutoff=FUZZY_CUTOFF,
    )

    if matches:
        return candidates[matches[0]]

    return None


def find_existing_transaction(
    conn,
    *,
    member_id: str | None,
    raw_ticker_string: str | None,
    transaction_type: str | None,
    amount_band: str | None,
    transaction_date: str | None,
):
    return conn.execute(
        """
        SELECT id
        FROM transactions
        WHERE COALESCE(member_id, '') = COALESCE(?, '')
          AND COALESCE(raw_ticker_string, '') = COALESCE(?, '')
          AND COALESCE(transaction_type, '') = COALESCE(?, '')
          AND COALESCE(amount_band, '') = COALESCE(?, '')
          AND COALESCE(transaction_date, '') = COALESCE(?, '');
        """,
        (member_id, raw_ticker_string, transaction_type, amount_band, transaction_date),
    ).fetchone()


def insert_transaction(
    conn,
    *,
    member_id: str | None,
    raw_ticker_string: str | None,
    transaction_type: str | None,
    amount_band: str | None,
    transaction_date: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO transactions (
            filing_id, member_id, ticker_id, raw_ticker_string, raw_description,
            transaction_type, amount_band, transaction_date, filing_date
        )
        VALUES (NULL, ?, NULL, ?, NULL, ?, ?, ?, NULL);
        """,
        (member_id, raw_ticker_string, transaction_type, amount_band, transaction_date),
    )
    conn.commit()


def build_alert_tags(amount_band: str | None) -> list[str]:
    tags = []
    floor = amount_band_floor(amount_band)

    if floor is not None and floor > HIGH_AMOUNT_THRESHOLD:
        tags.append("amount_above_50k")

    return tags


def emit_alert(
    conn,
    *,
    member_id: str | None,
    representative: str,
    chamber: str | None,
    symbol: str | None,
    transaction_type: str | None,
    amount_band: str | None,
    raw_amount: str | None,
) -> None:
    amount_label = AMOUNT_BANDS.get(amount_band or "", raw_amount or "unknown amount")
    verb = transaction_verb(transaction_type)
    member_label = member_label_for(representative, chamber)
    ticker_label = symbol or "----"

    headline = f"{member_label} {verb} {ticker_label} ({amount_label})"
    tags = build_alert_tags(amount_band)
    severity = severity_score(tags)

    conn.execute(
        """
        INSERT INTO alerts (rule, headline, severity, tags, ticker, member_id)
        VALUES ('RULE_01', ?, ?, ?, ?, ?);
        """,
        (headline, severity, ",".join(tags), symbol, member_id),
    )
    conn.commit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest congressional PTR trades from the Quiver Quantitative API."
    )
    parser.add_argument(
        "--since",
        required=True,
        help="Only store trades reported on or after this date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--emit-alerts",
        action="store_true",
        help="Emit a RULE_01 alert for each newly stored transaction.",
    )
    return parser


def main() -> None:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    try:
        since_date = datetime.strptime(args.since, "%Y-%m-%d").date()
    except ValueError:
        print("ERROR: --since must be in YYYY-MM-DD format.")
        raise SystemExit(1)

    api_key = get_api_key()
    raw_records = fetch_congress_trades(api_key)
    print(f"Fetched {len(raw_records)} records from Quiver Quantitative.")

    transactions_stored = 0
    alerts_emitted = 0

    with db_connection() as conn:
        member_cache: dict[str, str | None] = {}

        for raw_record in raw_records:
            record = parse_record(raw_record)

            if record is None:
                continue

            try:
                record_date = datetime.strptime(record["transaction_date"], "%Y-%m-%d").date()
            except ValueError:
                continue

            if record_date < since_date:
                continue

            representative = record["representative"]

            if representative not in member_cache:
                member_cache[representative] = match_member(conn, representative)

            member_id = member_cache[representative]
            amount_band = normalize_amount_band(record["amount"])

            if find_existing_transaction(
                conn,
                member_id=member_id,
                raw_ticker_string=record["ticker"],
                transaction_type=record["transaction_type"],
                amount_band=amount_band,
                transaction_date=record["transaction_date"],
            ):
                continue

            insert_transaction(
                conn,
                member_id=member_id,
                raw_ticker_string=record["ticker"],
                transaction_type=record["transaction_type"],
                amount_band=amount_band,
                transaction_date=record["transaction_date"],
            )
            transactions_stored += 1

            if args.emit_alerts:
                emit_alert(
                    conn,
                    member_id=member_id,
                    representative=representative,
                    chamber=record["chamber"],
                    symbol=record["ticker"],
                    transaction_type=record["transaction_type"],
                    amount_band=amount_band,
                    raw_amount=record["amount"],
                )
                alerts_emitted += 1

    print(
        f"Done. Records fetched: {len(raw_records)}. "
        f"Transactions stored: {transactions_stored}. "
        f"Alerts emitted: {alerts_emitted}."
    )


if __name__ == "__main__":
    main()
