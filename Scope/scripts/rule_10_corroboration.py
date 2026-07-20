#!/usr/bin/env python3
"""
RULE_10 — Cross-source corroboration.

Fires when 4+ *distinct fundamental rules* hit the same ticker within 24h.
Noisy signal sources (Polymarket, OSINT, Reddit, anomaly) are excluded
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
from jpt_common import db_connection, insert_alert, score_alert_fields


def upsert_theme(conn, ticker, distinct_rules, scores) -> int:
    """Create or evolve a Market Thesis (theme) for this ticker. Returns theme id.

    A corroboration on a ticker that already has an active theme advances its
    lifecycle (Emerging → Developing → Confirmed) and refreshes its scores; a new
    ticker starts an Emerging thesis. (Data hierarchy §3 of the product spec.)"""
    rules_json = json.dumps(sorted(set(distinct_rules)))
    existing = conn.execute(
        "SELECT id, signal_count FROM themes WHERE primary_ticker = ? "
        "AND status NOT IN ('Resolved','Fading')",
        (ticker,),
    ).fetchone()
    if existing:
        new_count = (existing["signal_count"] or 0) + 1
        status = "Confirmed" if new_count >= 5 else "Developing" if new_count >= 2 else "Emerging"
        conn.execute(
            """UPDATE themes SET
                   signal_count = ?, evidence_confidence = ?, opportunity_score = ?,
                   novelty_score = ?, time_horizon = ?, supporting_rules = ?,
                   what_changed = ?, status = ?, last_updated = datetime('now')
               WHERE id = ?""",
            (new_count, scores["evidence_confidence"], scores["opportunity_score"],
             scores["novelty_score"], scores["time_horizon"], rules_json,
             f"New corroboration: {', '.join(sorted(set(distinct_rules)))}", status,
             existing["id"]),
        )
        return existing["id"]
    cur = conn.execute(
        """INSERT INTO themes (
               title, primary_ticker, affected_tickers, status,
               evidence_confidence, opportunity_score, novelty_score, time_horizon,
               supporting_rules, signal_count, what_changed,
               first_signal_at, last_updated)
           VALUES (?, ?, ?, 'Emerging', ?, ?, ?, ?, ?, 1, ?, datetime('now'), datetime('now'))""",
        (f"Convergence: {ticker} — {len(set(distinct_rules))} rules aligned",
         ticker, json.dumps([ticker]),
         scores["evidence_confidence"], scores["opportunity_score"],
         scores["novelty_score"], scores["time_horizon"], rules_json,
         f"Thesis opened from convergence: {', '.join(sorted(set(distinct_rules)))}"),
    )
    return cur.lastrowid

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

DEDUP_WINDOW_DAYS   = 7
MIN_DISTINCT_RULES  = 4   # 4 independent rule types required (raised from 2)


def _candidate_alerts(conn, window_hours: int) -> list:
    excluded = ",".join(f"'{r}'" for r in EXCLUDED_FROM_CORROBORATION)
    return conn.execute(
        f"""
        SELECT id, ticker, rule, severity, headline, created_at
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

    # Require 4+ distinct rule types AND no RULE_10 already in last 7 days
    return {
        ticker: alerts
        for ticker, alerts in ticker_alerts.items()
        if len(ticker_rules[ticker]) >= MIN_DISTINCT_RULES
        and not _already_corroborated(conn, ticker)
    }


