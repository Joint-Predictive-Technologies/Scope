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


EARNINGS_ITEM = "2.02"          # 8-K Item 2.02 = Results of Operations

# Rows ingested before this instant were produced by the pre-repair code: wrong-CIK
# attribution, no Item-2.02 filter, and a raw-.split() denominator. They are kept
# (forward-only, nothing rewritten) but may never take part in a comparison.
REPAIR_EPOCH = "2026-07-27 00:00:00"
_TAG = re.compile(r"<[^>]+>")
_B64 = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")


# Corporate successors: a reorganisation gives the company a NEW CIK, but filings made
# before it stay under the predecessor. SEC's company_tickers.json lists only the current
# one, so a strict single-CIK match silently zeroes the ticker's coverage.
# XOM is the live case: `tickers` maps it to 0002115436 (ExxonMobil Holdings Corp, which
# matches SEC's current file), while its only Item 2.02 filing in the window is under
# 0000034088 (Exxon Mobil Corp). Both are genuinely Exxon.
# Keep this list SHORT and evidenced — each entry is a claim that two CIKs are the same
# issuer, and a wrong entry would re-introduce exactly the mis-attribution this fixes.
CIK_ALIASES: dict[str, set[str]] = {
    "XOM": {"0000034088"},      # Exxon Mobil Corp, predecessor of ExxonMobil Holdings
}


def issuer_ciks(conn, ticker: str) -> set[str]:
    """EVERY CIK that legitimately identifies this ticker's issuer.

    A set, not a single value: see CIK_ALIASES. Empty means unresolvable, and the
    caller SKIPS the ticker rather than guessing.
    """
    out = {c for c in (issuer_cik(conn, ticker),) if c}
    out |= {c.strip().lstrip("0").zfill(10) for c in CIK_ALIASES.get(ticker, set())}
    return out


def issuer_cik(conn, ticker: str) -> str:
    """The ticker's own SEC CIK, zero-padded to 10. '' if unknown.

    This is the whole attribution fix. `q=<ticker>` is a BARE FULL-TEXT SEARCH, so
    `q=BA` returns BA Credit Card Trust and Affiliated Managers; `q=GD` returns GD
    Culture Group. Every hit was stored under the queried ticker without ever checking
    the `ciks` field the response already carries — which is how Boeing's political
    score came from Bank of America credit-card securitisation filings (0/4 correct).
    """
    row = conn.execute("SELECT cik FROM tickers WHERE symbol = ? AND cik IS NOT NULL "
                       "AND cik != '' LIMIT 1", (ticker,)).fetchone()
    if not row or not row["cik"]:
        return ""
    return str(row["cik"]).strip().lstrip("0").zfill(10)


def hit_matches_issuer(hit: dict, cik) -> bool:
    """Keep a hit ONLY if the filing's CIK list contains one of the issuer's CIKs.

    `cik` may be a single string or a set (successor + predecessor).
    """
    wanted = {cik} if isinstance(cik, str) else set(cik or ())
    wanted = {w for w in wanted if w}
    if not wanted:
        return False                       # unknown issuer -> drop, never guess
    ciks = (hit.get("_source", {}) or {}).get("ciks") or []
    return any(str(c).strip().lstrip("0").zfill(10) in wanted for c in ciks)


def is_earnings_8k(hit: dict) -> bool:
    """Item 2.02 only. Only ~40% of 8-Ks are Results of Operations; the rest are
    officer changes (5.02), shareholder votes (5.07) and Reg-FD exhibits (7.01), and
    scoring those as 'earnings call sentiment' compares incomparable documents."""
    items = (hit.get("_source", {}) or {}).get("items") or []
    return any(str(i).strip() == EARNINGS_ITEM for i in items)


def _visible_words(text: str) -> list[str]:
    """Words a human would read: markup and base64 exhibit blobs removed.

    The denominator was `len(raw_text.split())` over the whole .txt submission —
    including XML tags and base64-encoded exhibits — so 'per 1000 words' was really
    'per 1000 whitespace-separated byte runs', and the score moved with attachment
    size rather than with language."""
    t = _B64.sub(" ", _TAG.sub(" ", text or ""))
    return [w for w in t.split() if any(c.isalpha() for c in w)]


