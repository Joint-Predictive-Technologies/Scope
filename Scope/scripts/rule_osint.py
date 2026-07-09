#!/usr/bin/env python3
"""
RULE_OSINT — Geopolitical Event Detection (Phase 1, daily cadence)
Sources:
  1. ReliefWeb API (free, no key) — humanitarian/conflict reports
  2. ACLED API (requires free registration) — conflict event data
Cross-checks: if affected tickers also have RULE_02/RULE_06 alerts → triggers RULE_10.

Set env vars: ACLED_API_KEY, ACLED_EMAIL (optional — ACLED source skipped if missing)
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

from jpt_common import db_connection, REGION_TICKERS

RELIEFWEB_URL = "https://api.reliefweb.int/v1/reports"
ACLED_URL     = "https://api.acleddata.com/acled/read"

MONITORED_COUNTRIES = [
    "Israel", "Palestine", "Iran", "Lebanon", "Syria", "Yemen",
    "Ukraine", "Russia", "Taiwan", "South Korea", "North Korea",
    "Philippines",
]

REGION_MAP: dict[str, str] = {
    "Israel": "Middle East", "Palestine": "Middle East", "Iran": "Middle East",
    "Lebanon": "Middle East", "Syria": "Middle East", "Yemen": "Middle East",
    "Ukraine": "Eastern Europe", "Russia": "Eastern Europe",
    "Taiwan": "Taiwan Strait",
    "South Korea": "Korean Peninsula", "North Korea": "Korean Peninsula",
    "Philippines": "South China Sea",
}

HEADERS = {"User-Agent": "Scope Political Intelligence Monitor 1.0"}


def _fetch_reliefweb(country: str) -> list[dict]:
    try:
        payload = {
            "appname": "scope",
            "filter": {"field": "country.name", "value": country},
            "fields": {"include": ["title", "body", "date", "url", "source"]},
            "sort": ["date:desc"],
            "limit": 5,
        }
        r = requests.post(RELIEFWEB_URL, json=payload, timeout=15)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"[RULE_OSINT] ReliefWeb error for {country}: {e}")
        return []


def _fetch_acled(yesterday: str) -> list[dict]:
    api_key = os.getenv("ACLED_API_KEY", "").strip()
    email   = os.getenv("ACLED_EMAIL", "").strip()
    if not api_key or not email:
        return []
    try:
        params = {
            "key":        api_key,
            "email":      email,
            "limit":      50,
            "event_date": yesterday,
            "country":    "|".join(MONITORED_COUNTRIES),
        }
        r = requests.get(ACLED_URL, params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"[RULE_OSINT] ACLED error: {e}")
        return []


def _already_alerted(conn, ticker: str, days: int = 14) -> bool:
    row = conn.execute(
        """SELECT 1 FROM alerts WHERE rule='RULE_OSINT' AND ticker=?
           AND datetime(created_at) >= datetime('now', ?)""",
        (ticker, f"-{days} days"),
    ).fetchone()
    return row is not None


def _check_corroboration(conn, tickers: list[str]) -> bool:
    for t in tickers:
        row = conn.execute(
            """SELECT 1 FROM alerts
               WHERE ticker=? AND rule IN ('RULE_02','RULE_06')
               AND datetime(created_at) >= datetime('now', '-30 days')""",
            (t,),
        ).fetchone()
        if row:
            return True
    return False


def run(emit: bool = False, dry_run: bool = False) -> None:
    conn = db_connection()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    emitted = 0

    # ── Source 1: ReliefWeb ──────────────────────────────────────────────────
    for country in MONITORED_COUNTRIES:
        reports = _fetch_reliefweb(country)
        time.sleep(0.5)

        for rpt in reports:
            fields  = rpt.get("fields", {})
            title   = fields.get("title", "")
            body    = (fields.get("body", "") or "")[:300]
            url     = fields.get("url", "")
            source  = (fields.get("source", [{}]) or [{}])[0].get("name", "ReliefWeb")
            region  = REGION_MAP.get(country, country)
            tickers = REGION_TICKERS.get(region, [])

            if not tickers or not title:
                continue

            # Deduplicate by URL in tags
            existing = conn.execute(
                "SELECT 1 FROM alerts WHERE rule='RULE_OSINT' AND tags LIKE ?",
                (f"%{url[:60]}%",),
            ).fetchone()
            if existing:
                continue

            ticker   = tickers[0]
            sev      = "HIGH"
            headline = f"Geopolitical — {country}: {title[:80]}"
            detail   = f"Source: {source}\n\n{body}"
            tags_obj = {"url": url, "country": country, "region": region,
                        "tickers": tickers, "source": source}
            tags_str = json.dumps(tags_obj)

            print(
                f"[RULE_OSINT] {'[dry]' if dry_run else '[emit]'} "
                f"{country}/{region} → {ticker}: {title[:60]}"
            )

            if not dry_run and emit:
                conn.execute(
                    """INSERT INTO alerts (rule, headline, severity, tags, ticker, detail)
                       VALUES ('RULE_OSINT', ?, ?, ?, ?, ?)""",
                    (headline, sev, tags_str, ticker, detail),
                )
                conn.commit()
                emitted += 1

                # Cross-check for corroboration
                if _check_corroboration(conn, tickers):
                    corr_headline = (
                        f"Corroboration — {ticker} has congressional/insider activity "
                        f"alongside geopolitical event in {region}"
                    )
                    corr_tags = json.dumps({
                        "rules": ["RULE_02", "RULE_OSINT"],
                        "rule_count": 2,
                        "rules_fired": "RULE_02,RULE_OSINT",
                    })
                    conn.execute(
                        """INSERT INTO alerts (rule, headline, severity, tags, ticker, detail)
                           VALUES ('RULE_10', ?, 'CRITICAL', ?, ?, ?)""",
                        (corr_headline, corr_tags, ticker,
                         f"Geopolitical event in {region} overlaps with existing congressional signal on {ticker}."),
                    )
                    conn.commit()

    # ── Source 2: ACLED ──────────────────────────────────────────────────────
    acled_events = _fetch_acled(yesterday)
    for event in acled_events:
        country    = event.get("country", "")
        event_type = event.get("event_type", "")
        location   = event.get("location", "")
        fatalities = int(event.get("fatalities", 0) or 0)
        notes      = (event.get("notes", "") or "")[:300]
        region     = REGION_MAP.get(country, "")
        tickers    = REGION_TICKERS.get(region, []) if region else []

        if not tickers:
            continue

        ticker   = tickers[0]
        sev      = "CRITICAL" if fatalities > 10 else "HIGH"
        headline = f"ACLED — {event_type} in {location}, {country} ({', '.join(tickers[:3])})"
        detail   = notes
        tags_obj = {"country": country, "region": region, "event_type": event_type,
                    "fatalities": fatalities, "tickers": tickers, "source": "ACLED"}
        tags_str = json.dumps(tags_obj)

        if _already_alerted(conn, ticker):
            continue

        print(
            f"[RULE_OSINT] ACLED {'[dry]' if dry_run else '[emit]'} "
            f"{event_type} {location} {country} fatalities={fatalities} → {ticker}"
        )

        if not dry_run and emit:
            conn.execute(
                """INSERT INTO alerts (rule, headline, severity, tags, ticker, detail)
                   VALUES ('RULE_OSINT', ?, ?, ?, ?, ?)""",
                (headline, sev, tags_str, ticker, detail),
            )
            conn.commit()
            emitted += 1

    print(f"[RULE_OSINT] Done — {emitted} alerts emitted")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_OSINT — Geopolitical event detection")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
