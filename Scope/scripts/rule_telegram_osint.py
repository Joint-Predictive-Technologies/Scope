#!/usr/bin/env python3
"""
RULE_TELEGRAM_OSINT — Monitor public Telegram OSINT channels via RSS.
Uses RSSHub bridges (no auth needed for public channels).
Often 30–60 minutes ahead of mainstream news.

Run via cron:
  */15 * * * * cd /path/to/Scope && python scripts/rule_telegram_osint.py --emit-alerts

Requires: feedparser  (pip install feedparser)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, REGION_TICKERS, COUNTRY_REGION_MAP

try:
    import feedparser
except ImportError:
    print("[RULE_TELEGRAM_OSINT] feedparser not installed — pip install feedparser")
    sys.exit(1)

import requests

# Public Telegram OSINT channels via RSSHub
TELEGRAM_FEEDS = [
    ("https://rsshub.app/telegram/channel/intelslava",     "Intel Slava"),
    ("https://rsshub.app/telegram/channel/wartranslated",  "War Translated"),
    ("https://rsshub.app/telegram/channel/militarylandnet","Military Land"),
    ("https://rsshub.app/telegram/channel/rybar",          "Rybar"),
    ("https://rsshub.app/telegram/channel/OSINTua",        "OSINT UA"),
    ("https://rsshub.app/telegram/channel/GeoConfirmed",   "GeoConfirmed"),
]

# Region/country keyword patterns to detect region from post text
REGION_KEYWORDS: dict[str, list[str]] = {
    "Middle East":       ["israel", "hamas", "hezbollah", "iran", "beirut", "gaza", "yemen", "iraq", "syria"],
    "Eastern Europe":    ["ukraine", "kyiv", "kharkiv", "donetsk", "kherson", "zaporizhzhia", "russia", "moscow"],
    "Russia":            ["russia", "kremlin", "moscow", "putin", "wagner", "chechen"],
    "Taiwan Strait":     ["taiwan", "pla", "strait", "taipei", "china"],
    "Korean Peninsula":  ["north korea", "south korea", "dprk", "pyongyang", "seoul"],
    "South China Sea":   ["south china sea", "philippines", "spratlys", "paracel"],
    "South Asia":        ["pakistan", "afghanistan", "india", "kashmir"],
}


def _detect_region(text: str) -> str | None:
    low = text.lower()
    for region, keywords in REGION_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return region
    return None


def _severity_from_text(text: str) -> str:
    low = text.lower()
    if any(w in low for w in ["missile", "explosion", "attack", "shelling", "strike", "combat", "killed", "airstrike"]):
        return "HIGH"
    if any(w in low for w in ["troops", "military", "convoy", "drone", "artillery"]):
        return "MEDIUM"
    return "MEDIUM"


def fetch_channel(url: str, channel_name: str) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:15]:
            title = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or entry.get("description") or "").strip()
            link = entry.get("link", "")
            published = entry.get("published", "")
            results.append({
                "title":   title or summary[:80],
                "summary": summary[:500],
                "link":    link,
                "published": published,
                "channel": channel_name,
            })
        return results
    except Exception as e:
        print(f"[RULE_TELEGRAM_OSINT] {channel_name} feed error: {e}")
        return []


def run(emit: bool = False, dry_run: bool = False) -> None:
    conn = db_connection()
    total_emitted = 0

    for feed_url, channel_name in TELEGRAM_FEEDS:
        posts = fetch_channel(feed_url, channel_name)
        print(f"[RULE_TELEGRAM_OSINT] {channel_name}: {len(posts)} posts")

        for post in posts:
            text = post["title"] + " " + post["summary"]
            region = _detect_region(text)
            if not region:
                continue

            tickers = REGION_TICKERS.get(region, [])
            if not tickers:
                continue

            # Dedup: check if this link has been ingested before
            link = post["link"]
            if link:
                existing = conn.execute(
                    "SELECT 1 FROM alerts WHERE rule='RULE_TELEGRAM_OSINT' AND tags LIKE ?",
                    (f"%{link[:80]}%",),
                ).fetchone()
                if existing:
                    continue

            severity = _severity_from_text(text)
            ticker_str = " ".join(f"${t}" for t in tickers[:3])
            headline = f"Telegram OSINT — {channel_name}: {post['title'][:90]}"
            detail = f"Channel: {channel_name}\n\n{post['summary'][:400]}"
            tags_str = json.dumps({
                "source_url": link,
                "source": f"Telegram/{channel_name}",
                "region": region,
                "tickers": tickers[:3],
                "channel": channel_name,
            })

            print(f"[RULE_TELEGRAM_OSINT] {'[dry]' if dry_run else '[emit]'} {severity} — {region}: {post['title'][:60]}")

            if not dry_run and emit:
                conn.execute(
                    "INSERT INTO alerts (rule, ticker, severity, headline, detail, tags) "
                    "VALUES ('RULE_TELEGRAM_OSINT', ?, ?, ?, ?, ?)",
                    (tickers[0], severity, headline, detail, tags_str),
                )
                conn.commit()
                total_emitted += 1

    conn.close()
    print(f"[RULE_TELEGRAM_OSINT] Done — {total_emitted} new alerts emitted")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_TELEGRAM_OSINT — Telegram OSINT channel monitor")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
