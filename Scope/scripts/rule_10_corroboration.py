#!/usr/bin/env python3
"""
RULE_10 — Cross-source corroboration.

Fires when 2+ *distinct fundamental rules* hit the same ticker within 48h.
Noisy signal sources (Polymarket, OSINT, Reddit, Reddit-like) are excluded
as corroboration inputs — they're too high-volume and would create false
convergences. Only genuine fundamental rule signals count.

Dedup window is 7 days: once a ticker earns a RULE_10, it won't fire again
for a week even if more signals arrive.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection

RULE = "RULE_10"

# These rules are too noisy / too volume-heavy to count as corroboration sources.
# Polymarket has 578+ alerts, OSINT has hundreds — they'd pair with everything.
EXCLUDED_FROM_CORROBORATION = {
    "RULE_07",       # Polymarket — noise
    "RULE_OSINT",    # GDELT geopolitics — noise
    "RULE_REDDIT",   # Reddit sentiment — noise
    "RULE_10",       # self-referential
    "RULE_ANOMALY",  # ML anomaly — not a fundamental signal
}

DEDUP_WINDOW_DAYS = 7


def _candidate_alerts(conn, window_hours: int) -> list:
    excluded = ",".join(f"'{r}'" for r in EXCLUDED_FROM_CORROBORATION)
    return conn.execute(
        f"""
        SELECT ticker, rule, severity, headline, created_at
        FROM alerts
        WHERE ticker IS NOT NULL AND ticker != ''
          AND rule NOT IN ({excluded})
          AND severity IN ('HIGH', 'CRITICAL')
          AND created_at >= datetime('now', '-{int(window_hours)} hours')
        ORDER BY created_at DESC
        """
    ).fetchall()


def _already_corroborated(conn, ticker: str) -> bool:
    row = conn.execute(
        """
        SELECT id FROM alerts
        WHERE ticker = ?
          AND rule = 'RULE_10'
          AND created_at >= datetime('now', ? || ' days')
        LIMIT 1
        """,
        (ticker, f"-{DEDUP_WINDOW_DAYS}"),
    ).fetchone()
    return row is not None


def find_corroborated_tickers(conn, window_hours: int) -> dict[str, list]:
    rows = _candidate_alerts(conn, window_hours)

    ticker_rules: dict[str, set] = defaultdict(set)
    ticker_alerts: dict[str, list] = defaultdict(list)
    for row in rows:
        ticker_rules[row["ticker"]].add(row["rule"])
        ticker_alerts[row["ticker"]].append(row)

    # Require 2+ distinct rule types AND no RULE_10 already in last 7 days
    return {
        ticker: alerts
        for ticker, alerts in ticker_alerts.items()
        if len(ticker_rules[ticker]) >= 2
        and not _already_corroborated(conn, ticker)
    }


def _build_narrative(ticker: str, alerts: list, rules_fired: str) -> str:
    headlines = " | ".join(a["headline"] for a in alerts[:6])
    fallback = (
        f"Signals from {rules_fired} converged on {ticker} within 48 hours. "
        "See individual rule alerts for details."
    )
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return fallback
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        prompt = f"""You are a political intelligence analyst for macro investors.

The following signals have fired on ticker {ticker} within the past 48 hours:

{chr(10).join(f"  - {a['headline']}" for a in alerts[:6])}

Rules triggered: {rules_fired}

In 2-3 sentences, explain why this convergence of signals is notable for an investor watching {ticker}. Be specific. Do not say "you should buy" or give investment advice. Describe what the signals collectively suggest about political/regulatory/insider activity around this stock."""
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:
        print(f"  [warn] LLM call failed for {ticker}: {exc}", file=sys.stderr)
        return fallback


def run(dry_run: bool, window_hours: int = 48) -> tuple[int, int]:
    load_dotenv()
    conn = db_connection()

    clusters = find_corroborated_tickers(conn, window_hours)
    found = len(clusters)
    emitted = 0

    if not clusters:
        print("  No corroboration clusters found.")
        conn.close()
        return 0, 0

    for ticker, alerts in sorted(clusters.items()):
        rules_fired = ",".join(sorted({a["rule"] for a in alerts}))
        rule_count = len({a["rule"] for a in alerts})
        severities = {a["severity"] for a in alerts}

        print(f"  [{rule_count} rules] {ticker}  rules={rules_fired}")

        if dry_run:
            continue

        narrative = _build_narrative(ticker, alerts, rules_fired)
        print(f"    narrative: {narrative[:120]}")

        severity = "CRITICAL" if rule_count >= 3 or "CRITICAL" in severities else "HIGH"
        headline = (
            f"[CORROBORATION] {ticker}: {rule_count} independent signals "
            f"in {window_hours}h ({rules_fired})"
        )
        tags = json.dumps({
            "rules": rules_fired.split(","),
            "rule_count": rule_count,
            "rules_fired": rules_fired,
        })

        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, detail, tags)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (RULE, ticker, severity, headline, narrative, tags),
        )
        conn.execute(
            """UPDATE alerts SET lifecycle_stage = 'corroborated'
               WHERE ticker = ? AND rule != 'RULE_10'
                 AND (lifecycle_stage IS NULL OR lifecycle_stage = 'created')
                 AND datetime(created_at) >= datetime('now', '-48 hours')""",
            (ticker,),
        )
        conn.commit()
        emitted += 1

    conn.close()
    return found, emitted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-source corroboration: fire when 2+ distinct fundamental rules "
                    "hit the same ticker within 48h (RULE_10)."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print clusters without writing to DB or calling LLM.")
    parser.add_argument("--window-hours", type=int, default=48,
                        help="Lookback window in hours (default: 48).")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.dry_run:
        print("Dry run — no DB writes or LLM calls.")
    print(f"Scanning for corroboration clusters ({args.window_hours}h window) …")
    found, emitted = run(args.dry_run, args.window_hours)
    print(f"\n{found} cluster(s) found, {emitted} RULE_10 alert(s) emitted")


if __name__ == "__main__":
    main()
