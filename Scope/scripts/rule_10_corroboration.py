#!/usr/bin/env python3
# Rule 10 — Cross-source corroboration. Runs after all other rules. Queries
# alerts table for 2+ distinct rule_ids on the same ticker within 48h window.
# LLM: Groq (free tier) — llama-3.3-70b-versatile

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection


RULE = "RULE_10"

CORROBORATION_SQL = """
SELECT
    a1.ticker,
    GROUP_CONCAT(DISTINCT a1.rule)  AS rules_fired,
    COUNT(DISTINCT a1.rule)         AS rule_count,
    MIN(a1.created_at)              AS first_fire,
    MAX(a1.created_at)              AS last_fire,
    GROUP_CONCAT(a1.headline, ' | ') AS headlines
FROM alerts a1
WHERE a1.created_at >= datetime('now', ? || ' hours')
  AND a1.ticker IS NOT NULL
  AND a1.ticker != ''
  AND a1.rule != 'RULE_10'
GROUP BY a1.ticker
HAVING COUNT(DISTINCT a1.rule) >= 2
ORDER BY rule_count DESC
"""


def ensure_detail_column(conn) -> None:
    try:
        conn.execute("ALTER TABLE alerts ADD COLUMN detail TEXT")
        conn.commit()
    except Exception:
        pass  # column already exists


def alert_exists(conn, ticker: str, rules_fired: str, window_hours: int) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE rule = ?
          AND ticker = ?
          AND tags LIKE ?
          AND created_at >= datetime('now', ? || ' hours')
        LIMIT 1
        """,
        (RULE, ticker, f'%"rules_fired": "{rules_fired}"%', f"-{window_hours}"),
    ).fetchone()
    return row is not None


def build_narrative_prompt(ticker: str, headlines: str, rules_fired: str) -> str:
    headlines_formatted = "\n".join(
        f"  - {h.strip()}" for h in headlines.split("|") if h.strip()
    )
    return f"""You are a political intelligence analyst for macro investors.

The following signals have fired on ticker {ticker} within the past 48 hours:

{headlines_formatted}

Rules triggered: {rules_fired}

In 2-3 sentences, explain why this convergence of signals is notable for an investor watching {ticker}. Be specific. Do not say "you should buy" or give investment advice. Describe what the signals collectively suggest about political/regulatory/insider activity around this stock."""


def generate_narrative(ticker: str, headlines: str, rules_fired: str) -> str:
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
        prompt = build_narrative_prompt(ticker, headlines, rules_fired)
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
    ensure_detail_column(conn)

    clusters = conn.execute(CORROBORATION_SQL, (f"-{window_hours}",)).fetchall()

    found = len(clusters)
    emitted = 0

    if not clusters:
        return 0, 0

    for row in clusters:
        ticker = row["ticker"]
        rules_fired = row["rules_fired"]
        rule_count = row["rule_count"]
        first_fire = row["first_fire"]
        last_fire = row["last_fire"]
        headlines = row["headlines"] or ""

        print(
            f"  [{rule_count} rules] {ticker}  rules={rules_fired}\n"
            f"    window: {first_fire} → {last_fire}"
        )

        if dry_run:
            continue

        if alert_exists(conn, ticker, rules_fired, window_hours):
            print(f"    [skip] duplicate RULE_10 already exists")
            continue

        narrative = generate_narrative(ticker, headlines, rules_fired)
        print(f"    narrative: {narrative[:120]}")

        severity = "CRITICAL" if rule_count >= 3 else "HIGH"
        headline = f"[CORROBORATION] {ticker}: {rule_count} independent signals in 48h ({rules_fired})"
        tags = json.dumps({"rules": rules_fired.split(","), "rule_count": rule_count, "rules_fired": rules_fired})

        conn.execute(
            """
            INSERT INTO alerts (rule, ticker, severity, headline, detail, tags)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (RULE, ticker, severity, headline, narrative, tags),
        )
        conn.commit()
        emitted += 1

    conn.close()
    return found, emitted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-source corroboration: fire when 2+ rules hit the same ticker in 48h (RULE_10)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print clusters without calling LLM or writing to DB.",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=48,
        help="Lookback window in hours. Default: 48. Use a larger value to test against older alerts.",
    )
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
