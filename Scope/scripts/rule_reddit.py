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

from jpt_common import db_connection, insert_alert, record_activity

ARCTIC_BASE = "https://arctic-shift.photon-reddit.com/api/posts/search"

SUBREDDITS = [
    "wallstreetbets", "smallstreetbets", "pennystocks",
    "StockMarket", "investing", "SecurityAnalysis",
    "thetagang", "SPACs", "Superstonk", "RobinHoodPennyStocks",
    "options", "dividends",
    # removed: stocks, ValueInvesting, Economics — 422 on Arctic Shift
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
    # Arctic Shift's default query returns the NEWEST posts, whose score is ~1
    # (not yet upvoted) — so the MIN_UPVOTES gate rejected everything and nothing
    # was ever stored. Query a 1–5-day-old window instead, where posts have
    # accumulated real scores while still being fresh enough to act on.
    now = int(time.time())
    try:
        resp = requests.get(
            ARCTIC_BASE,
            params={
                "subreddit": subreddit,
                "limit": 100,
                "after":  now - 5 * 86400,
                "before": now - 1 * 86400,
            },
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("data", []) or []
    except Exception as e:
        reason = "422" if "422" in str(e) else "err"
        _fetch_failures.append(f"{subreddit}({reason})")
        if reason == "422":
            print(f"[RULE_REDDIT] Skipping r/{subreddit} — 422 (unavailable)")
        else:
            print(f"[RULE_REDDIT] Error fetching r/{subreddit}: {e}")
        return []


# Reddit rarely uses the $ cashtag, so we also read bare symbols — but only
# 4–5 char tokens (few collide with English words), plus a tiny curated set of
# famous 3-char meme tickers. Bare 3-char words like "TOP"/"RUN"/"CAR" are NOT
# treated as tickers (they'd be constant false positives).
BARE_TICKER_RE = re.compile(r"\b([A-Z]{4,5})\b")
_CURATED_3CHAR = {"GME", "AMC", "AMD", "SPY", "QQQ", "TSM", "UAL", "DIS", "PLT"}
# THE CASHTAG IS THE DISAMBIGUATOR.
#
# The universe check (`t in known`) was never the problem — it PASSES these, because
# they are genuine listed symbols that happen to spell English words:
#     BACK -> IMAC Holdings   HERE -> Here Group   POST -> Post Holdings
# So RULE_REDDIT stored "HERE"/"BEAT"/"MOVE" as tickers out of ordinary sentences.
#
# YOU CANNOT BLOCKLIST YOUR WAY OUT OF THIS, and two attempts proved it. A 63-word list
# became a ~125-word list, and a verifier still measured 414 real symbols slipping
# through bare. The false positives are GENUINE TICKERS, so no amount of enumeration
# converges — the missing information is not "is this a symbol" but "did the writer mean
# the symbol".
#
# The `$` answers exactly that, and it is the only thing that does:
#     $POST beat earnings   -> the writer means the stock. Accept, common word or not.
#     I saw this post       -> ordinary prose. Reject.
#     NVDA is up            -> unambiguous; nobody writes "nvda" as a word. Accept bare.
#
# So: cashtagged + real symbol -> accept. Bare + real symbol -> accept ONLY if the token
# is not a common English word. The hand-maintained blocklist is GONE; COMMON_WORDS is
# generated from measured word frequency (see scripts/_common_words.py for why frequency
# and not a dictionary, and how the cutoff was chosen).
#
# ⚠️ THE RECALL COST, STATED PLAINLY. A BARE mention of a common-word ticker is now
# missed BY DESIGN — "POST beat earnings" no longer yields POST; "$POST beat earnings"
# still does. That is the correct trade for a DISCOVERY feed: measured on the 93 real
# stored posts, the old extractor produced 53 tickers of which 21 were false — RYAN
# (a person's first name), TECH, HELP, MATH, FORM, PUMP, FEED, SHOP. A feed that cries
# wolf 40% of the time is worse than one that misses an ambiguous bare mention, and
# RULE_REDDIT is gate-excluded noise, so a miss costs no corroboration.
#
# What it does NOT cost: 1888 common words against a 10,619-symbol universe means only
# ~277 symbols (2.8%) require a cashtag. Every unambiguous ticker — NVDA, GME, AMD,
# PLTR, TSLA — is untouched, asserted in the tests.
from scripts._common_words import COMMON_WORDS

# THE BOUNDED RESIDUE. General-English frequency cannot know finance/Reddit jargon, so
# a hand-maintained set survives — but measured against the real universe it is TWO
# entries, not 125, and that is the whole difference.
#
# Auditing the old ~125-word blocklist against the 10,619-symbol universe:
#   * the English words in it (BACK, HERE, POST, MOVE, BEAT, ...) are now covered by
#     COMMON_WORDS, along with 109 more it had missed;
#   * CFO, CPI, DELTA, ELON, ETF, FDA, FOMO, GAMMA, GDP, IPO, MUSK, THETA, TLDR, USD,
#     YOLO are NOT LISTED SYMBOLS AT ALL — the `t in known` check already stopped every
#     one of them. They were dead weight that made the list look load-bearing;
#   * exactly two entries were both a real ticker and invisible to English frequency.
#
# This set is bounded in a way the old one never was: it is domain jargon, which is
# enumerable and slow-moving, not "English words that happen to be tickers", which is
# unbounded and had 414 measured gaps. Add here only after checking the token IS a real
# symbol — otherwise `known` already handles it and the entry is noise.
DOMAIN_JARGON = frozenset({
    "BETA",   # finance term; also a listed symbol. Was stored as a ticker in prod.
    "HODL",   # r/wallstreetbets vernacular; also a listed symbol.
})

_TICKER_WORDS = COMMON_WORDS | DOMAIN_JARGON

_COMMON_WORDS = _TICKER_WORDS       # legacy name, still referenced by tests
_TICKER_STOPWORDS = _TICKER_WORDS   # older name kept so nothing else breaks


def _extract_tickers(text: str, known: set[str]) -> list[str]:
    up = text.upper()
    found: list[str] = []
    # 1) High-confidence cashtags ($NVDA / $GME) — any length.
    for t in TICKER_RE.findall(up):
        if t in known and t not in found:
            found.append(t)
    # 2) Bare 4–5 char tracked symbols — accepted only if NOT ordinary prose.
    #    A cashtagged one already got in above, so a common word is only rejected
    #    here when the writer gave no `$` to disambiguate it.
    for t in BARE_TICKER_RE.findall(up):
        if t in known and t not in _TICKER_WORDS and t not in found:
            found.append(t)
    # 3) A small curated set of famous 3-char meme tickers, bare. Still curated
    #    rather than frequency-gated: 3-char tokens are dense with initialisms
    #    (CEO/ETF/FDA) that are not words, so frequency alone would not save them.
    for t in _CURATED_3CHAR:
        if t in known and t not in _TICKER_WORDS and re.search(rf"\b{t}\b", up) \
                and t not in found:
            found.append(t)
    return found


# Subreddits that failed to fetch this run (422/etc.) — surfaced in activity_log.
_fetch_failures: list[str] = []


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

    _t0 = time.time()
    _fetch_failures.clear()
    emitted = stored = scanned = flagged = 0

    for subreddit in SUBREDDITS:
        # Isolate each subreddit — a 422 (renamed/private/removed) or any other
        # error on one must not stop the whole sweep.
        try:
            posts = _fetch_subreddit(subreddit)
        except Exception as exc:
            print(f"[RULE_REDDIT] r/{subreddit}: fetch failed ({str(exc)[:80]}) — skipping")
            continue
        print(f"[RULE_REDDIT] r/{subreddit}: {len(posts)} posts fetched")
        scanned += len(posts)
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

            full_text = f"{title} {selftext}"
            tickers = _extract_tickers(full_text, known_tickers)
            if not tickers:
                continue  # no tracked ticker mentioned — not a tradeable post
            ticker = tickers[0]

            if dry_run:
                print(f"[RULE_REDDIT] [dry] {ticker} r/{subreddit} +{score} — {title[:60]}")
                continue

            # Archive every scored, ticker-bearing post (populates reddit_posts,
            # drives dedup). Alerts are the political subset, below.
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO reddit_posts
                       (post_id, subreddit, title, ticker, upvotes, url)
                       VALUES (?,?,?,?,?,?)""",
                    (post_id, subreddit, title, ticker, score, url),
                )
                ingested_posts.add(post_id)
                stored += 1
            except Exception as exc:
                print(f"[RULE_REDDIT] Failed to store post {post_id}: {exc}")
                continue

            # Emit an alert only when the post also carries a political angle.
            if url not in alerted_urls and _has_political(full_text):
                flagged += 1
                tags_str = json.dumps({"url": url, "subreddit": subreddit, "upvotes": score})
                sev      = "HIGH" if score >= 500 else "MEDIUM"
                headline = f"Reddit Signal — {ticker} mentioned in r/{subreddit} (+{score} upvotes)"
                detail   = f"{title}\n\n{selftext}".strip()[:400]
                print(f"[RULE_REDDIT] [emit] {ticker} r/{subreddit} +{score} — {title[:60]}")
                if emit:
                    insert_alert(conn, rule="RULE_REDDIT", ticker=ticker, severity=sev,
                                 headline=headline, tags=tags_str, detail=detail)
                    alerted_urls.add(url)
                    emitted += 1

            conn.commit()

        time.sleep(3)

    print(f"[RULE_REDDIT] Done — {stored} posts stored, {emitted} alerts emitted")
    conn.close()
    from jpt_common import record_activity
    notes = ("failed: " + ", ".join(_fetch_failures)) if _fetch_failures else None
    record_activity("RULE_REDDIT", scanned=scanned, flagged=flagged, emitted=emitted,
                    duration_seconds=round(time.time() - _t0, 2), notes=notes)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_REDDIT — Reddit signal tracker (Arctic Shift)")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
