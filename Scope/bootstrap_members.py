#!/usr/bin/env python3
"""
bootstrap_members.py

Populate/update the local members table from the unitedstates.io congress-legislators
dataset (free, no API key required, updated daily).
Falls back to Congress.gov API if CONGRESS_API_KEY is set in .env.
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


# ── Data sources ──────────────────────────────────────────────────────────────

CONGRESS_API_URL  = "https://api.congress.gov/v3/member"
PAGE_LIMIT        = 250
REQUEST_TIMEOUT   = 30
DEFAULT_API_KEY   = "DEMO_KEY"

HEADERS = {"User-Agent": "Scope Political Intelligence/1.0"}


# ── DB helpers ────────────────────────────────────────────────────────────────

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


def upsert_members(conn, rows: list[dict[str, Any]]) -> int:
    rows = [r for r in rows if r]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO members (
            bioguide_id, full_name, party, state,
            chamber, district, is_current, url, updated_at
        )
        VALUES (
            :bioguide_id, :full_name, :party, :state,
            :chamber, :district, :is_current, :url, :updated_at
        )
        ON CONFLICT(bioguide_id) DO UPDATE SET
            full_name  = excluded.full_name,
            party      = excluded.party,
            state      = excluded.state,
            chamber    = excluded.chamber,
            district   = excluded.district,
            is_current = excluded.is_current,
            url        = excluded.url,
            updated_at = excluded.updated_at;
        """,
        rows,
    )
    conn.commit()
    return len(rows)


# ── Congress.gov API (DEMO_KEY works without registration) ───────────────────

def _fetch_congress_page(api_key: str, offset: int) -> dict[str, Any] | None:
    params = {"api_key": api_key, "format": "json", "limit": PAGE_LIMIT, "offset": offset}
    r = requests.get(CONGRESS_API_URL, params=params, timeout=REQUEST_TIMEOUT)
    if r.status_code == 429:
        print(f"  Rate-limited at offset={offset} — stopping (DEMO_KEY daily quota reached).")
        return None
    r.raise_for_status()
    return r.json()


def _normalize_congress(member: dict) -> dict[str, Any] | None:
    bioguide_id = member.get("bioguideId")
    full_name   = member.get("name")
    if not bioguide_id or not full_name:
        return None

    terms = member.get("terms", {})
    items = terms.get("item", []) if isinstance(terms, dict) else []
    if isinstance(items, dict):
        items = [items]
    term    = items[-1] if items else {}
    chamber = term.get("chamber") or ("House" if member.get("type") == "Representative" else "Senate" if member.get("type") == "Senator" else None)
    district = str(member.get("district")) if member.get("district") is not None and chamber == "House" else None

    return {
        "bioguide_id": bioguide_id,
        "full_name":   full_name,
        "party":       member.get("partyName") or member.get("party"),
        "state":       member.get("state"),
        "chamber":     chamber,
        "district":    district,
        "is_current":  1 if member.get("currentMember", True) else 0,
        "url":         member.get("url"),
        "updated_at":  datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def run_congress_api(conn, api_key: str) -> int:
    total = 0
    offset = 0
    while True:
        data = _fetch_congress_page(api_key, offset)
        if data is None:
            break
        members = data.get("members", [])
        if not isinstance(members, list):
            break
        rows   = [_normalize_congress(m) for m in members]
        count  = upsert_members(conn, rows)
        total += count
        print(f"  offset={offset}: {len(members)} fetched, {count} upserted (total={total})")
        pagination = data.get("pagination", {})
        if not pagination.get("next") and len(members) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
        time.sleep(0.5)
    print(f"Congress.gov API: {total} upserted.")
    return total


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv()
    api_key = os.getenv("CONGRESS_API_KEY", "").strip()

    with db_connection() as conn:
        ensure_members_table(conn)

        key = api_key or DEFAULT_API_KEY
        if not api_key:
            print(f"CONGRESS_API_KEY not set — using {DEFAULT_API_KEY} (rate-limited).")
        run_congress_api(conn, key)


if __name__ == "__main__":
    main()
