#!/usr/bin/env python3
"""
rule_09_lobbying.py

Detects companies whose quarterly lobbying spend has spiked significantly
year-over-year using the Senate LDA (Lobbying Disclosure Act) API.
"""

from __future__ import annotations

import argparse
import difflib
import time
from collections import defaultdict
from datetime import date
from typing import NamedTuple

import requests

from jpt_common import db_connection


RULE = "RULE_09"
LDA_API = "https://lda.senate.gov/api/v1/filings/"
HEADERS = {"User-Agent": "Scope/0.1 sloppysecondstbb@gmail.com"}
SLEEP = 0.3

QUARTERLY_TYPES = {"Q1", "Q2", "Q3", "Q4"}

# Historical quarter pairs to compare when current quarter has sparse data.
# Format: (current_filing_type, current_year, prior_filing_type, prior_year)
COMPARE_PERIODS = [
    ("Q2", 2026, "Q2", 2025),
    ("Q1", 2026, "Q1", 2025),
    ("Q4", 2025, "Q4", 2024),
    ("Q3", 2025, "Q3", 2024),
]

# Trigger thresholds
MIN_SPEND = 50_000.0
MIN_YOY_PCT = 50.0

# Severity thresholds
HIGH_PCT = 100.0
HIGH_SPEND = 500_000.0

# Fuzzy match cutoff against tickers.company_name
TICKER_CUTOFF = 0.7


def current_quarter() -> tuple[str, int]:
    """Return (filing_type, year) for the current calendar quarter."""
    today = date.today()
    q = (today.month - 1) // 3 + 1
    return f"Q{q}", today.year


# ---------------------------------------------------------------------------
# LDA API pagination
# ---------------------------------------------------------------------------

class FilingRecord(NamedTuple):
    client_name: str
    registrant_name: str
    spend: float
    issue_codes: list[str]
    issue_descs: list[str]
    filing_type: str
    filing_year: int


def _spend(filing: dict) -> float:
    for field in ("income", "expenses"):
        val = filing.get(field)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    return 0.0


def fetch_filings(filing_type: str, filing_year: int) -> list[FilingRecord]:
    records: list[FilingRecord] = []
    url: str | None = (
        f"{LDA_API}?filing_type={filing_type}&filing_year={filing_year}"
        "&format=json&page_size=100"
    )

    while url:
        time.sleep(SLEEP)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException:
            break

        if r.status_code == 429:
            time.sleep(10)
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
            except requests.RequestException:
                break

        if r.status_code != 200:
            break

        data = r.json()
        for filing in data.get("results", []):
            client = filing.get("client") or {}
            registrant = filing.get("registrant") or {}
            activities = filing.get("lobbying_activities") or []

            codes: list[str] = []
            descs: list[str] = []
            seen_codes: set[str] = set()
            for act in activities:
                code = act.get("general_issue_code_display") or ""
                desc = (act.get("description") or "").strip()
                if code and code not in seen_codes:
                    codes.append(code)
                    seen_codes.add(code)
                if desc:
                    descs.append(desc[:80])

            records.append(FilingRecord(
                client_name=(client.get("name") or "").strip(),
                registrant_name=(registrant.get("name") or "").strip(),
                spend=_spend(filing),
                issue_codes=codes,
                issue_descs=descs,
                filing_type=filing_type,
                filing_year=filing_year,
            ))

        url = data.get("next") or None

    return records


# ---------------------------------------------------------------------------
# Ticker fuzzy match
# ---------------------------------------------------------------------------

def load_ticker_names(conn) -> list[tuple[str, str]]:
    """Return [(symbol, company_name), ...] from the tickers table."""
    rows = conn.execute("SELECT symbol, company_name FROM tickers").fetchall()
    return [(row["symbol"], (row["company_name"] or "").upper()) for row in rows]


def match_ticker(client_name: str, ticker_names: list[tuple[str, str]]) -> str | None:
    needle = client_name.upper()
    names = [tn[1] for tn in ticker_names]
    matches = difflib.get_close_matches(needle, names, n=1, cutoff=TICKER_CUTOFF)
    if not matches:
        return None
    idx = names.index(matches[0])
    return ticker_names[idx][0]


# ---------------------------------------------------------------------------
# Group + compare
# ---------------------------------------------------------------------------

def _group_key(rec: FilingRecord) -> tuple[str, str]:
    return (rec.client_name, rec.registrant_name)


def find_spikes(
    current: list[FilingRecord],
    prior: list[FilingRecord],
) -> list[dict]:
    prior_idx: dict[tuple[str, str], float] = defaultdict(float)
    for rec in prior:
        prior_idx[_group_key(rec)] += rec.spend

    current_agg: dict[tuple[str, str], dict] = {}
    for rec in current:
        key = _group_key(rec)
        if key not in current_agg:
            current_agg[key] = {
                "client_name": rec.client_name,
                "registrant_name": rec.registrant_name,
                "spend": 0.0,
                "issue_codes": [],
                "issue_descs": [],
                "filing_type": rec.filing_type,
                "filing_year": rec.filing_year,
            }
        entry = current_agg[key]
        entry["spend"] += rec.spend
        for code in rec.issue_codes:
            if code not in entry["issue_codes"]:
                entry["issue_codes"].append(code)
        entry["issue_descs"].extend(rec.issue_descs)

    spikes: list[dict] = []
    for key, entry in current_agg.items():
        prior_spend = prior_idx.get(key, 0.0)
        curr_spend = entry["spend"]

        if curr_spend < MIN_SPEND:
            continue
        if prior_spend <= 0:
            continue

        pct = (curr_spend - prior_spend) / prior_spend * 100.0
        if pct < MIN_YOY_PCT:
            continue

        spikes.append({**entry, "prior_spend": prior_spend, "pct_change": pct})

    spikes.sort(key=lambda x: x["pct_change"], reverse=True)
    return spikes


