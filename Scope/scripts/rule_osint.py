#!/usr/bin/env python3
"""
RULE_OSINT — Geopolitical Event Detection
Sources:
  1. GDELT Project (primary) — free, no key, updates every 15 minutes
  2. ReliefWeb API (secondary) — free, no key, daily cadence

Run every 15 minutes via cron:
  */15 * * * * cd /path/to/Scope && python scripts/rule_osint.py
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import (
    db_connection,
    COUNTRY_REGION_MAP,
    HIGH_SIGNAL_CAMEO,
    REGION_TICKERS,
)

GDELT_MASTER_URL = "http://data.gdelt.org/gdeltv2/lastupdate.txt"
RELIEFWEB_URL    = "https://api.reliefweb.int/v1/reports"

MONITORED_COUNTRIES_RW = [
    "Israel", "Palestine", "Iran", "Lebanon", "Syria", "Yemen",
    "Ukraine", "Russia", "Taiwan", "South Korea", "North Korea",
    "Philippines", "Pakistan", "Afghanistan",
]

HEADERS = {"User-Agent": "Scope Political Intelligence Monitor 1.0"}


# ── GDELT ─────────────────────────────────────────────────────────────────────

def _fetch_gdelt_events() -> list[dict]:
    resp = requests.get(GDELT_MASTER_URL, timeout=15)
    resp.raise_for_status()

    # lastupdate.txt has 3 lines; first line is the export zip (full events)
    latest_url = None
    for line in resp.text.strip().splitlines():
        parts = line.split(" ")
        if len(parts) >= 3 and parts[2].endswith(".export.CSV.zip"):
            latest_url = parts[2]
            break

    if not latest_url:
        print("[RULE_OSINT] GDELT: could not find export file URL")
        return []

    zip_resp = requests.get(latest_url, timeout=60)
    zip_resp.raise_for_status()

    signals: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
        filename = z.namelist()[0]
        with z.open(filename) as f:
            reader = csv.reader(
                io.TextIOWrapper(f, encoding="utf-8", errors="replace"),
                delimiter="\t",
            )
            for row in reader:
                if len(row) < 58:
                    continue
                try:
                    event_id     = row[0]
                    event_code   = row[26][:3] if row[26] else ""
                    goldstein    = float(row[30]) if row[30] else 0.0
                    num_mentions = int(row[31])   if row[31] else 0
                    avg_tone     = float(row[34]) if row[34] else 0.0
                    country      = row[53]
                    source_url   = row[57]
                except (ValueError, IndexError):
                    continue

                if goldstein > -3:
                    continue
                if num_mentions < 5:
                    continue
                if event_code not in HIGH_SIGNAL_CAMEO:
                    continue
                if country not in COUNTRY_REGION_MAP:
                    continue

                signals.append({
                    "event_id":     event_id,
                    "event_code":   event_code,
                    "event_type":   HIGH_SIGNAL_CAMEO[event_code],
                    "goldstein":    goldstein,
                    "num_mentions": num_mentions,
                    "avg_tone":     avg_tone,
                    "country":      country,
                    "region":       COUNTRY_REGION_MAP[country],
                    "source_url":   source_url,
                })

    return signals


def _gdelt_severity(goldstein: float, num_mentions: int) -> str:
    if goldstein <= -7 and num_mentions >= 20:
        return "CRITICAL"
    if goldstein <= -5 or num_mentions >= 15:
        return "HIGH"
    return "MEDIUM"


def _run_gdelt(conn, emit: bool, dry_run: bool) -> int:
    try:
        events = _fetch_gdelt_events()
    except Exception as e:
        print(f"[RULE_OSINT] GDELT fetch failed: {e}")
        return 0

    print(f"[RULE_OSINT] GDELT: {len(events)} candidate events after filtering")
    emitted = 0

    for event in events:
        existing = conn.execute(
            "SELECT 1 FROM gdelt_events WHERE event_id = ?", (event["event_id"],)
        ).fetchone()
        if existing:
            continue

        region  = event["region"]
        tickers = REGION_TICKERS.get(region, [])
        if not tickers:
            conn.execute(
                "INSERT OR IGNORE INTO gdelt_events (event_id) VALUES (?)",
                (event["event_id"],),
            )
            conn.commit()
            continue

        severity   = _gdelt_severity(event["goldstein"], event["num_mentions"])
        ticker_str = " ".join(f"${t}" for t in tickers[:3])
        headline   = f"Geopolitical — {event['event_type']} ({region}) → {ticker_str}"
        detail     = (
            f"GDELT event {event['event_id']}: {event['event_type']} in {event['country']}. "
            f"Goldstein scale: {event['goldstein']} (hostile). "
            f"Mentioned in {event['num_mentions']} sources. "
            f"Tone: {event['avg_tone']:.1f}."
        )
        tags_obj = {
            "source_url": event["source_url"],
            "region":     region,
            "goldstein":  event["goldstein"],
            "cameo":      event["event_code"],
            "tickers":    tickers[:3],
            "source":     "GDELT",
        }
        tags_str = json.dumps(tags_obj)

        print(
            f"[RULE_OSINT] {'[dry]' if dry_run else '[emit]'} {severity} — "
            f"{event['event_type']} ({region}) → {ticker_str}"
        )

        if not dry_run and emit:
            for ticker in tickers[:3]:
                conn.execute(
                    """INSERT INTO alerts (rule, ticker, severity, headline, detail, tags)
                       VALUES ('RULE_OSINT', ?, ?, ?, ?, ?)""",
                    (ticker, severity, headline, detail, tags_str),
                )

            conn.execute(
                "INSERT OR IGNORE INTO gdelt_events (event_id) VALUES (?)",
                (event["event_id"],),
            )

            # Auto-trigger RULE_10 if corroborating signal exists within 48h
            for ticker in tickers[:3]:
                corr = conn.execute(
                    """SELECT COUNT(DISTINCT rule) FROM alerts
                       WHERE ticker = ?
                         AND rule NOT IN ('RULE_OSINT', 'RULE_10')
                         AND datetime(created_at) >= datetime('now', '-48 hours')""",
                    (ticker,),
                ).fetchone()[0]
                if corr >= 1:
                    corr_tags = json.dumps({
                        "rules": ["RULE_OSINT", "RULE_10"],
                        "rule_count": 2,
                        "rules_fired": "RULE_OSINT",
                        "source": "GDELT",
                    })
                    conn.execute(
                        """INSERT INTO alerts (rule, ticker, severity, headline, detail, tags)
                           VALUES ('RULE_10', ?, 'CRITICAL', ?, ?, ?)""",
                        (
                            ticker,
                            f"[Corroboration] {ticker}: OSINT + existing signals converged within 48h",
                            f"Geopolitical event ({event['event_type']} in {region}) "
                            f"corroborates existing Scope signals on {ticker}.",
                            corr_tags,
                        ),
                    )

            conn.commit()
            emitted += 1

    print(f"[RULE_OSINT] GDELT: {emitted} new alerts emitted")
    return emitted


# ── ReliefWeb ─────────────────────────────────────────────────────────────────

def _fetch_reliefweb(country: str) -> list[dict]:
    try:
        payload = {
            "appname": "scope",
            "filter":  {"field": "country.name", "value": country},
            "fields":  {"include": ["title", "body", "date", "url", "source"]},
            "sort":    ["date:desc"],
            "limit":   5,
        }
        r = requests.post(RELIEFWEB_URL, json=payload, timeout=15)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"[RULE_OSINT] ReliefWeb error for {country}: {e}")
        return []


COUNTRY_TO_REGION = {
    "Israel": "Middle East", "Palestine": "Middle East", "Iran": "Middle East",
    "Lebanon": "Middle East", "Syria": "Middle East", "Yemen": "Middle East",
    "Ukraine": "Eastern Europe", "Russia": "Russia",
    "Taiwan": "Taiwan Strait",
    "South Korea": "Korean Peninsula", "North Korea": "Korean Peninsula",
    "Philippines": "South China Sea",
    "Pakistan": "South Asia", "Afghanistan": "South Asia",
}


def _run_reliefweb(conn, emit: bool, dry_run: bool) -> int:
    emitted = 0

    for country in MONITORED_COUNTRIES_RW:
        reports = _fetch_reliefweb(country)
        time.sleep(0.5)

        for rpt in reports:
            fields  = rpt.get("fields", {})
            title   = fields.get("title", "")
            body    = (fields.get("body", "") or "")[:300]
            url     = fields.get("url", "")
            source  = (fields.get("source") or [{}])[0].get("name", "ReliefWeb")
            region  = COUNTRY_TO_REGION.get(country, country)
            tickers = REGION_TICKERS.get(region, [])

            if not tickers or not title:
                continue

            existing = conn.execute(
                "SELECT 1 FROM alerts WHERE rule='RULE_OSINT' AND tags LIKE ?",
                (f"%{url[:60]}%",),
            ).fetchone()
            if existing:
                continue

            ticker   = tickers[0]
            headline = f"Geopolitical — {country}: {title[:80]}"
            detail   = f"Source: {source}\n\n{body}"
            tags_str = json.dumps({
                "url": url, "country": country, "region": region,
                "tickers": tickers, "source": source,
            })

            print(
                f"[RULE_OSINT] ReliefWeb {'[dry]' if dry_run else '[emit]'} "
                f"{country} → {ticker}: {title[:60]}"
            )

            if not dry_run and emit:
                conn.execute(
                    """INSERT INTO alerts (rule, headline, severity, tags, ticker, detail)
                       VALUES ('RULE_OSINT', ?, 'HIGH', ?, ?, ?)""",
                    (headline, tags_str, ticker, detail),
                )
                conn.commit()
                emitted += 1

    print(f"[RULE_OSINT] ReliefWeb: {emitted} new alerts emitted")
    return emitted


# ── Entry point ───────────────────────────────────────────────────────────────

def run(emit: bool = False, dry_run: bool = False) -> None:
    conn = db_connection()
    _run_gdelt(conn, emit=emit, dry_run=dry_run)
    _run_reliefweb(conn, emit=emit, dry_run=dry_run)
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_OSINT — Geopolitical event detection (GDELT + ReliefWeb)")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
