#!/usr/bin/env python3
"""
RULE_REDDIT — Reddit Political Signal Tracker
Monitors key subreddits for posts that mention tracked tickers alongside
political keywords. Uses Arctic Shift (free archive API, no auth required).
https://arctic-shift.photon-reddit.com
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

ARCTIC_BASE = "https://arctic-shift.photon-reddit.com/api/posts/search"

SUBREDDITS = [
    "wallstreetbets", "investing", "stocks",
    "StockMarket", "worldnews",
    "economics", "politics", "geopolitics",
]

POLITICAL_KEYWORDS = [
    "congress", "senate", "pelosi", "sec ", "fda", "dod", "federal reserve",
    "regulation", "contract", "lobbying", "insider", "pentagon", "itar",
    "tariff", "subsidy", "stimulus", "legislation", "sanctions", "executive order",
]

TICKER_RE   = re.compile(r'\$([A-Z]{1,5})\b')
MIN_UPVOTES = 50

HEADERS = {"User-Agent": "Scope Political Intelligence v1.0"}


def _fetch_subreddit(subreddit: str) -> list[dict]:
    try:
        resp = requests.get(
            ARCTIC_BASE,
            params={"subreddit": subreddit, "limit": 25},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", []) or []
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

    ingested_posts: set[str] = {
        r[0]
        for r in conn.execute("SELECT post_id FROM reddit_posts").fetchall()
    }

    alerted_urls: set[str] = {
        json.loads(r[0]).get("url", "")
        for r in conn.execute("SELECT tags FROM alerts WHERE rule='RULE_REDDIT'").fetchall()
        if r[0]
    }

    emitted = stored = 0

    for subreddit in SUBREDDITS:
        posts = _fetch_subreddit(subreddit)
        print(f"[RULE_REDDIT] r/{subreddit}: {len(posts)} posts fetched")
        time.sleep(3)

        for post in posts:
            post_id  = post.get("id", "")
            title    = post.get("title", "")
            selftext = (post.get("selftext", "") or "")[:500]
            score    = post.get("score", 0) or 0
            url      = post.get("url", "")

            if not post_id or post_id in ingested_posts:
                continue
            if score < MIN_UPVOTES:
                continue
            if url in alerted_urls:
                continue

            full_text = f"{title} {selftext}"
            if not _has_political(full_text):
                continue

            tickers = _extract_tickers(full_text, known_tickers)
            if not tickers:
                continue

            ticker   = tickers[0]
            tags_str = json.dumps({"url": url, "subreddit": subreddit, "upvotes": score})
            sev      = "HIGH" if score >= 500 else "MEDIUM"
            headline = f"Reddit Signal — {ticker} mentioned in r/{subreddit} (+{score} upvotes)"
            detail   = f"{title}\n\n{selftext}".strip()[:400]

            print(
                f"[RULE_REDDIT] {'[dry]' if dry_run else '[emit]'} "
                f"{ticker} r/{subreddit} +{score} — {title[:60]}"
            )

            if dry_run:
                continue

            conn.execute(
                """INSERT OR IGNORE INTO reddit_posts
                   (post_id, subreddit, title, ticker, upvotes, url)
                   VALUES (?,?,?,?,?,?)""",
                (post_id, subreddit, title, ticker, score, url),
            )
            ingested_posts.add(post_id)
            stored += 1

            if emit:
                conn.execute(
                    """INSERT INTO alerts (rule, headline, severity, tags, ticker, detail)
                       VALUES ('RULE_REDDIT', ?, ?, ?, ?, ?)""",
                    (headline, sev, tags_str, ticker, detail),
                )
                alerted_urls.add(url)
                emitted += 1

            conn.commit()

        time.sleep(3)

    print(f"[RULE_REDDIT] Done — {stored} posts stored, {emitted} alerts emitted")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_REDDIT — Reddit signal tracker (Arctic Shift)")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
