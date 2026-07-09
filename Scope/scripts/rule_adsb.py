#!/usr/bin/env python3
"""
RULE_ADSB — Military Aviation Signal Detection
Polls OpenSky Network every 5 minutes for unusual military flight patterns.
Emits alerts when military aircraft concentration detected over conflict zones.

Run via cron:
  */5 * * * * cd /path/to/Scope && python scripts/rule_adsb.py --emit-alerts

OpenSky Network: free tier, 400 req/day without auth.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, REGION_TICKERS

OPENSKY_URL = "https://opensky-network.org/api/states/all"
HEADERS = {"User-Agent": "Scope Political Intelligence Monitor 1.0"}

# Military callsign prefix patterns (US + NATO)
MILITARY_PREFIXES = [
    "RCH",    # USAF AMC airlift
    "HAWK",   # US Army
    "EPIC",   # USAF special ops
    "JAKE",   # US Navy
    "SPAR",   # USAF VIP/exec
    "SAM",    # Special Air Mission (AF1 designator family)
    "VENUS",  # US Navy
    "PETE",   # USAF
    "EVAC",   # Aeromedical
    "REACH",  # USAF AMC
    "DUKE",   # NATO AWACS
    "NATO",   # NATO callsign
    "MAGIC",  # USAF E-3 AWACS
    "STEEL",  # Combat patrol
    "JOLLY",  # USAF rescue
    "KING",   # USAF tanker
]

# Conflict zone bounding boxes: (min_lat, max_lat, min_lng, max_lng)
CONFLICT_ZONES: dict[str, tuple[float, float, float, float]] = {
    "Middle East":    (20.0, 42.0,  30.0,  65.0),
    "Eastern Europe": (44.0, 58.0,  20.0,  45.0),
    "Taiwan Strait":  (20.0, 28.0, 118.0, 126.0),
    "Korean Peninsula": (34.0, 42.0, 124.0, 132.0),
    "South China Sea":  (0.0, 22.0, 105.0, 122.0),
}

CONCENTRATION_THRESHOLD = 3  # military flights in zone = alert


def _is_military(callsign: str) -> bool:
    cs = (callsign or "").strip().upper()
    return any(cs.startswith(p) for p in MILITARY_PREFIXES)


def _in_zone(lat: float, lng: float, zone: tuple) -> bool:
    return zone[0] <= lat <= zone[1] and zone[2] <= lng <= zone[3]


def fetch_military_flights() -> list[dict]:
    try:
        resp = requests.get(OPENSKY_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        states = resp.json().get("states", []) or []
    except Exception as e:
        print(f"[RULE_ADSB] OpenSky fetch failed: {e}")
        return []

    military = []
    for s in states:
        if not s or len(s) < 8:
            continue
        callsign = (s[1] or "").strip()
        if not _is_military(callsign):
            continue
        lat = s[6]
        lng = s[5]
        if lat is None or lng is None:
            continue
        military.append({
            "callsign":  callsign,
            "lat":       lat,
            "lng":       lng,
            "altitude":  s[7],
            "country":   s[2] or "",
            "on_ground": bool(s[8]) if len(s) > 8 else False,
        })
    return military


def check_zones(flights: list[dict]) -> dict[str, list[dict]]:
    concentrations: dict[str, list[dict]] = {}
    for zone_name, bbox in CONFLICT_ZONES.items():
        in_zone = [f for f in flights if _in_zone(f["lat"], f["lng"], bbox) and not f["on_ground"]]
        if len(in_zone) >= CONCENTRATION_THRESHOLD:
            concentrations[zone_name] = in_zone
    return concentrations


def run(emit: bool = False, dry_run: bool = False) -> None:
    conn = db_connection()
    flights = fetch_military_flights()
    print(f"[RULE_ADSB] {len(flights)} military flights detected")

    concentrations = check_zones(flights)
    if not concentrations:
        print("[RULE_ADSB] No unusual concentrations detected")
        conn.close()
        return

    now = datetime.now(timezone.utc)
    for zone, zone_flights in concentrations.items():
        tickers = REGION_TICKERS.get(zone, [])
        callsigns = ", ".join(f["callsign"] for f in zone_flights[:5])
        count = len(zone_flights)

        # Deduplicate by zone + hour
        window_key = now.strftime("%Y-%m-%dT%H")
        existing = conn.execute(
            "SELECT 1 FROM alerts WHERE rule='RULE_ADSB' AND tags LIKE ? "
            "AND datetime(created_at) >= datetime('now', '-1 hours')",
            (f"%{zone}%",),
        ).fetchone()
        if existing:
            print(f"[RULE_ADSB] zone {zone}: alert already emitted this hour — skipping")
            continue

        severity = "CRITICAL" if count >= 6 else "HIGH"
        ticker_str = " ".join(f"${t}" for t in tickers[:3]) if tickers else zone
        headline = f"Military aviation — {count} military flights detected over {zone} → {ticker_str}"
        detail = (
            f"{count} confirmed military aircraft operating over {zone}. "
            f"Callsigns: {callsigns}. "
            f"Military aviation concentration in conflict zones is a leading indicator of escalation."
        )
        tags_str = json.dumps({
            "zone": zone,
            "flight_count": count,
            "callsigns": [f["callsign"] for f in zone_flights[:10]],
            "source": "OpenSky Network",
            "tickers": tickers[:3],
        })

        print(f"[RULE_ADSB] {'[dry]' if dry_run else '[emit]'} {severity} — {zone}: {count} military flights ({callsigns})")

        if not dry_run and emit:
            for ticker in (tickers[:2] if tickers else ["NONE"]):
                conn.execute(
                    "INSERT INTO alerts (rule, ticker, severity, headline, detail, tags) "
                    "VALUES ('RULE_ADSB', ?, ?, ?, ?, ?)",
                    (ticker if ticker != "NONE" else None, severity, headline, detail, tags_str),
                )
            conn.commit()

    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_ADSB — Military aviation detection")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