def _political_score(text: str) -> tuple[float, dict]:
    """Compute political keyword density (per 1000 words) and per-keyword counts."""
    lower = text.lower()
    word_count = max(len(_visible_words(text)), 1)
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
    _t0 = time.time()
    conn = db_connection()
    _ensure_tables(conn)

    ingested  = 0
    alerts_emitted = 0
    unresolved_cik: list[str] = []
    fetch_failures: list[str] = []
    wrong_cik = non_earnings = baselined = 0

    for ticker in TRACKED_TICKERS:
        cik = issuer_ciks(conn, ticker)
        if not cik:
            unresolved_cik.append(ticker)
            print(f"[RULE_15] {ticker}: no issuer CIK in `tickers` — SKIPPED, not guessed")
            continue
        hits = _fetch_8k_filings(ticker, days_back=120)
        time.sleep(0.5)

        kept = [h for h in hits if hit_matches_issuer(h, cik)]
        wrong_cik += len(hits) - len(kept)
        earnings_hits = [h for h in kept if is_earnings_8k(h)]
        non_earnings += len(kept) - len(earnings_hits)

        for hit in earnings_hits[:4]:
            src = hit.get("_source", {})
            # EDGAR EFTS uses "adsh" for accession number (not "file_num")
            accession   = src.get("adsh") or hit.get("_id", "")
            # file_date FIRST: `period_ending` is the reporting period and is often
            # absent or equal for two different filings, which is what made the "QoQ"
            # comparison line up two filings dated the SAME DAY (AMGN, PFE).
            filing_date = src.get("file_date") or src.get("period_ending", "")
            ciks        = src.get("ciks") or []
            cik         = (ciks[0] or "").lstrip("0") if ciks else ""

            filing_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=8-K&dateb=&owner=include&count=40"

            # Skip if already ingested
            existing = conn.execute(
                "SELECT id FROM earnings_sentiment WHERE accession = ?", (accession,)
            ).fetchone()
            if existing:
                continue

            # Fetch text — construct submission text URL from CIK + accession
            text = ""
            if cik and accession:
                acc_clean = accession.replace("-", "")
                file_href = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{accession}.txt"
                time.sleep(0.3)
                text = _fetch_filing_text(file_href)

            if not text:
                # DO NOT write a 0.0 placeholder. `accession` is UNIQUE and the insert
                # is INSERT OR IGNORE, so a placeholder written on a TRANSIENT fetch
                # failure is permanent — that filing is never scored again. Skip
                # instead, so the next run retries it.
                fetch_failures.append(f"{ticker}:{accession}")
                print(f"[RULE_15] text fetch failed {ticker} {accession} — will RETRY "
                      f"next run (no placeholder written)")
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

        # COLD START: the first time we ever score a ticker there is no prior period to
        # compare against, and the 120-day backlog would otherwise arrive all at once and
        # emit as if it were news. Store it, emit nothing, and let the NEXT filing be the
        # signal. Without this the first live run would flood ~73 stored filings across
        # 4 months into the feed as fresh earnings alerts.
        # QUARANTINE THE PRE-REPAIR ROWS. `scored_rows <= 1` was the wrong test: the 73
        # rows already in earnings_sentiment carry 2-4 rows per ticker, so it suppressed
        # exactly ONE ticker and the first live run would have emitted 4 alerts computed
        # entirely from pre-repair data — led by "BA +109% HIGH", which is the fabricated
        # Boeing signal built from BA Credit Card Trust filings that this repair exists to
        # eliminate. PFE +92%, ABBV +68%, XOM +637% behind it.
        #
        # Those rows are unusable for three independent reasons: wrong-CIK attribution,
        # no Item 2.02 restriction, and a raw-.split() denominator 2.4-3.1x larger than
        # the visible-word one now used — so comparing a new row against a stored one
        # manufactures a +143% to +212% "surge" from nothing but the units change.
        #
        # So emission requires BOTH sides of the comparison to be post-repair. Nothing is
        # rewritten or deleted (forward-only); the old rows simply cannot participate.
        usable = conn.execute(
            "SELECT COUNT(*) c FROM earnings_sentiment "
            "WHERE ticker=? AND political_score>0 AND ingested_at >= ?",
            (ticker, REPAIR_EPOCH)).fetchone()["c"]
        if usable < 2:
            baselined += 1
            continue

        # ── Signal: QoQ keyword density surge ─────────────────────────────────
        # The `ingested_at` predicate is NOT redundant with the `usable` gate above. The
        # gate only counts post-repair rows; this query SELECTS the denominator, and
        # without the same filter it reached past the clean rows into the quarantined set
        # — so a ticker could pass the gate on two genuine rows and still be compared
        # against a pre-repair one. That is exactly how "BA +908%" was emitted 2026-07-28,
        # a real Boeing 8-K over a BA Credit Card Trust filing (CIK 936988, asset-backed
        # securities, items 8.01/9.01 — not an earnings release). Both queries must read
        # the epoch on the same basis or the comment above ("BOTH sides post-repair") is
        # not actually enforced.
        history = conn.execute("""
            SELECT political_score, keyword_counts, filing_date
            FROM earnings_sentiment
            WHERE ticker = ? AND political_score > 0 AND ingested_at >= ?
            ORDER BY filing_date DESC
            LIMIT 8
        """, (ticker, REPAIR_EPOCH)).fetchall()

        if len(history) < 2:
            continue

        # LIKE-FOR-LIKE: refuse to compare two filings from the same day (or within a
        # few days). "QoQ" used to mean "the two most recent rows" regardless of spacing,
        # so AMGN and PFE were comparing two filings dated the SAME DAY and calling the
        # difference a quarterly trend.
        MIN_GAP_DAYS = 45
        cur_row, prior_row = history[0], None
        for cand in history[1:]:
            try:
                d0 = datetime.strptime(cur_row["filing_date"][:10], "%Y-%m-%d")
                d1 = datetime.strptime(cand["filing_date"][:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            if (d0 - d1).days >= MIN_GAP_DAYS:
                prior_row = cand
                break
        if prior_row is None:
            continue                        # no comparable prior period yet

        current_score = cur_row["political_score"]
        prior_score   = prior_row["political_score"]

        if prior_score <= 0:
            continue

        trend = (current_score - prior_score) / prior_score * 100
        if trend < 50:
            continue

        current_counts = json.loads(cur_row["keyword_counts"] or "{}")
        prior_counts   = json.loads(prior_row["keyword_counts"] or "{}")
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

        # ── REMOVED 2026-07-27: the shadow corroboration path ────────────────
        #
        # This used to run a SECOND corroboration gate here: if RULE_08 + RULE_09 +
        # RULE_15 all appeared on a ticker within 30 days, it UPDATEd *other rules'*
        # alerts to lifecycle_stage='corroborated'.
        #
        # It was wrong three ways and had to go before RULE_15 was ever repaired:
        #   1. It counted rule NAMES, not instruments — precisely the D1 defect the
        #      gate redesign removed. RULE_10 counts distinct INSTRUMENTS because
        #      several rules can read one source; this path could not tell the
        #      difference.
        #   2. It bypassed RULE_10 entirely, so a corroboration could exist that the
        #      gate never sanctioned and rule10_is_valid() would not endorse.
        #   3. Its trigger was bounded to 30 days but its UPDATE had NO time bound at
        #      all, so it would retroactively mark every RULE_08/09/15 alert on that
        #      ticker corroborated, for all history. RULE_10's equivalent is 48h.
        #
        # It was dormant only because RULE_15 never reached its emit path. Repairing
        # RULE_15 without removing this would have RE-ARMED D1 — which is why it is
        # removed first, while the rule is still silent.
        #
        # RULE_10 (scripts/rule_10_corroboration.py:314) is now the SINGLE authority
        # that may set lifecycle_stage='corroborated'. RULE_15 corroborates nothing.
        # Orphan check: the only readers are build_data_moat.py (exports the column)
        # and morning_brief.py (reads 'superseded' only) — nothing depended on RULE_15
        # having set it.

    conn.commit()
    conn.close()
    print(f"[RULE_15] Done — {ingested} filings ingested, {alerts_emitted} alerts emitted")
    from jpt_common import record_activity
    notes = (f"ingested={ingested} emitted={alerts_emitted} baselined={baselined} "
             f"wrong_cik_dropped={wrong_cik} non_2.02_dropped={non_earnings}")
    if unresolved_cik:
        notes += f" | NO_CIK(skipped): {','.join(unresolved_cik[:8])}"
    if fetch_failures:
        notes = ("CRITICAL: " + f"{len(fetch_failures)} text fetches failed (will retry): "
                 + ",".join(fetch_failures[:4]) + " | " + notes)
    record_activity("RULE_15", scanned=ingested, flagged=ingested, emitted=alerts_emitted,
                    duration_seconds=round(time.time() - _t0, 2), notes=notes)
    print(f"[RULE_15] {notes}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-alerts", action="store_true")
    args = parser.parse_args()
    run(emit=args.emit_alerts)
