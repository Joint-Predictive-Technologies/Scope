#!/usr/bin/env python3
"""
RULE_15 — Earnings Call Political Sentiment
Source: SEC EDGAR full-text search (free, no key required)

Tracks political keyword density in earnings call 8-K filings.
Fires when political keyword mentions surge >50% QoQ.

Run daily via cron:
  0 8 * * * cd /path/to/Scope && python scripts/rule_15_earnings_nlp.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, SECTOR_MAP, WHY_MATTERS

EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"
EDGAR_BASE   = "https://www.sec.gov"
HEADERS = {
    "User-Agent": "Scope Political Intelligence research@jointpredictive.com",
    "Accept-Encoding": "gzip, deflate",
}

POLITICAL_KEYWORDS = [
    "regulatory environment", "washington", "congress", "legislation",
    "federal contract", "government spending", "defense budget",
    "export control", "tariff", "sanction", "antitrust",
    "fda approval", "medicare", "medicaid", "pentagon",
    "department of defense", "geopolitical", "trade policy",
    "national security", "government contract", "appropriations",
    "executive order", "regulatory approval", "compliance", "lobbying",
]

TRACKED_TICKERS = [
    "LMT", "RTX", "NOC", "GD", "BA",
    "NVDA", "AAPL", "MSFT", "AMD", "INTC",
    "PFE", "MRK", "ABBV", "LLY", "AMGN",
    "XOM", "CVX", "COP",
    "PLTR", "SAIC", "BAH",
]


def _fetch_8k_filings(ticker: str, days_back: int = 120) -> list[dict]:
    """Fetch recent 8-K filings for a ticker from EDGAR."""
    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end   = datetime.now().strftime("%Y-%m-%d")
    params = {
        "q":         ticker,
        "dateRange": "custom",
        "startdt":   start,
        "enddt":     end,
        "forms":     "8-K",
    }
    try:
        resp = requests.get(EDGAR_SEARCH, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        return hits
    except Exception as e:
        print(f"[RULE_15] EDGAR search error for {ticker}: {e}")
        return []


def _fetch_filing_text(url: str) -> str:
    """Fetch the full text of a filing."""
    try:
        full_url = url if url.startswith("http") else EDGAR_BASE + url
        resp = requests.get(full_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[RULE_15] Text fetch error: {e}")
        return ""


def _political_score(text: str) -> tuple[float, dict]:
    """Compute political keyword density (per 1000 words) and per-keyword counts."""
    lower = text.lower()
    word_count = max(len(lower.split()), 1)
    counts = {kw: lower.count(kw) for kw in POLITICAL_KEYWORDS if kw in lower}
    total  = sum(counts.values())
    score  = (total / word_count) * 1000
    return score, counts


def _ensure_tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS earnings_sentiment (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker        TEXT NOT NULL,
        filing_date   TEXT NOT NULL,
        accession     TEXT UNIQUE,
        political_score REAL,
        keyword_counts  TEXT,
        ingested_at   TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()


def run(emit: bool = False) -> None:
    conn = db_connection()
    _ensure_tables(conn)

    ingested  = 0
    alerts_emitted = 0

    for ticker in TRACKED_TICKERS:
        hits = _fetch_8k_filings(ticker, days_back=120)
        time.sleep(0.5)

        for hit in hits[:4]:
            src = hit.get("_source", {})
            accession  = src.get("file_num") or src.get("accession_no") or hit.get("_id", "")
            filing_date = src.get("period_of_report") or src.get("file_date", "")
            text_url   = src.get("file_date")  # EDGAR full text isn't directly in search results

            # Build accession-based text URL
            acc_clean = accession.replace("-", "")
            filing_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=8-K&dateb=&owner=include&count=40"

            # Skip if already ingested
            existing = conn.execute(
                "SELECT id FROM earnings_sentiment WHERE accession = ?", (accession,)
            ).fetchone()
            if existing:
                continue

            # Fetch text (only first filing per ticker to avoid rate limits)
            text = ""
            file_href = src.get("file_url") or ""
            if file_href:
                time.sleep(0.3)
                text = _fetch_filing_text(file_href)

            if not text:
                # Store placeholder with 0 score so we don't re-fetch
                conn.execute("""
                    INSERT OR IGNORE INTO earnings_sentiment
                        (ticker, filing_date, accession, political_score, keyword_counts)
                    VALUES (?, ?, ?, ?, ?)
                """, (ticker, filing_date, accession, 0.0, "{}"))
                continue

            score, counts = _political_score(text)
            conn.execute("""
                INSERT OR IGNORE INTO earnings_sentiment
                    (ticker, filing_date, accession, political_score, keyword_counts)
                VALUES (?, ?, ?, ?, ?)
            """, (ticker, filing_date, accession, score, json.dumps(counts)))
            ingested += 1

        conn.commit()

        if not emit:
            continue

        # ── Signal: QoQ keyword density surge ─────────────────────────────────
        history = conn.execute("""
            SELECT political_score, keyword_counts, filing_date
            FROM earnings_sentiment
            WHERE ticker = ? AND political_score > 0
            ORDER BY filing_date DESC
            LIMIT 4
        """, (ticker,)).fetchall()

        if len(history) < 2:
            continue

        current_score = history[0]["political_score"]
        prior_score   = history[1]["political_score"]

        if prior_score <= 0:
            continue

        trend = (current_score - prior_score) / prior_score * 100
        if trend < 50:
            continue

        current_counts = json.loads(history[0]["keyword_counts"] or "{}")
        prior_counts   = json.loads(history[1]["keyword_counts"] or "{}")
        new_keywords   = [kw for kw in current_counts if kw not in prior_counts]

        severity = "HIGH" if trend > 100 else "MEDIUM"
        headline = f"Earnings Signal — {ticker} political keyword mentions up {trend:.0f}% QoQ"
        detail   = (
            f"{ticker} management mentioned regulatory/political terms {current_score:.1f} per 1000 words "
            f"in latest earnings call vs {prior_score:.1f} in prior quarter. "
            f"Top keywords: {', '.join(list(current_counts.keys())[:5])}. "
            + (f"New keywords: {', '.join(new_keywords[:5])}." if new_keywords else "")
        )
        tags = json.dumps({
            "ticker": ticker,
            "current_score": round(current_score, 2),
            "prior_score":   round(prior_score, 2),
            "trend_pct":     round(trend, 1),
            "new_keywords":  new_keywords[:10],
            "source":        "SEC EDGAR 8-K",
            "source_url":    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=8-K",
        })
        why = WHY_MATTERS.get("RULE_15",
            "Surging political keyword density in earnings calls often precedes regulatory action, "
            "government contract activity, or policy-driven sector moves.")

        existing = conn.execute("""
            SELECT id FROM alerts WHERE rule = 'RULE_15' AND ticker = ?
              AND datetime(created_at) >= datetime('now', '-90 days')
        """, (ticker,)).fetchone()

        if existing:
            continue

        conn.execute("""
            INSERT INTO alerts (rule, ticker, headline, detail, severity, tags, why_matters)
            VALUES ('RULE_15', ?, ?, ?, ?, ?, ?)
        """, (ticker, headline, detail, severity, tags, why))
        alerts_emitted += 1
        print(f"[RULE_15] EMIT {severity}: {headline}")

        # Cross-reference with RULE_08 and RULE_09 — if all three on same ticker in 30d → corroborate
        cross_count = conn.execute("""
            SELECT COUNT(DISTINCT rule) FROM alerts
            WHERE ticker = ? AND rule IN ('RULE_08', 'RULE_09', 'RULE_15')
              AND datetime(created_at) >= datetime('now', '-30 days')
        """, (ticker,)).fetchone()[0]

        if cross_count >= 3:
            conn.execute("""
                UPDATE alerts SET lifecycle_stage = 'corroborated'
                WHERE ticker = ? AND rule IN ('RULE_08', 'RULE_09', 'RULE_15')
                  AND (lifecycle_stage IS NULL OR lifecycle_stage = 'created')
            """, (ticker,))
            print(f"[RULE_15] Auto-corroborated {ticker} (RULE_08+09+15 all fired within 30d)")

    conn.commit()
    conn.close()
    print(f"[RULE_15] Done — {ingested} filings ingested, {alerts_emitted} alerts emitted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-alerts", action="store_true")
    args = parser.parse_args()
    run(emit=args.emit_alerts)
