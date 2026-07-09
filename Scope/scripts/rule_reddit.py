#!/usr/bin/env python3
"""
RULE_REDDIT — Reddit Political Signal Tracker
Monitors key subreddits for posts that mention tracked tickers alongside
political keywords. Requires >50 upvotes and a political keyword match.
No API key required — uses Reddit's free JSON endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection

SUBREDDITS = [
    "wallstreetbets", "investing", "stocks", "options",
    "politicaleconomy", "Economics", "StockMarket",
    "SecurityAnalysis",
]

POLITICAL_KEYWORDS = [
    "congress", "senate", "pelosi", "sec ", "fda", "dod", "federal reserve",
    "regulation", "contract", "lobbying", "insider", "pentagon", "itar",
    "tariff", "subsidy", "stimulus", "legislation", "sanctions", "executive order",
]

TICKER_RE = re.compile(r'\$([A-Z]{1,5})\b')
MIN_UPVOTES = 50

HEADERS = {"User-Agent": "Scope Political Intelligence Monitor 1.0"}


def _fetch_subreddit(subreddit: str) -> list[dict]:
    try:
        url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=25"
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        posts = r.json().get("data", {}).get("children", [])
        return [p["data"] for p in posts]
    except Exception as e:
        print(f"[RULE_REDDIT] Error fetching r/{subreddit}: {e}")
        return []


def _extract_tickers(text: str, known: set[str]) -> list[str]:
    found = TICKER_RE.findall(text.upper())
    return [t for t in found if t in known]


def _has_political(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in POLITICAL_KEYWORDS)


def run(emit: bool = False, dry_run: bool = False) -> None:
    conn = db_connection()

    known_tickers: set[str] = {
        r[0].upper()
        for r in conn.execute("SELECT symbol FROM tickers WHERE symbol IS NOT NULL").fetchall()
    }

    already_alerted: set[str] = {
        r[0]
        for r in conn.execute(
            "SELECT tags FROM alerts WHERE rule='RULE_REDDIT'"
        ).fetchall()
        if r[0]
    }

    ingested_posts: set[str] = {
        r[0]
        for r in conn.execute("SELECT post_id FROM reddit_posts").fetchall()
    }

    emitted = stored = 0

    for subreddit in SUBREDDITS:
        posts = _fetch_subreddit(subreddit)
        time.sleep(1)

        for post in posts:
            post_id  = post.get("id", "")
            title    = post.get("title", "")
            selftext = post.get("selftext", "")[:500]
            upvotes  = post.get("ups", 0) or post.get("score", 0)
            url      = "https://www.reddit.com" + post.get("permalink", "")

            if post_id in ingested_posts:
                continue
            if upvotes < MIN_UPVOTES:
                continue

            full_text = f"{title} {selftext}"
            if not _has_political(full_text):
                continue

            tickers = _extract_tickers(full_text, known_tickers)
            if not tickers:
                continue

            ticker = tickers[0]
            tags_obj = {"url": url, "subreddit": subreddit, "upvotes": upvotes}
            tags_str = json.dumps(tags_obj)

            if url in already_alerted:
                continue

            sev = "HIGH" if upvotes >= 500 else "MEDIUM"
            headline = f"Reddit Signal — {ticker} mentioned in r/{subreddit} (+{upvotes} upvotes)"
            detail = f"{title}\n\n{selftext}".strip()[:400]

            print(
                f"[RULE_REDDIT] {'[dry]' if dry_run else '[emit]'} "
                f"{ticker} r/{subreddit} +{upvotes} — {title[:60]}"
            )

            if not dry_run:
                conn.execute(
                    """INSERT OR IGNORE INTO reddit_posts
                       (post_id, subreddit, title, ticker, upvotes, url)
                       VALUES (?,?,?,?,?,?)""",
                    (post_id, subreddit, title, ticker, upvotes, url),
                )
                ingested_posts.add(post_id)
                stored += 1

            if (emit or dry_run is False) and not dry_run:
                conn.execute(
                    """INSERT INTO alerts (rule, headline, severity, tags, ticker, detail)
                       VALUES ('RULE_REDDIT', ?, ?, ?, ?, ?)""",
                    (headline, sev, tags_str, ticker, detail),
                )
                conn.commit()
                already_alerted.add(url)
                emitted += 1

        time.sleep(2)

    print(f"[RULE_REDDIT] Done — {stored} posts stored, {emitted} alerts emitted")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_REDDIT — Reddit signal tracker")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
