#!/usr/bin/env python3
"""
Ingest a broad, browsable lobbying dataset from the Senate LDA API.

RULE_09 only flags YoY *spikes* that also match a stock ticker, which leaves the
Lobbying page mostly empty. This job populates `lobbying_filings` with the actual
spend of the most significant lobbies — advocacy groups (AIPAC, NRA, PhRMA, the
US Chamber), defense primes, big tech, pharma, finance, and energy — so the page
shows real, recognizable lobbying activity regardless of whether it spiked.

Clients are fetched by name (bounded + reliable under the LDA throttle) across
recent years. Run weekly via the scheduler, or:
    python scripts/ingest_lobbying.py
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection

LDA_API = "https://lda.senate.gov/api/v1/filings/"
HEADERS = {"User-Agent": "Scope/0.1 sloppysecondstbb@gmail.com"}
SLEEP = 6.0          # ~10 req/min throttle
YEARS = [2024, 2025, 2026]

# Curated notable lobbies, keyed by the category we file them under. The value
# is the client_name search string sent to the LDA API.
CURATED: dict[str, list[str]] = {
    "Advocacy & Foreign": [
        "American Israel Public Affairs Committee",
        "J Street",
        "National Rifle Association",
        "AARP",
        "Planned Parenthood",
        "National Association of Realtors",
        "US Chamber of Commerce",
        "Business Roundtable",
        "American Federation of Labor",
    ],
    "Defense & Aerospace": [
        "Lockheed Martin", "Boeing", "RTX", "Raytheon",
        "Northrop Grumman", "General Dynamics", "L3Harris",
    ],
    "Healthcare & Pharma": [
        "Pharmaceutical Research and Manufacturers",  # PhRMA
        "Pfizer", "Eli Lilly", "Merck", "AbbVie", "Amgen",
    ],
    "Technology": [
        "Meta Platforms", "Alphabet", "Amazon.com", "Microsoft",
        "Apple Inc", "NVIDIA",
    ],
    "Finance & Banking": [
        "JPMorgan", "Goldman Sachs", "BlackRock",
        "American Bankers Association", "Citigroup",
    ],
    "Energy": [
        "Exxon Mobil", "Chevron", "American Petroleum Institute",
        "ConocoPhillips",
    ],
    "Telecom": ["AT&T", "Comcast", "Verizon"],
}

# Fuzzy client-name → ticker for a handful of the biggest names (best-effort).
TICKER_HINTS: dict[str, str] = {
    "lockheed": "LMT", "boeing": "BA", "rtx": "RTX", "raytheon": "RTX",
    "northrop": "NOC", "general dynamics": "GD", "l3harris": "LHX",
    "pfizer": "PFE", "eli lilly": "LLY", "merck": "MRK", "abbvie": "ABBV",
    "amgen": "AMGN", "meta platforms": "META", "alphabet": "GOOGL",
    "amazon": "AMZN", "microsoft": "MSFT", "apple": "AAPL", "nvidia": "NVDA",
    "jpmorgan": "JPM", "goldman": "GS", "blackrock": "BLK", "citigroup": "C",
    "exxon": "XOM", "chevron": "CVX", "conocophillips": "COP",
    "at&t": "T", "comcast": "CMCSA", "verizon": "VZ",
}


def _amount(f: dict) -> float:
    for k in ("income", "expenses"):
        v = f.get(k)
        if v:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return 0.0


def _ticker_for(client: str) -> str:
    low = client.lower()
    for needle, sym in TICKER_HINTS.items():
        if needle in low:
            return sym
    return ""


def _issues(f: dict) -> str:
    seen: list[str] = []
    for act in (f.get("lobbying_activities") or []):
        code = act.get("general_issue_code_display") or ""
        if code and code not in seen:
            seen.append(code)
    return ", ".join(seen[:5])


def _fetch_client(client_name: str, year: int) -> list[dict]:
    time.sleep(SLEEP)
    try:
        r = requests.get(
            LDA_API, headers=HEADERS,
            params={"client_name": client_name, "filing_year": year, "page_size": 25},
            timeout=30,
        )
        if r.status_code == 429:
            time.sleep(15)
            r = requests.get(
                LDA_API, headers=HEADERS,
                params={"client_name": client_name, "filing_year": year, "page_size": 25},
                timeout=30,
            )
        if r.status_code != 200:
            return []
        return r.json().get("results") or []
    except requests.RequestException:
        return []


def run() -> int:
    conn = db_connection()
    ingested = 0

    for category, clients in CURATED.items():
        for client in clients:
            for year in YEARS:
                for f in _fetch_client(client, year):
                    uuid = f.get("filing_uuid")
                    if not uuid:
                        continue
                    client_name = ((f.get("client") or {}).get("name") or "").strip()
                    registrant  = ((f.get("registrant") or {}).get("name") or "").strip()
                    amount      = _amount(f)
                    is_foreign  = 1 if (f.get("foreign_entities") or []) else 0
                    try:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO lobbying_filings
                                (filing_uuid, client_name, registrant_name, amount,
                                 filing_year, period, issues, category, ticker,
                                 is_foreign, document_url)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                uuid, client_name, registrant, amount,
                                f.get("filing_year"), f.get("filing_type_display") or "",
                                _issues(f), category, _ticker_for(client_name),
                                is_foreign, f.get("filing_document_url") or f.get("url") or "",
                            ),
                        )
                        ingested += 1
                    except Exception:
                        pass
                conn.commit()
            print(f"  [{category}] {client}: done", flush=True)

    total = conn.execute("SELECT COUNT(*) FROM lobbying_filings").fetchone()[0]
    conn.close()
    print(f"[LOBBYING] ingested/updated {ingested} filings — {total} total in table")
    return ingested


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest notable lobbying filings from Senate LDA.")
    parser.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    parser.parse_args()
    run()
