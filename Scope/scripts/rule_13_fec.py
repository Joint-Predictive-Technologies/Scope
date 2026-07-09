#!/usr/bin/env python3
"""
RULE_13 — PAC/FEC Funding Analysis (ongoing monitoring)
Detects significant new PAC contributions to tracked members.

Run daily via cron:
  0 9 * * * cd /path/to/Scope && python scripts/rule_13_fec.py

Requires: FEC_API_KEY in .env
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, SECTOR_MAP, WHY_MATTERS

load_dotenv()
FEC_API_KEY = os.getenv("FEC_API_KEY", "DEMO_KEY")
FEC_BASE    = "https://api.open.fec.gov/v1"

# PAC name patterns linked to foreign-connected or sector-specific funding
DEFENSE_PAC_PATTERNS  = ["defense", "aerospace", "lockheed", "raytheon", "northrop", "boeing", "l3", "leidos"]
ENERGY_PAC_PATTERNS   = ["oil", "gas", "energy", "petroleum", "coal", "nuclear", "lng"]
TECH_PAC_PATTERNS     = ["tech", "silicon", "semiconductor", "software", "digital"]
FINANCE_PAC_PATTERNS  = ["bank", "financial", "securities", "investment", "insurance", "capital"]


def _classify_pac(pac_name: str) -> str:
    low = pac_name.lower()
    if any(p in low for p in DEFENSE_PAC_PATTERNS):
        return "Defense & Aerospace"
    if any(p in low for p in ENERGY_PAC_PATTERNS):
        return "Energy"
    if any(p in low for p in TECH_PAC_PATTERNS):
        return "Technology"
    if any(p in low for p in FINANCE_PAC_PATTERNS):
        return "Finance & Banking"
    return "Other"


def _get_recent_contributions(candidate_id: str, days_back: int = 30) -> list[dict]:
    """Fetch large PAC contributions to a candidate in the last N days."""
    min_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    try:
        resp = requests.get(f"{FEC_BASE}/schedules/schedule_a/",
            params={
                "api_key":       FEC_API_KEY,
                "committee_id":  candidate_id,
                "min_date":      min_date,
                "min_amount":    50000,
                "per_page":      20,
                "sort":          "-contribution_receipt_amount",
            }, timeout=15)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        print(f"[RULE_13] FEC contribution fetch error for {candidate_id}: {e}")
        return []


def run(emit: bool = False) -> None:
    conn = db_connection()

    # Get members with known candidate IDs
    funded_members = conn.execute("""
        SELECT mf.bioguide_id, mf.candidate_id, m.full_name
        FROM member_funding mf
        JOIN members m ON m.bioguide_id = mf.bioguide_id
        WHERE mf.candidate_id IS NOT NULL AND mf.candidate_id != ''
        ORDER BY m.full_name
    """).fetchall()

    if not funded_members:
        print("[RULE_13] No members with FEC data — run bootstrap_fec.py first")
        conn.close()
        return

    print(f"[RULE_13] Checking {len(funded_members)} members for new large contributions")
    alerts_emitted = 0

    for member in funded_members:
        bioguide     = member["bioguide_id"]
        candidate_id = member["candidate_id"]
        name         = member["full_name"]

        contributions = _get_recent_contributions(candidate_id, days_back=30)
        time.sleep(1.0)

        for contrib in contributions:
            pac_name    = contrib.get("contributor_name", "")
            amount      = float(contrib.get("contribution_receipt_amount") or 0)
            contrib_date = contrib.get("contribution_receipt_date", "")
            pac_type    = _classify_pac(pac_name)

            if not emit:
                continue
            if amount < 50000:
                continue

            # Map PAC sector to relevant tickers
            ticker_map = {
                "Defense & Aerospace": "LMT",
                "Energy":              "XOM",
                "Technology":          "NVDA",
                "Finance & Banking":   "GS",
            }
            ticker = ticker_map.get(pac_type, "")

            headline = f"PAC Funding — {name} received ${amount:,.0f} from {pac_type} PAC"
            detail   = (
                f"{pac_name} contributed ${amount:,.0f} to {name} on {contrib_date}. "
                f"Watch for related committee votes or policy positions benefiting {pac_type}."
            )
            severity = "HIGH" if amount >= 200000 else "MEDIUM"
            source_url = f"https://www.fec.gov/data/candidate/{candidate_id}/"
            tags = json.dumps({
                "pac_name":   pac_name,
                "pac_type":   pac_type,
                "amount":     amount,
                "date":       contrib_date,
                "member":     name,
                "bioguide":   bioguide,
                "source":     "FEC",
                "source_url": source_url,
            })
            why = WHY_MATTERS.get("RULE_13",
                "Large PAC contributions from industry-specific groups often precede favorable "
                "committee votes or policy positions that benefit those sectors.")

            existing = conn.execute("""
                SELECT id FROM alerts WHERE rule = 'RULE_13'
                  AND member_id = ? AND tags LIKE ?
                  AND datetime(created_at) >= datetime('now', '-30 days')
            """, (bioguide, f"%{pac_name}%")).fetchone()

            if existing:
                continue

            conn.execute("""
                INSERT INTO alerts
                    (rule, ticker, member_id, headline, detail, severity, tags, source_url, why_matters)
                VALUES ('RULE_13', ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticker, bioguide, headline, detail, severity, tags, source_url, why))
            alerts_emitted += 1
            print(f"[RULE_13] EMIT {severity}: {headline}")

    conn.commit()
    conn.close()
    print(f"[RULE_13] Done — {alerts_emitted} alerts emitted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-alerts", action="store_true")
    args = parser.parse_args()
    run(emit=args.emit_alerts)