# ---------------------------------------------------------------------------
# Alert helpers
# ---------------------------------------------------------------------------

def alert_exists(conn, headline: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE rule = ?
          AND headline = ?
          AND datetime(created_at) >= datetime('now', '-90 days')
        LIMIT 1
        """,
        (RULE, headline),
    ).fetchone()
    return row is not None


def insert_alert(conn, headline: str, ticker: str | None, severity: str, tags: str) -> None:
    conn.execute(
        "INSERT INTO alerts (rule, headline, severity, tags, ticker) VALUES (?,?,?,?,?)",
        (RULE, headline, severity, tags, ticker),
    )
    conn.commit()


def _fmt_spend(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    return f"${v / 1_000:.0f}K"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    filing_type: str,
    filing_year: int,
    emit_alerts: bool,
    prior_type: str | None = None,
    prior_year: int | None = None,
) -> tuple[int, int]:
    if prior_type is None:
        prior_type = filing_type
    if prior_year is None:
        prior_year = filing_year - 1

    print(f"Fetching {filing_type} {filing_year} …")
    current = fetch_filings(filing_type, filing_year)
    print(f"  {len(current)} current filings")

    print(f"Fetching {prior_type} {prior_year} …")
    prior = fetch_filings(prior_type, prior_year)
    print(f"  {len(prior)} prior-year filings")

    spikes = find_spikes(current, prior)
    print(f"  {len(spikes)} spend spike(s) found")

    if not spikes:
        return 0, 0

    conn = db_connection()
    ticker_names = load_ticker_names(conn)

    triggered = len(spikes)
    emitted = 0

    for spike in spikes:
        client = spike["client_name"]
        registrant = spike["registrant_name"]
        curr_spend = spike["spend"]
        prior_spend = spike["prior_spend"]
        pct = spike["pct_change"]
        codes = spike["issue_codes"]

        ticker = match_ticker(client, ticker_names)
        ticker_label = f"${ticker}" if ticker else client[:30]

        issues_str = ", ".join(codes[:4]) if codes else "General"
        headline = (
            f"Lobbying spike: {ticker_label} spend up {pct:.0f}% YoY "
            f"({_fmt_spend(prior_spend)} → {_fmt_spend(curr_spend)}) "
            f"— issues: {issues_str}"
        )

        severity = (
            "HIGH"
            if pct >= HIGH_PCT or curr_spend >= HIGH_SPEND
            else "MEDIUM"
        )

        tags = ", ".join(filter(None, [
            client,
            registrant,
            issues_str,
            f"{_fmt_spend(prior_spend)}→{_fmt_spend(curr_spend)}",
        ]))

        print(
            f"  [{severity}] {pct:.0f}% YoY  {_fmt_spend(prior_spend)} → {_fmt_spend(curr_spend)}"
            f"  {client[:50]}"
            + (f"  → {ticker}" if ticker else "")
        )

        if emit_alerts:
            if not alert_exists(conn, headline):
                insert_alert(conn, headline, ticker, severity, tags)
                emitted += 1

    conn.close()
    return triggered, emitted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    default_type, default_year = current_quarter()
    parser = argparse.ArgumentParser(
        description="Detect lobbying spend spikes via Senate LDA API (RULE_09)."
    )
    parser.add_argument(
        "--filing-type",
        default=default_type,
        choices=list(QUARTERLY_TYPES),
        help=f"Quarterly filing type. Default: {default_type} (current quarter).",
    )
    parser.add_argument(
        "--filing-year",
        type=int,
        default=default_year,
        help=f"Filing year. Default: {default_year}.",
    )
    parser.add_argument(
        "--emit-alerts",
        action="store_true",
        help="Insert triggered filings into the alerts table.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    _t0 = time.time()
    if args.filing_type == current_quarter()[0] and args.filing_year == current_quarter()[1]:
        # Default run: sweep all COMPARE_PERIODS so historical data always shows
        total_triggered = total_emitted = 0
        for cur_type, cur_year, prior_type, prior_year in COMPARE_PERIODS:
            print(f"\n── {cur_type} {cur_year} vs {prior_type} {prior_year} ──")
            t, e = run(cur_type, cur_year, args.emit_alerts, prior_type, prior_year)
            total_triggered += t
            total_emitted += e
        print(f"\n{total_triggered} spike(s) detected, {total_emitted} alert(s) emitted")
    else:
        total_triggered, total_emitted = run(args.filing_type, args.filing_year, args.emit_alerts)
        print(f"\n{total_triggered} spike(s) detected, {total_emitted} alert(s) emitted")

    from jpt_common import record_activity
    record_activity("RULE_09", scanned=total_triggered, flagged=total_triggered,
                    emitted=total_emitted, duration_seconds=round(time.time() - _t0, 2))


if __name__ == "__main__":
    main()