def _build_narrative(ticker: str, alerts: list, rules_fired: str, window_hours: int = 24) -> str:
    headlines = " | ".join(a["headline"] for a in alerts[:6])
    fallback = (
        f"Signals from {rules_fired} converged on {ticker} within {window_hours}h. "
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


MAX_PER_RUN = 10  # hard cap — prevents any future flood


def run(dry_run: bool, window_hours: int = 24) -> tuple[int, int]:
    import time as _time
    from jpt_common import record_activity
    _t0 = _time.time()
    load_dotenv()
    conn = db_connection()

    clusters = find_corroborated_tickers(conn, window_hours)
    found = len(clusters)
    emitted = 0

    if not clusters:
        print("  No corroboration clusters found.")
        conn.close()
        record_activity("RULE_10", scanned=0, flagged=0, emitted=0,
                        duration_seconds=round(_time.time() - _t0, 2))
        return 0, 0

    # Sort by distinct rule count desc (most corroborated first), cap at MAX_PER_RUN
    ranked = sorted(
        clusters.items(),
        key=lambda kv: len({a["rule"] for a in kv[1]}),
        reverse=True,
    )[:MAX_PER_RUN]
    if len(clusters) > MAX_PER_RUN:
        print(f"  [{len(clusters)} qualified — capped at {MAX_PER_RUN} per run]")

    for ticker, alerts in ranked:
        rules_fired = ",".join(sorted({a["rule"] for a in alerts}))
        rule_count = len({a["rule"] for a in alerts})
        severities = {a["severity"] for a in alerts}

        print(f"  [{rule_count} rules] {ticker}  rules={rules_fired}")

        if dry_run:
            continue

        narrative = _build_narrative(ticker, alerts, rules_fired, window_hours)
        print(f"    narrative: {narrative[:120]}")

        severity = "CRITICAL" if rule_count >= MIN_DISTINCT_RULES or "CRITICAL" in severities else "HIGH"
        headline = (
            f"[CORROBORATION] {ticker}: {rule_count} independent signals "
            f"in {window_hours}h ({rules_fired})"
        )
        distinct_rules = sorted({a["rule"] for a in alerts})
        tags = json.dumps({
            "rules": distinct_rules,
            "rule_count": rule_count,
            "rules_fired": rules_fired,
        })

        # Insert via the scoring wrapper so the corroboration carries real
        # evidence/opportunity/novelty, and capture its id for theme linking.
        alert_id = insert_alert(
            conn, rule=RULE, ticker=ticker, severity=severity,
            headline=headline, detail=narrative, tags=tags,
            distinct_rule_count=rule_count,
        )
        conn.execute(
            """UPDATE alerts SET lifecycle_stage = 'corroborated'
               WHERE ticker = ? AND rule != 'RULE_10'
                 AND (lifecycle_stage IS NULL OR lifecycle_stage = 'created')
                 AND datetime(created_at) >= datetime('now', '-48 hours')""",
            (ticker,),
        )

        # Feature 4 — create/evolve the Market Thesis and link the evidence.
        scores = score_alert_fields(conn, RULE, ticker, headline, tags)
        theme_id = upsert_theme(conn, ticker, distinct_rules, scores)
        conn.execute("UPDATE alerts SET theme_id = ? WHERE id = ?", (theme_id, alert_id))
        # Link this corroboration + its contributing signals to the theme.
        conn.execute("INSERT INTO theme_signals (theme_id, alert_id) VALUES (?, ?)",
                     (theme_id, alert_id))
        for a in alerts:
            if a["id"] is not None:
                conn.execute(
                    "INSERT INTO theme_signals (theme_id, alert_id) VALUES (?, ?)",
                    (theme_id, a["id"]),
                )
        conn.commit()
        emitted += 1

    conn.close()
    record_activity("RULE_10", scanned=found, flagged=found, emitted=emitted,
                    duration_seconds=round(_time.time() - _t0, 2))
    return found, emitted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-source corroboration: fire when 2+ distinct fundamental rules "
                    "hit the same ticker within 48h (RULE_10)."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print clusters without writing to DB or calling LLM.")
    parser.add_argument("--window-hours", type=int, default=24,
                        help="Lookback window in hours (default: 24).")
    # Accepted (and ignored) for scheduler-runner uniformity — the scheduler
    # invokes every job with --emit-alerts; without this, argparse would reject it
    # (exit 2) and RULE_10 would fail on every scheduled run.
    parser.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
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
