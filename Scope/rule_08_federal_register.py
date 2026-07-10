#!/usr/bin/env python3
"""
rule_08_federal_register.py

Detects new regulatory rule proposals in the Federal Register that affect
tracked market sectors and emits RULE_08 alerts.
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta
from urllib.parse import urlencode

import requests

from jpt_common import db_connection


RULE = "RULE_08"
FR_API = "https://www.federalregister.gov/api/v1/documents.json"
HEADERS = {"User-Agent": "Scope/0.1 sloppysecondstbb@gmail.com"}
SLEEP = 0.5

SECTOR_MAP: dict[str, list[str]] = {
    "bank":           ["$JPM", "$BAC", "$WFC", "$GS"],
    "crypto":         ["$COIN", "$MSTR", "$IBIT"],
    "pharmaceutical": ["$JNJ", "$PFE", "$MRK"],
    "drug":           ["$JNJ", "$PFE", "$MRK"],
    "energy":         ["$XLE", "$USO", "$XOM"],
    "oil":            ["$XLE", "$USO", "$XOM"],
    "telecom":        ["$T", "$VZ", "$TMUS"],
    "antitrust":      ["$GOOGL", "$META", "$AMZN", "$AAPL"],
    "tech":           ["$GOOGL", "$META", "$AMZN", "$AAPL", "$MSFT"],
    "airline":        ["$DAL", "$UAL", "$AAL"],
    "defense":        ["$LMT", "$RTX", "$NOC"],
    "finance":        ["$JPM", "$BAC", "$GS", "$MS"],
    "securities":     ["$GS", "$MS", "$JPM"],
    "insurance":      ["$BRK.B", "$AIG", "$MET"],
    "housing":        ["$DHI", "$LEN", "$TOL"],
    "agriculture":    ["$ADM", "$BG", "$MOS"],
}


# ---------------------------------------------------------------------------
# Federal Register API
# ---------------------------------------------------------------------------

def fetch_proposed_rules(since: str) -> list[dict]:
    documents: list[dict] = []
    page = 1

    while True:
        time.sleep(SLEEP)
        _fields = [
            "title", "abstract", "publication_date",
            "agencies", "docket_ids", "significant", "topics",
            "comments_close_on",
        ]
        qs = urlencode(
            [(f"fields[]", f) for f in _fields]
            + [
                ("per_page", 100),
                ("order", "newest"),
                ("conditions[type][]", "PRORULE"),
                ("page", page),
            ]
        )
        try:
            r = requests.get(f"{FR_API}?{qs}", headers=HEADERS, timeout=30)
        except requests.RequestException:
            break

        if r.status_code != 200:
            break

        data = r.json()
        results = data.get("results", [])
        if not results:
            break

        for doc in results:
            pub_date = doc.get("publication_date", "")
            if pub_date and pub_date < since:
                return documents
            documents.append(doc)

        total_pages = data.get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1

    return documents


# ---------------------------------------------------------------------------
# Sector matching
# ---------------------------------------------------------------------------

def match_tickers(title: str, abstract: str | None) -> list[str]:
    haystack = (title + " " + (abstract or "")).lower()
    tickers: list[str] = []
    seen: set[str] = set()
    for keyword, syms in SECTOR_MAP.items():
        if keyword in haystack:
            for s in syms:
                if s not in seen:
                    tickers.append(s)
                    seen.add(s)
    return tickers


# ---------------------------------------------------------------------------
# Alert helpers
# ---------------------------------------------------------------------------

def alert_exists(conn, headline: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE rule = ?
          AND headline = ?
          AND datetime(created_at) >= datetime('now', '-14 days')
        LIMIT 1
        """,
        (RULE, headline),
    ).fetchone()
    return row is not None


def insert_alert(
    conn,
    headline: str,
    ticker: str | None,
    severity: str,
    tags: str,
) -> None:
    conn.execute(
        "INSERT INTO alerts (rule, headline, severity, tags, ticker) VALUES (?,?,?,?,?)",
        (RULE, headline, severity, tags, ticker),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Document processing
# ---------------------------------------------------------------------------

def _agency_name(doc: dict) -> str:
    agencies = doc.get("agencies") or []
    if agencies and isinstance(agencies[0], dict):
        return agencies[0].get("name", "")
    return str(agencies[0]) if agencies else ""


def _docket_id(doc: dict) -> str:
    dockets = doc.get("docket_ids") or []
    return dockets[0] if dockets else ""


def _comment_date(doc: dict) -> str:
    raw = doc.get("comments_close_on") or ""
    if not raw:
        return ""
    try:
        from datetime import datetime
        return datetime.strptime(raw[:10], "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return raw[:10]


def process_document(doc: dict, emit_alerts: bool, conn) -> bool:
    title = (doc.get("title") or "").strip()
    abstract = doc.get("abstract") or ""
    significant = bool(doc.get("significant"))
    pub_date = doc.get("publication_date", "")[:10]

    tickers = match_tickers(title, abstract)
    if not tickers:
        return False

    ticker_str = " ".join(tickers)
    comment_str = _comment_date(doc)
    comment_part = f" (comment period: {comment_str})" if comment_str else ""
    headline = f"Federal Register: {title[:80]} — affects {ticker_str}{comment_part}"

    severity = "HIGH" if significant else "MEDIUM"

    agency = _agency_name(doc)
    docket = _docket_id(doc)
    tags = ", ".join(filter(None, [agency, docket, pub_date]))

    print(
        f"  [{severity}] {pub_date}  {ticker_str}\n"
        f"    {title[:90]}"
        + (f"\n    Agency: {agency}" if agency else "")
        + (f"  Docket: {docket}" if docket else "")
    )

    if emit_alerts and conn is not None:
        if not alert_exists(conn, headline):
            insert_alert(conn, headline, ticker_str, severity, tags)
            return True

    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect Federal Register proposed rules affecting tracked sectors (RULE_08)."
    )
    parser.add_argument(
        "--since",
        default=str(date.today() - timedelta(days=14)),
        help="Earliest publication date to consider (YYYY-MM-DD). Default: 14 days ago.",
    )
    parser.add_argument(
        "--emit-alerts",
        action="store_true",
        help="Insert triggered documents into the alerts table.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    _t0 = time.time()
    print(f"Fetching Federal Register proposed rules since {args.since} …")
    documents = fetch_proposed_rules(args.since)
    print(f"  {len(documents)} document(s) fetched")

    triggered = 0
    emitted = 0

    conn = db_connection() if args.emit_alerts else None

    for doc in documents:
        was_emitted = process_document(doc, args.emit_alerts, conn)
        if match_tickers(doc.get("title") or "", doc.get("abstract") or ""):
            triggered += 1
        if was_emitted:
            emitted += 1

    if conn is not None:
        conn.close()

    print(f"\n{triggered} rule(s) matched sector keywords, {emitted} alert(s) emitted")
    from jpt_common import record_activity
    record_activity("RULE_08", scanned=len(documents), flagged=triggered, emitted=emitted,
                    duration_seconds=round(time.time() - _t0, 2))


if __name__ == "__main__":
    main()
