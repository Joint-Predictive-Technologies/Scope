#!/usr/bin/env python3
"""
Options Flow Correlation — enriches existing alerts with unusual options activity.
Does NOT emit standalone alerts. Only boosts intelligence score of existing HIGH+ alerts.

Source: Unusual Whales public feed (via RSSHub)

Run every 15 minutes via cron:
  */15 * * * * cd /path/to/Scope && python scripts/rule_options_correlation.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection

# Public Unusual Whales feed (no auth required for public posts)
UW_FEEDS = [
    "https://rsshub.app/twitter/user/unusual_whales",
    "https://rss.app/feeds/_RqFXwX9N6d6XqBkS.xml",  # alternative public feed
]


def _parse_flows(feed_url: str) -> list[dict]:
    flows = []
    try:
        feed = feedparser.parse(feed_url)
        for entry in (feed.entries or [])[:30]:
            text = (entry.get("title", "") + " " + entry.get("summary", "")).strip()
            tickers = list(set(re.findall(r'\$([A-Z]{1,5})', text)))
            if not tickers:
                continue

            bullish = any(w in text.lower() for w in
                          ["calls", "bullish", "sweep", "unusual call", "call flow"])
            bearish = any(w in text.lower() for w in
                          ["puts", "bearish", "unusual put", "put flow"])

            if not (bullish or bearish):
                continue

            # Extract expiry and strike if present
            expiry_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
            strike_match = re.search(r'\$(\d{2,4}(?:\.\d{2})?)\s*(?:C|P|call|put)', text, re.I)

            flows.append({
                "tickers":   tickers,
                "direction": "bullish" if bullish else "bearish",
                "text":      text[:300],
                "url":       entry.get("link", ""),
                "published": entry.get("published", ""),
                "expiry":    expiry_match.group(1) if expiry_match else "",
                "strike":    strike_match.group(1) if strike_match else "",
            })
    except Exception as e:
        print(f"[OPTIONS] Feed parse error {feed_url}: {e}")
    return flows


def run() -> None:
    import time as _time
    _t0 = _time.time()
    conn = db_connection()
    enriched = 0

    # Try feeds in order
    flows: list[dict] = []
    for feed_url in UW_FEEDS:
        flows = _parse_flows(feed_url)
        if flows:
            break

    if not flows:
        print("[OPTIONS] No flows fetched from any feed")
        conn.close()
        return

    print(f"[OPTIONS] {len(flows)} flow signals fetched")

    for flow in flows:
        for ticker in flow["tickers"]:
            # Check if Scope has a HIGH+ alert on this ticker in last 48h
            existing = conn.execute("""
                SELECT id, tags, confidence_score, why_matters
                FROM alerts
                WHERE ticker = ?
                  AND severity IN ('HIGH', 'CRITICAL')
                  AND datetime(created_at) >= datetime('now', '-48 hours')
                ORDER BY created_at DESC
                LIMIT 1
            """, (ticker,)).fetchone()

            if not existing:
                continue

            alert_id = existing["id"]
            tags_raw = existing["tags"] or "{}"

            # Don't enrich the same alert twice
            try:
                tags_obj = json.loads(tags_raw)
            except Exception:
                tags_obj = {}

            if tags_obj.get("options_corroborated"):
                continue

            # Boost: mark options corroboration in tags
            tags_obj["options_corroborated"] = True
            tags_obj["options_direction"]    = flow["direction"]
            tags_obj["options_url"]          = flow["url"]
            if flow["expiry"]:
                tags_obj["options_expiry"] = flow["expiry"]
            if flow["strike"]:
                tags_obj["options_strike"] = flow["strike"]

            # Boost confidence score by 20%
            old_conf = existing["confidence_score"] or 50
            new_conf = min(100, int(old_conf * 1.20))

            # Update why_matters to mention options activity
            why = existing["why_matters"] or ""
            options_note = (
                f" Unusual {flow['direction']} options activity detected within 48h "
                f"of this signal — sophisticated traders may be positioning based on related information."
            )
            if "options" not in why.lower():
                why = why.rstrip() + options_note

            conn.execute("""
                UPDATE alerts
                SET tags = ?, confidence_score = ?, why_matters = ?
                WHERE id = ?
            """, (json.dumps(tags_obj), new_conf, why, alert_id))

            # Check for RULE_10 on same ticker — upgrade to CRITICAL if exists
            rule10 = conn.execute("""
                SELECT id, severity FROM alerts
                WHERE ticker = ? AND rule = 'RULE_10'
                  AND datetime(created_at) >= datetime('now', '-48 hours')
            """, (ticker,)).fetchone()

            if rule10 and rule10["severity"] != "CRITICAL":
                conn.execute("""
                    UPDATE alerts SET severity = 'CRITICAL',
                        why_matters = COALESCE(why_matters, '') ||
                            ' Unusual options activity corroborates the cross-source signal.'
                    WHERE id = ?
                """, (rule10["id"],))
                print(f"[OPTIONS] Upgraded RULE_10 on {ticker} to CRITICAL (options corroboration)")

            enriched += 1
            print(f"[OPTIONS] Enriched alert {alert_id} for {ticker} ({flow['direction']} flow)")

    conn.commit()
    conn.close()
    print(f"[OPTIONS] Done — {enriched} alerts enriched")
    from jpt_common import record_activity
    record_activity("RULE_OPTIONS", emitted=enriched,
                    duration_seconds=round(_time.time() - _t0, 2))


if __name__ == "__main__":
    run()
