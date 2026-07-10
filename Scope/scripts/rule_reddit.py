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

# ── Authenticity scoring / subreddit weighting (spec §9) ─────────────────────
SUBREDDIT_WEIGHTS = {
    "wallstreetbets": 3.0, "smallstreetbets": 2.5, "pennystocks": 2.0,
    "SecurityAnalysis": 2.0, "SPACs": 2.0, "Superstonk": 2.0,
    "stocks": 1.5, "investing": 1.5, "thetagang": 1.5,
    "RobinHoodPennyStocks": 1.5, "ValueInvesting": 1.5,
}

_AUTH_POS_FILING = ["10-k", "10-q", "sec filing", "earnings", "revenue", "contract", "patent"]
_AUTH_POS_ANALYSIS = ["price target", "my analysis", "dd:", "due diligence"]
_AUTH_NEG_HYPE = ["to the moon", "10x", "don't miss", "life changing", "next gme",
                  "hidden gem", "massive potential", "huge catalyst",
                  "about to explode", "going parabolic"]


def authenticity_score(post: dict) -> float:
    """0–10. Below 4 = likely slop/pump; 7+ = organic signal."""
    score = 5.0
    text = ((post.get("title", "") or "") + " " + (post.get("selftext", "") or "")).lower()
    if any(w in text for w in _AUTH_POS_FILING):
        score += 2
    if any(w in text for w in _AUTH_POS_ANALYSIS):
        score += 1.5
    if (post.get("num_comments") or 0) > 50:
        score += 1
    if (post.get("upvote_ratio") or 1.0) > 0.85:
        score += 0.5
    if any(w in text for w in _AUTH_NEG_HYPE):
        score -= 2
    if (post.get("author_post_count", 10) or 10) < 3:
        score -= 2  # new account
    return max(0.0, min(10.0, score))


def signal_type(authenticity: float, upvote_velocity: float, score: float):
    """ORGANIC_MOMENTUM / POSSIBLE_PUMP / DEVELOPING / None."""
    if authenticity >= 7 and score > 100:
        return "ORGANIC_MOMENTUM"
    if authenticity < 4 and upvote_velocity > 50:
        return "POSSIBLE_PUMP"      # still actionable — flag honestly
    if authenticity >= 6:
        return "DEVELOPING"
    return None


def reddit_severity(sig_type, subreddit_weight: float, score: float) -> str:
    if sig_type == "POSSIBLE_PUMP" and subreddit_weight >= 2.5:
        return "HIGH"
    if sig_type == "ORGANIC_MOMENTUM" and subreddit_weight >= 2.0:
        return "HIGH"
    if sig_type == "ORGANIC_MOMENTUM":
        return "MEDIUM"
    return "LOW"


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
