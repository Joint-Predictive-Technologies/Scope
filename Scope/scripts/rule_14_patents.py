#!/usr/bin/env python3
"""
RULE_14 — USPTO Patent Filing Signals
Source: PatentsView API — patentsview.org (free, no key required)

Run daily via cron:
  0 8 * * * cd /path/to/Scope && python scripts/rule_14_patents.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, SECTOR_MAP, WHY_MATTERS

PATENTS_BASE = "https://api.patentsview.org/patents/query"
HEADERS = {
    "User-Agent": "Scope Political Intelligence research@jointpredictive.com",
    "Content-Type": "application/json",
}

PATENT_KEYWORDS: dict[str, list[str]] = {
    "Defense & Aerospace": [
        "hypersonic", "directed energy weapon", "autonomous weapon",
        "drone swarm", "missile defense", "electronic warfare", "stealth aircraft",
        "satellite communication", "radar system", "munition",
    ],
    "Semiconductors": [
        "EUV lithography", "chip packaging", "advanced node",
        "gate-all-around transistor", "quantum computing chip",
        "semiconductor fabrication", "photolithography",
    ],
    "Energy": [
        "small modular reactor", "nuclear fusion", "carbon capture",
        "hydrogen fuel cell", "grid scale storage", "offshore wind turbine",
        "geothermal energy",
    ],
    "Healthcare & Pharma": [
        "mRNA vaccine", "gene therapy", "CRISPR", "biosimilar",
        "antiviral compound", "immunotherapy", "CAR-T cell",
    ],
}

# Tickers whose assignees to track
TRACKED_ASSIGNEES: dict[str, list[str]] = {
    "LMT":  ["Lockheed Martin"],
    "RTX":  ["Raytheon", "RTX Corporation"],
    "NOC":  ["Northrop Grumman"],
    "BA":   ["Boeing"],
    "GD":   ["General Dynamics"],
    "NVDA": ["NVIDIA"],
    "AMD":  ["Advanced Micro Devices"],
    "TSM":  ["Taiwan Semiconductor"],
    "INTC": ["Intel"],
    "AVGO": ["Broadcom"],
    "PFE":  ["Pfizer"],
    "MRK":  ["Merck"],
    "ABBV": ["AbbVie"],
    "LLY":  ["Eli Lilly"],
    "AMGN": ["Amgen"],
    "GILD": ["Gilead"],
    "XOM":  ["ExxonMobil", "Exxon Mobil"],
    "CVX":  ["Chevron"],
}


def _fetch_patents(keywords: list[str], days_back: int = 90) -> list[dict]:
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    query = {
        "q": {"_and": [
            {"_gte": {"patent_date": start_date}},
            {"_or": [{"_text_phrase": {"patent_abstract": kw}} for kw in keywords[:5]]},
        ]},
        "f": ["patent_number", "patent_title", "patent_abstract",
              "patent_date", "assignee_organization", "assignee_key_id"],
        "o": {"per_page": 50},
    }
    try:
        resp = requests.post(PATENTS_BASE, json=query, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json().get("patents") or []
    except Exception as e:
        print(f"[RULE_14] PatentsView error for {keywords[0]}: {e}")
        return []


def _ticker_for_assignee(assignee: str) -> str:
    if not assignee:
        return ""
    for ticker, names in TRACKED_ASSIGNEES.items():
        for name in names:
            if name.lower() in assignee.lower():
                return ticker
    return ""


def _ensure_tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS patent_filings (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        patent_number TEXT UNIQUE,
        patent_title  TEXT,
        patent_date   TEXT,
        assignee      TEXT,
        ticker        TEXT,
        category      TEXT,
        keywords_hit  TEXT,
        ingested_at   TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()


def run(emit: bool = False) -> None:
    conn = db_connection()
    _ensure_tables(conn)

    ingested = 0
    alerts_emitted = 0

    for category, keywords in PATENT_KEYWORDS.items():
        patents = _fetch_patents(keywords, days_back=90)
        time.sleep(1)

        assignee_counts: dict[str, list] = {}

        for p in patents:
            if not isinstance(p, dict):
                continue
            pnum      = p.get("patent_number", "")
            title     = p.get("patent_title", "")
            date      = p.get("patent_date", "")
            assignees = p.get("assignee_organization") or []
            if isinstance(assignees, str):
                assignees = [assignees]
            if not assignees:
                continue

            assignee = assignees[0] if assignees else ""
            ticker   = _ticker_for_assignee(assignee)

            # Find which keywords matched
            abstract = (p.get("patent_abstract") or "").lower()
            kws_hit  = [kw for kw in keywords if kw.lower() in abstract]

            try:
                conn.execute("""
                    INSERT OR IGNORE INTO patent_filings
                        (patent_number, patent_title, patent_date, assignee, ticker, category, keywords_hit)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (pnum, title, date, assignee, ticker, category, json.dumps(kws_hit)))
                ingested += 1
            except Exception:
                pass

            if assignee:
                if assignee not in assignee_counts:
                    assignee_counts[assignee] = []
                assignee_counts[assignee].append({"title": title, "date": date, "ticker": ticker})

        conn.commit()

        if not emit:
            continue

        # ── Signal: filing cluster (5+ patents from same company in 90 days) ──
        for assignee, filings in assignee_counts.items():
            count = len(filings)
            if count < 5:
                continue

            ticker = filings[0]["ticker"] if filings else ""
            severity = "HIGH" if count >= 10 else "MEDIUM"
            recent_titles = [f["title"] for f in filings[:3] if f["title"]]
            titles_str    = "; ".join(recent_titles) if recent_titles else ""

            headline = f"Patent Cluster — {assignee} filed {count} {category} patents in 90 days"
            detail   = (
                f"Recent patents: {titles_str}. "
                f"This level of R&D activity in {category} suggests anticipated growth in this vertical."
            )
            tags = json.dumps({
                "assignee": assignee,
                "category": category,
                "patent_count": count,
                "source": "USPTO/PatentsView",
                "source_url": f"https://patentsview.org/search/real/result/?q={{\"assignee_organization\":\"{assignee}\"}}&f=[\"patent_number\",\"patent_title\",\"patent_date\"]",
            })
            why = WHY_MATTERS.get("RULE_14",
                "Patent filing clusters signal strategic R&D investment that often precedes product launches, "
                "regulatory approvals, or government contract awards.")

            # Deduplicate: one alert per assignee+category per 90-day window
            existing = conn.execute("""
                SELECT id FROM alerts
                WHERE rule = 'RULE_14'
                  AND tags LIKE ?
                  AND datetime(created_at) >= datetime('now', '-90 days')
            """, (f"%{assignee}%",)).fetchone()

            if existing:
                continue

            conn.execute("""
                INSERT INTO alerts (rule, ticker, headline, detail, severity, tags, why_matters)
                VALUES ('RULE_14', ?, ?, ?, ?, ?, ?)
            """, (ticker, headline, detail, severity, tags, why))
            alerts_emitted += 1
            print(f"[RULE_14] EMIT {severity}: {headline}")

        # ── Signal: YoY filing rate increase ──────────────────────────────────
        for assignee in assignee_counts:
            cur_count = len(assignee_counts[assignee])

            prior_count_row = conn.execute("""
                SELECT COUNT(*) as cnt FROM patent_filings
                WHERE assignee = ?
                  AND category = ?
                  AND patent_date >= date('now', '-180 days')
                  AND patent_date < date('now', '-90 days')
            """, (assignee, category)).fetchone()

            prior_count = (prior_count_row["cnt"] or 0) if prior_count_row else 0
            if prior_count <= 0 or cur_count <= 0:
                continue

            pct = (cur_count - prior_count) / prior_count * 100
            if pct < 100:
                continue

            ticker   = assignee_counts[assignee][0]["ticker"] if assignee_counts[assignee] else ""
            severity = "HIGH" if cur_count >= 10 else "MEDIUM"
            headline = f"Patent Surge — {assignee} {category} filings up {pct:.0f}% vs prior quarter"
            detail   = f"{cur_count} patents filed in last 90 days vs {prior_count} in prior 90 days."
            tags     = json.dumps({"assignee": assignee, "category": category,
                                   "cur_count": cur_count, "prior_count": prior_count,
                                   "pct_increase": round(pct, 1), "source": "USPTO/PatentsView"})
            why = WHY_MATTERS.get("RULE_14",
                "Patent filing rate surges indicate companies are accelerating R&D in specific verticals.")

            existing = conn.execute("""
                SELECT id FROM alerts WHERE rule = 'RULE_14'
                  AND headline LIKE ? AND datetime(created_at) >= datetime('now', '-90 days')
            """, (f"%{assignee}% {category}%",)).fetchone()

            if existing:
                continue

            conn.execute("""
                INSERT INTO alerts (rule, ticker, headline, detail, severity, tags, why_matters)
                VALUES ('RULE_14', ?, ?, ?, ?, ?, ?)
            """, (ticker, headline, detail, severity, tags, why))
            alerts_emitted += 1
            print(f"[RULE_14] EMIT {severity}: {headline}")

    conn.commit()
    conn.close()
    print(f"[RULE_14] Done — {ingested} patents ingested, {alerts_emitted} alerts emitted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-alerts", action="store_true")
    args = parser.parse_args()
    run(emit=args.emit_alerts)
