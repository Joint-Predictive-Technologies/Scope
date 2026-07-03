#!/usr/bin/env python3
"""
bootstrap_members.py

Populate/update the local members table from the Congress.gov API.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv

from jpt_common import db_connection


API_URL = "https://api.congress.gov/v3/member"
PAGE_LIMIT = 250
PAGE_SLEEP_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 30


def get_api_key() -> str:
    load_dotenv()
    api_key = os.getenv("CONGRESS_API_KEY")

    if not api_key:
        print("ERROR: CONGRESS_API_KEY is missing. Add it to your .env file.")
        sys.exit(1)

    return api_key


def fetch_members_page(api_key: str, offset: int) -> dict[str, Any]:
    params = {
        "api_key": api_key,
        "format": "json",
        "limit": PAGE_LIMIT,
        "offset": offset,
    }

    try:
        response = requests.get(
            API_URL,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        body = exc.response.text if exc.response is not None else ""
        print(f"HTTP error while fetching members page offset={offset}: {status_code}")
        if body:
            print(body[:1000])
        sys.exit(1)

    except requests.exceptions.RequestException as exc:
        print(f"Network error while fetching members page offset={offset}: {exc}")
        sys.exit(1)

    except ValueError as exc:
        print(f"Invalid JSON response while fetching members page offset={offset}: {exc}")
        sys.exit(1)


def ensure_members_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS members (
            bioguide_id   TEXT PRIMARY KEY,
            full_name     TEXT NOT NULL,
            party         TEXT,
            state         TEXT,
            chamber       TEXT,
            district      TEXT,
            is_current    INTEGER DEFAULT 1,
            url           TEXT,
            updated_at    TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()


def normalize_chamber(member: dict[str, Any]) -> str | None:
    terms = member.get("terms", {})
    items = terms.get("item", []) if isinstance(terms, dict) else []

    if isinstance(items, dict):
        items = [items]

    if isinstance(items, list) and items:
        latest_term = items[-1]
        chamber = latest_term.get("chamber")
        if chamber:
            return chamber

    member_type = member.get("type")
    if member_type == "Representative":
        return "House"
    if member_type == "Senator":
        return "Senate"

    return None


def normalize_member(member: dict[str, Any]) -> dict[str, Any] | None:
    bioguide_id = member.get("bioguideId")
    full_name = member.get("name")

    if not bioguide_id or not full_name:
        return None

    chamber = normalize_chamber(member)
    district = member.get("district")

    if chamber != "House":
        district = None

    return {
        "bioguide_id": bioguide_id,
        "full_name": full_name,
        "party": member.get("partyName") or member.get("party"),
        "state": member.get("state"),
        "chamber": chamber,
        "district": str(district) if district is not None else None,
        "is_current": 1 if member.get("currentMember", True) else 0,
        "url": member.get("url"),
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def upsert_members(conn, members: list[dict[str, Any]]) -> int:
    rows = [normalize_member(member) for member in members]
    rows = [row for row in rows if row is not None]

    if not rows:
        return 0

    conn.executemany(
        """
        INSERT INTO members (
            bioguide_id,
            full_name,
            party,
            state,
            chamber,
            district,
            is_current,
            url,
            updated_at
        )
        VALUES (
            :bioguide_id,
            :full_name,
            :party,
            :state,
            :chamber,
            :district,
            :is_current,
            :url,
            :updated_at
        )
        ON CONFLICT(bioguide_id) DO UPDATE SET
            full_name = excluded.full_name,
            party = excluded.party,
            state = excluded.state,
            chamber = excluded.chamber,
            district = excluded.district,
            is_current = excluded.is_current,
            url = excluded.url,
            updated_at = excluded.updated_at;
        """,
        rows,
    )
    conn.commit()

    return len(rows)


def has_next_page(data: dict[str, Any], current_page_count: int) -> bool:
    pagination = data.get("pagination", {})

    if isinstance(pagination, dict):
        next_url = pagination.get("next")
        if next_url:
            return True

        count = pagination.get("count")
        offset = pagination.get("offset")

        if isinstance(count, int) and isinstance(offset, int):
            return offset + PAGE_LIMIT < count

    return current_page_count == PAGE_LIMIT


def main() -> None:
    api_key = get_api_key()

    total_fetched = 0
    total_upserted = 0
    offset = 0

    with db_connection() as conn:
        ensure_members_table(conn)

        while True:
            data = fetch_members_page(api_key=api_key, offset=offset)
            members = data.get("members", [])

            if not isinstance(members, list):
                print(f"ERROR: Unexpected API response shape at offset={offset}.")
                sys.exit(1)

            fetched_count = len(members)
            upserted_count = upsert_members(conn, members)

            total_fetched += fetched_count
            total_upserted += upserted_count

            print(
                f"Fetched page offset={offset}: "
                f"{fetched_count} members, "
                f"{upserted_count} upserted "
                f"(total fetched={total_fetched}, total upserted={total_upserted})"
            )

            if not has_next_page(data, fetched_count):
                break

            offset += PAGE_LIMIT
            time.sleep(PAGE_SLEEP_SECONDS)

    print(f"Done. Members fetched: {total_fetched}. Members upserted: {total_upserted}.")


if __name__ == "__main__":
    main()