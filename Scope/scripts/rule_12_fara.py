#!/usr/bin/env python3
"""
RULE_12 — Foreign Lobbying (FARA)
Source: DOJ FARA database — fara.justice.gov
Free, no key required. Bulk CSV updated daily.

Run hourly via cron:
  0 * * * * cd /path/to/Scope && python scripts/rule_12_fara.py
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, WHY_MATTERS

FARA_LD_URL   = "https://efile.fara.gov/bulk/ALL_LD.csv"
FARA_SUPP_URL = "https://efile.fara.gov/bulk/ALL_SUPP.csv"
HEADERS = {
    "User-Agent": "Scope Political Intelligence research@jointpredictive.com"
}

PRINCIPAL_SECTOR_MAP: dict[str, list[str]] = {
    "Saudi Arabia":  ["USO", "XLE", "XOM", "CVX"],
    "Israel":        ["LMT", "RTX", "NOC"],
    "UAE":           ["USO", "XLE"],
    "China":         ["TSM", "NVDA", "AMD"],
    "Japan":         ["TSM"],
    "South Korea":   ["TSM"],
    "Turkey":        ["LMT", "RTX"],
    "Qatar":         ["XLE"],
    "Russia":        ["USO", "XLE"],
    "India":         ["PLTR"],
    "Taiwan":        ["TSM", "NVDA", "AMD"],
    "Germany":       ["LMT", "RTX"],
    "United Kingdom": ["LMT", "RTX"],
    "France":        ["LMT"],
    "South Africa":  ["XLE"],
    "Brazil":        ["XLE"],
}

# Cache: last-modified timestamp of bulk file
_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".fara_cache.json")


def _load_cache() -> dict:
    try:
        with open(_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _fetch_csv(url: str, cache_key: str) -> list[dict] | None:
    """Download CSV if newer than last fetch; return parsed rows or None if unchanged."""
    cache = _load_cache()
    last_modified = cache.get(cache_key, "")

    headers = dict(HEADERS)
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code == 304:
        print(f"[RULE_12] {cache_key}: not modified, skipping")
        return None
    resp.raise_for_status()

    new_lm = resp.headers.get("Last-Modified", "")
    if new_lm:
        cache[cache_key] = new_lm
        _save_cache(cache)

    reader = csv.DictReader(io.StringIO(resp.text))
    return list(reader)


def _normalize_country(name: str) -> str:
    """Normalize a foreign principal name to a country for sector mapping."""
    name_lower = name.lower()
    for country in PRINCIPAL_SECTOR_MAP:
        if country.lower() in name_lower:
            return country
    return ""


def _parse_amount(val: str) -> float:
    try:
        cleaned = val.replace("$", "").replace(",", "").strip()
        return float(cleaned) if cleaned else 0.0
    except Exception:
        return 0.0


def run(emit: bool = False) -> None:
    conn = db_connection()

    rows = _fetch_csv(FARA_LD_URL, "ld")
    if rows is None:
        conn.close()
        return

    ingested = 0
    alerts_emitted = 0

    for row in rows:
        # FARA CSV uses various column names — detect flexibly
        reg_number  = (row.get("Registration_Number") or row.get("RegistrationNumber") or "").strip()
        registrant  = (row.get("Registrant_Name") or row.get("RegistrantName") or "").strip()
        principal   = (row.get("Foreign_Principal_Name") or row.get("ForeignPrincipalName") or row.get("ForeignPrincipal") or "").strip()
        country     = (row.get("Foreign_Principal_Country") or row.get("ForeignPrincipalCountry") or row.get("ForeignPrincipalNationality") or "").strip()
        period_start = (row.get("Period_Of_Performance_Start") or row.get("PeriodFrom") or row.get("DateStamp") or "").strip()
        period_end   = (row.get("Period_Of_Performance_End") or row.get("PeriodTo") or "").strip()
        receipts_str = (row.get("Total_Receipts") or row.get("TotalReceipts") or row.get("Receipts") or "0").strip()
        issues       = (row.get("Issues") or row.get("LobbyingIssues") or "").strip()

        if not reg_number or not principal:
            continue

        receipts = _parse_amount(receipts_str)
        if receipts <= 0:
            continue

        # Normalize country from principal name if not explicit
        if not country:
            country = _normalize_country(principal)
        if not country:
            continue

        # Upsert into fara_filings
        try:
            conn.execute("""
                INSERT OR IGNORE INTO fara_filings
                    (reg_number, registrant, foreign_principal, country,
                     period_start, period_end, total_receipts, issues_lobbied)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (reg_number, registrant, principal, country,
                  period_start, period_end, receipts, issues[:500] if issues else ""))
            ingested += 1
        except Exception:
            pass

    conn.commit()
    print(f"[RULE_12] Ingested {ingested} FARA filings")

    if not emit:
        conn.close()
        return

    # ── Signal logic: detect YoY spend increases > 30% ──────────────────────
    principals = conn.execute(
        "SELECT DISTINCT foreign_principal, country FROM fara_filings"
    ).fetchall()

    now = datetime.now(timezone.utc)
    current_year = str(now.year)
    prior_year   = str(now.year - 1)

    for row in principals:
        principal = row["foreign_principal"]
        country   = row["country"]

        # Aggregate current-year receipts
        cur_row = conn.execute("""
            SELECT SUM(total_receipts) as total, GROUP_CONCAT(issues_lobbied, '; ') as issues,
                   MAX(registrant) as reg, MAX(reg_number) as reg_num
            FROM fara_filings
            WHERE foreign_principal = ?
              AND period_start LIKE ?
        """, (principal, f"{current_year}%")).fetchone()

        # Aggregate prior-year receipts
        prior_row = conn.execute("""
            SELECT SUM(total_receipts) as total
            FROM fara_filings
            WHERE foreign_principal = ?
              AND period_start LIKE ?
        """, (principal, f"{prior_year}%")).fetchone()

        current_amount = (cur_row["total"] or 0.0) if cur_row else 0.0
        prior_amount   = (prior_row["total"] or 0.0) if prior_row else 0.0

        if current_amount <= 0 or prior_amount <= 0:
            continue

        pct = (current_amount - prior_amount) / prior_amount * 100
        if pct < 30:
            continue

        registrant = (cur_row["reg"] or "Unknown") if cur_row else "Unknown"
        reg_number = (cur_row["reg_num"] or "") if cur_row else ""
        issues     = (cur_row["issues"] or "") if cur_row else ""

        # Map country to relevant tickers
        tickers = PRINCIPAL_SECTOR_MAP.get(country, [])
        ticker_str = tickers[0] if tickers else ""

        severity = "CRITICAL" if pct > 100 else "HIGH" if pct > 50 else "MEDIUM"
        headline = f"Foreign Lobbying — {principal} increased US lobbying +{pct:.0f}% YoY"
        detail   = (
            f"{registrant} registered as foreign agent for {principal}. "
            f"Spend: ${current_amount:,.0f} vs ${prior_amount:,.0f} prior year. "
            f"Issues lobbied: {issues[:300] if issues else 'Not specified'}"
        )
        source_url = (
            f"https://efile.fara.gov/pls/apex/f?p=171:200:0::NO:RP,200:P200_REG_NUMBER:{reg_number}"
            if reg_number else "https://efile.fara.gov"
        )
        tags = json.dumps({
            "foreign_principal": principal,
            "country": country,
            "registrant": registrant,
            "reg_number": reg_number,
            "spend": current_amount,
            "prior_spend": prior_amount,
            "pct_increase": round(pct, 1),
            "issues": issues[:200] if issues else "",
            "source": "FARA",
            "source_url": source_url,
        })
        why = WHY_MATTERS.get("RULE_12",
            "Foreign lobbying spend spikes indicate anticipated policy shifts that directly benefit the lobbying country's sector interests.")

        # Deduplicate: one alert per principal per year
        existing = conn.execute("""
            SELECT id FROM alerts
            WHERE rule = 'RULE_12'
              AND headline LIKE ?
              AND datetime(created_at) >= datetime('now', '-365 days')
        """, (f"%{principal}%",)).fetchone()

        if existing:
            print(f"[RULE_12] Already alerted for {principal} this year, skipping")
            continue

        conn.execute("""
            INSERT INTO alerts (rule, ticker, headline, detail, severity, tags, source_url, why_matters)
            VALUES ('RULE_12', ?, ?, ?, ?, ?, ?, ?)
        """, (ticker_str, headline, detail, severity, tags, source_url, why))
        alerts_emitted += 1
        print(f"[RULE_12] EMIT {severity}: {headline}")

    conn.commit()
    conn.close()
    print(f"[RULE_12] Done — {alerts_emitted} alerts emitted")


def _ensure_tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS fara_filings (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        reg_number       TEXT,
        registrant       TEXT,
        foreign_principal TEXT,
        country          TEXT,
        period_start     TEXT,
        period_end       TEXT,
        total_receipts   REAL,
        issues_lobbied   TEXT,
        ingested_at      TEXT DEFAULT (datetime('now')),
        UNIQUE(reg_number, period_start)
    )""")
    conn.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-alerts", action="store_true")
    args = parser.parse_args()

    import sqlite3
    conn = db_connection()
    _ensure_tables(conn)
    conn.close()

    run(emit=args.emit_alerts)
