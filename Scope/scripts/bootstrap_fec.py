#!/usr/bin/env python3
"""
Bootstrap FEC — one-time run to build funding profiles for all members in the DB.
Run weekly via cron:
  0 3 * * 0 cd /path/to/Scope && python scripts/bootstrap_fec.py

Requires: FEC_API_KEY in .env (get free key at https://api.data.gov/signup)
DEMO_KEY is allowed but limited to 30 requests/hour — use a real key for full bootstrap.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection

load_dotenv()
FEC_API_KEY = os.getenv("FEC_API_KEY", "DEMO_KEY")
FEC_BASE    = "https://api.open.fec.gov/v1"

INDUSTRY_KEYWORDS = {
    "Defense & Aerospace": [
        "defense", "aerospace", "lockheed", "raytheon", "northrop",
        "general dynamics", "boeing", "l3", "leidos", "saic",
    ],
    "Energy": [
        "oil", "gas", "energy", "petroleum", "coal", "pipeline",
        "exxon", "chevron", "shell", "coal", "lng", "nuclear",
    ],
    "Technology": [
        "technology", "tech", "software", "semiconductor", "silicon",
        "nvidia", "apple", "microsoft", "google", "amazon", "meta",
    ],
    "Finance & Banking": [
        "bank", "financial", "investment", "securities", "insurance",
        "goldman", "jpmorgan", "morgan stanley", "wells fargo",
    ],
    "Healthcare & Pharma": [
        "health", "pharma", "biotech", "hospital", "medical",
        "pfizer", "merck", "abbvie", "insurance", "hmo",
    ],
}


def _classify_industry(committee_name: str, org_type: str = "") -> str:
    name_lower = (committee_name or "").lower()
    for industry, keywords in INDUSTRY_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return industry
    return "Other"


def _get_candidate_id(member_name: str, chamber: str) -> str | None:
    """Search FEC for a candidate ID by member name."""
    office_map = {"Senate": "S", "House": "H"}
    office = office_map.get(chamber, "")
    try:
        params = {
            "q":       member_name,
            "api_key": FEC_API_KEY,
            "per_page": 5,
        }
        if office:
            params["office"] = office

        resp = requests.get(f"{FEC_BASE}/candidates/search/", params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0].get("candidate_id")
    except Exception as e:
        print(f"[FEC] Candidate search error for {member_name}: {e}")
    return None


def _get_fundraising(candidate_id: str) -> dict:
    """Fetch fundraising totals and committee information for a candidate."""
    result = {
        "total_raised": 0.0,
        "pac_funding": 0.0,
        "industry_pcts": {},
        "top_pacs": [],
        "defense_pct": 0.0,
        "energy_pct": 0.0,
        "tech_pct": 0.0,
        "finance_pct": 0.0,
        "pharma_pct": 0.0,
    }
    try:
        # Get candidate totals
        resp = requests.get(f"{FEC_BASE}/candidates/{candidate_id}/totals/",
            params={"api_key": FEC_API_KEY, "per_page": 1}, timeout=15)
        resp.raise_for_status()
        totals = resp.json().get("results", [{}])
        if totals:
            result["total_raised"] = float(totals[0].get("receipts", 0) or 0)

        time.sleep(0.5)

        # Get PAC contributions (itemized)
        resp2 = requests.get(f"{FEC_BASE}/schedules/schedule_b/",
            params={
                "api_key": FEC_API_KEY,
                "committee_id": candidate_id,
                "per_page": 50,
                "sort": "-contribution_receipt_amount",
            }, timeout=15)
        resp2.raise_for_status()
        pacs = resp2.json().get("results", [])

        industry_totals: dict[str, float] = {}
        pac_list = []
        for pac in pacs:
            name   = pac.get("contributor_name", "")
            amount = float(pac.get("contribution_receipt_amount") or 0)
            ind    = _classify_industry(name)
            industry_totals[ind] = industry_totals.get(ind, 0) + amount
            pac_list.append({"name": name, "amount": amount})

        total = sum(industry_totals.values()) or 1
        result["industry_pcts"] = {k: round(v / total * 100, 1) for k, v in industry_totals.items()}
        result["defense_pct"]   = result["industry_pcts"].get("Defense & Aerospace", 0.0)
        result["energy_pct"]    = result["industry_pcts"].get("Energy", 0.0)
        result["tech_pct"]      = result["industry_pcts"].get("Technology", 0.0)
        result["finance_pct"]   = result["industry_pcts"].get("Finance & Banking", 0.0)
        result["pharma_pct"]    = result["industry_pcts"].get("Healthcare & Pharma", 0.0)
        result["top_pacs"]      = sorted(pac_list, key=lambda x: -x["amount"])[:5]
        result["pac_funding"]   = sum(p["amount"] for p in result["top_pacs"])

    except Exception as e:
        print(f"[FEC] Fundraising error for {candidate_id}: {e}")

    return result


def _ensure_tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS member_funding (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        bioguide_id         TEXT UNIQUE,
        candidate_id        TEXT,
        total_raised        REAL DEFAULT 0,
        top_industries      TEXT,
        pac_summary         TEXT,
        defense_pct         REAL DEFAULT 0,
        energy_pct          REAL DEFAULT 0,
        tech_pct            REAL DEFAULT 0,
        finance_pct         REAL DEFAULT 0,
        pharma_pct          REAL DEFAULT 0,
        foreign_connected_pct REAL DEFAULT 0,
        last_updated        TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()


def run() -> None:
    conn = db_connection()
    _ensure_tables(conn)

    members = conn.execute(
        "SELECT bioguide_id, full_name, chamber FROM members WHERE is_current = 1 ORDER BY full_name"
    ).fetchall()

    print(f"[FEC] Processing {len(members)} members")
    processed = 0
    skipped   = 0

    for i, member in enumerate(members):
        bioguide = member["bioguide_id"]
        name     = member["full_name"]
        chamber  = member["chamber"] or "House"

        # Skip if recently updated (within 7 days)
        existing = conn.execute(
            "SELECT last_updated FROM member_funding WHERE bioguide_id = ?", (bioguide,)
        ).fetchone()
        if existing:
            try:
                from datetime import timezone as _tz
                upd = datetime.fromisoformat(existing["last_updated"]).replace(tzinfo=_tz.utc)
                age_days = (datetime.now(_tz.utc) - upd).days
                if age_days < 7:
                    skipped += 1
                    continue
            except Exception:
                pass

        print(f"[FEC] [{i+1}/{len(members)}] {name} ...", end=" ", flush=True)

        candidate_id = _get_candidate_id(name, chamber)
        time.sleep(1.0)

        if not candidate_id:
            print("no FEC match")
            conn.execute("""
                INSERT OR REPLACE INTO member_funding (bioguide_id, candidate_id)
                VALUES (?, NULL)
            """, (bioguide,))
            conn.commit()
            continue

        data = _get_fundraising(candidate_id)
        time.sleep(1.0)

        top_industries = [
            {"industry": ind, "pct": pct}
            for ind, pct in sorted(
                data["industry_pcts"].items(), key=lambda x: -x[1]
            )
        ]

        conn.execute("""
            INSERT OR REPLACE INTO member_funding
                (bioguide_id, candidate_id, total_raised, top_industries, pac_summary,
                 defense_pct, energy_pct, tech_pct, finance_pct, pharma_pct,
                 foreign_connected_pct, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            bioguide,
            candidate_id,
            data["total_raised"],
            json.dumps(top_industries),
            json.dumps({"top_pacs": data["top_pacs"], "pac_total": data["pac_funding"]}),
            data["defense_pct"],
            data["energy_pct"],
            data["tech_pct"],
            data["finance_pct"],
            data["pharma_pct"],
            0.0,
        ))
        conn.commit()
        processed += 1
        print(f"done (raised ${data['total_raised']:,.0f})")

    conn.close()
    print(f"[FEC] Bootstrap complete — {processed} processed, {skipped} skipped (recent)")


if __name__ == "__main__":
    run()
