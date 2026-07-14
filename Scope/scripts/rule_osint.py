#!/usr/bin/env python3
"""
RULE_OSINT — Geopolitical Event Detection via GDELT 2.0 Event Stream

Downloads the raw 15-minute GDELT event CSV directly — no API key, no rate
limits, updates every 15 minutes. The previous GDELT DOC API approach
(/api/v2/doc/doc) caused 429 errors; this avoids it entirely.

Run every 15 minutes via APScheduler (configured in api/main.py) or cron:
  */15 * * * * cd /path/to/Scope && python scripts/rule_osint.py
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.parse
import zipfile

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import (
    db_connection,
    COUNTRY_REGION_MAP,
    HIGH_SIGNAL_CAMEO,
)

GDELT_MASTER_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
HEADERS = {"User-Agent": "Scope Political Intelligence Monitor 1.0"}


# ── GDELT Event Stream ────────────────────────────────────────────────────────

def _fetch_gdelt_events() -> list[dict]:
    """Download latest 15-min GDELT event CSV — no API, no rate limits."""
    master = requests.get(GDELT_MASTER_URL, timeout=30, headers=HEADERS)
    master.raise_for_status()

    # lastupdate.txt: line 1 = events export, line 2 = mentions, line 3 = GKG
    # Format per line: <size> <md5> <url>
    event_url = master.text.strip().splitlines()[0].strip().split()[-1]
    if not event_url.endswith(".export.CSV.zip"):
        print("[RULE_OSINT] GDELT: unexpected lastupdate.txt format")
        return []

    print(f"[RULE_OSINT] Downloading GDELT event file: {event_url}")
    r = requests.get(event_url, timeout=60, headers=HEADERS)
    r.raise_for_status()

    events: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        with z.open(z.namelist()[0]) as f:
            reader = csv.reader(
                io.TextIOWrapper(f, encoding="utf-8", errors="replace"),
                delimiter="\t",
            )
            for row in reader:
                if len(row) < 58:
                    continue
                try:
                    event_id     = row[0]
                    cameo        = row[26] if row[26] else ""
                    goldstein    = float(row[30]) if row[30] else 0.0
                    num_mentions = int(row[31])   if row[31] else 0
                    avg_tone     = float(row[34]) if row[34] else 0.0
                    # GDELT 2.0 has 61 columns; geo blocks are 8 cols each
                    # (Type, FullName, CountryCode, ADM1Code, ADM2Code, Lat, Long, FeatureID)
                    # ActionGeo block starts at col 51: Type=51, FullName=52,
                    # CountryCode=53, ADM1=54, ADM2=55, Lat=56, Long=57, FeatureID=58
                    # DATEADDED=59, SOURCEURL=60
                    country    = row[53] if len(row) > 53 and row[53] else (row[37] if len(row) > 37 and row[37] else "")
                    geo_lat    = float(row[56]) if len(row) > 56 and row[56] else None
                    geo_lng    = float(row[57]) if len(row) > 57 and row[57] else None
                    source_url = row[60] if len(row) > 60 and row[60] else ""
                except (ValueError, IndexError):
                    continue

                events.append({
                    "event_id":     event_id,
                    "cameo":        cameo,
                    "goldstein":    goldstein,
                    "num_mentions": num_mentions,
                    "avg_tone":     avg_tone,
                    "country":      country,
                    "geo_lat":      geo_lat,
                    "geo_lng":      geo_lng,
                    "source_url":   source_url,
                })

    print(f"[RULE_OSINT] Parsed {len(events)} raw GDELT events")
    return events


def _filter_hostile(events: list[dict]) -> list[dict]:
    """Keep hostile events in tracked countries that clear the Tier-3 floor
    (goldstein < -4 AND mentions >= 8) — loose enough for 3-8 alerts/day."""
    hostile: list[dict] = []
    for e in events:
        if e["goldstein"] >= -4:
            continue
        if e["num_mentions"] < 8:
            continue
        if e["country"] not in COUNTRY_REGION_MAP:
            continue
        event_type = next(
            (v for k, v in HIGH_SIGNAL_CAMEO.items() if e["cameo"].startswith(k)),
            "Hostile event",
        )
        hostile.append({**e, "event_type": event_type, "region": COUNTRY_REGION_MAP[e["country"]]})
    print(f"[RULE_OSINT] {len(hostile)} candidate events after filtering")
    return hostile


# ── Event-type-aware ticker mapping ──────────────────────────────────────────
# Map the CAMEO root code to a coarse event category, then map (region, category)
# to specific tickers. Events with no (region, category) entry are DROPPED rather
# than defaulting to a generic basket — so a diplomatic criticism doesn't move
# the same 5 defense/energy names as a missile strike.
def _event_category(cameo: str) -> str:
    c = (cameo or "").strip()
    if c.startswith(("18", "19", "20", "191", "193", "194", "195", "196")):
        return "MILITARY_ACTION"
    if c.startswith(("172", "173")):
        return "SANCTIONS"
    if c.startswith(("17", "175")):
        return "COERCE"
    if c.startswith("13"):
        return "THREAT"
    if c.startswith("14"):
        return "PROTEST"
    if c.startswith("112"):
        return "DIPLOMATIC"
    return "OTHER"


EVENT_TICKER_MAP: dict[tuple[str, str], list[str]] = {
    ("Middle East", "MILITARY_ACTION"): ["USO", "XLE", "XOM", "CVX"],
    ("Middle East", "SANCTIONS"):       ["XOM", "CVX", "USO"],
    ("Middle East", "COERCE"):          ["USO", "XLE"],
    ("Middle East", "THREAT"):          ["USO", "XLE"],

    ("Russia", "MILITARY_ACTION"):      ["LMT", "RTX", "NOC", "USO", "XLE"],
    ("Russia", "SANCTIONS"):            ["XOM", "USO", "XLE"],

    ("Eastern Europe", "MILITARY_ACTION"): ["LMT", "RTX", "NOC", "GD"],
    ("Eastern Europe", "SANCTIONS"):       ["XOM", "USO", "XLE"],
    ("Eastern Europe", "THREAT"):          ["LMT", "RTX"],

    ("Taiwan Strait", "MILITARY_ACTION"):  ["TSM", "NVDA", "AMD", "INTC", "AVGO"],
    ("Taiwan Strait", "COERCE"):           ["TSM", "NVDA", "AMD"],
    ("Taiwan Strait", "THREAT"):           ["TSM", "NVDA"],

    ("South China Sea", "MILITARY_ACTION"): ["TSM", "NVDA", "LMT", "RTX"],
    ("South China Sea", "COERCE"):          ["TSM", "NVDA"],

    ("Korean Peninsula", "MILITARY_ACTION"): ["LMT", "NOC", "TSM"],
    ("Korean Peninsula", "THREAT"):          ["LMT", "NOC"],

    ("South Asia", "MILITARY_ACTION"):  ["LMT", "RTX", "NOC"],

    ("West Africa", "MILITARY_ACTION"): ["XOM", "CVX", "COP"],
    ("East Africa", "MILITARY_ACTION"): ["XOM", "CVX", "USO"],
    ("North Africa", "MILITARY_ACTION"): ["USO", "XLE", "LMT"],
    ("North Africa", "SANCTIONS"):       ["USO", "XLE"],

    ("Southeast Asia", "MILITARY_ACTION"): ["TSM", "NVDA", "XOM"],
    ("Latin America", "MILITARY_ACTION"):  ["XOM", "CVX", "USO"],
    ("Latin America", "SANCTIONS"):        ["XOM", "USO"],
}


def get_tickers_for_event(region: str, cameo: str) -> list[str]:
    return EVENT_TICKER_MAP.get((region, _event_category(cameo)), [])


def _gdelt_severity(goldstein: float, num_mentions: int) -> str:
    # Tier 1 — CRITICAL (strict); Tier 2 — HIGH; Tier 3 — MEDIUM (catches more
    # genuine daily events so the feed isn't silent).
    if goldstein < -8 and num_mentions >= 25:
        return "CRITICAL"
    if goldstein < -6 and num_mentions >= 15:
        return "HIGH"
    return "MEDIUM"


def _run_gdelt(conn, emit: bool, dry_run: bool) -> tuple[int, int, int]:
    try:
        raw    = _fetch_gdelt_events()
        events = _filter_hostile(raw)
    except Exception as e:
        print(f"[RULE_OSINT] GDELT fetch failed: {e}")
        return 0, 0, 0

    scanned = len(raw)      # raw GDELT rows examined
    flagged = len(events)   # passed hostile/mention filter
    emitted = 0

    for event in events:
        existing = conn.execute(
            "SELECT 1 FROM gdelt_events WHERE event_id = ?", (event["event_id"],)
        ).fetchone()
        if existing:
            continue

        region   = event["region"]
        category = _event_category(event["cameo"])
        tickers  = get_tickers_for_event(region, event["cameo"])
        if not tickers:
            # No specific (region, event-type) mapping — mark seen, don't emit a
            # generic-basket alert.
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
        # Google News search URL — always English, always relevant
        news_q = urllib.parse.quote(f"{event['event_type']} {region}")
        news_url = f"https://news.google.com/search?q={news_q}&hl=en-US&gl=US&ceid=US:en"

        tags_obj: dict = {
            "source_url":     event["source_url"],
            "region":         region,
            "event_category": category,
            "goldstein":      event["goldstein"],
            "mentions":       event["num_mentions"],
            "cameo":          event["cameo"],
            "country":        event["country"],
            "tickers":        tickers[:3],
            "source":         "GDELT",
            "event_type":     event["event_type"],
        }
        if event.get("geo_lat") is not None:
            tags_obj["lat"] = event["geo_lat"]
            tags_obj["lng"] = event["geo_lng"]
        tags_str = json.dumps(tags_obj)

        print(
            f"[RULE_OSINT] {'[dry]' if dry_run else '[emit]'} {severity} — "
            f"{event['event_type']} ({region}) → {ticker_str}"
        )

        if not dry_run and emit:
            # One alert per GDELT event — all tickers live in tags_obj["tickers"].
            # Via the scoring wrapper so it carries novelty/opportunity/evidence.
            from jpt_common import insert_alert
            insert_alert(conn, rule="RULE_OSINT", ticker=tickers[0], severity=severity,
                         headline=headline, detail=detail, tags=tags_str, source_url=news_url)
            conn.execute(
                "INSERT OR IGNORE INTO gdelt_events (event_id) VALUES (?)",
                (event["event_id"],),
            )
            conn.commit()
            emitted += 1

    print(f"[RULE_OSINT] GDELT: {emitted} new alerts emitted")
    return scanned, flagged, emitted


# ── Entry point ───────────────────────────────────────────────────────────────

def run(emit: bool = False, dry_run: bool = False) -> None:
    import time
    from jpt_common import record_activity
    _t0 = time.time()
    conn = db_connection()
    scanned, flagged, emitted = _run_gdelt(conn, emit=emit, dry_run=dry_run)
    conn.close()
    record_activity("RULE_OSINT", scanned=scanned, flagged=flagged, emitted=emitted,
                    duration_seconds=round(time.time() - _t0, 2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="RULE_OSINT — Geopolitical event detection via GDELT Event Stream"
    )
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
