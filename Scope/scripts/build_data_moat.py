#!/usr/bin/env python3
"""
Data Moat Builder — runs weekly.
Joins every alert with its post-alert price outcome to build a proprietary
labeled dataset: political signal → market outcome.

Run weekly via cron:
  0 2 * * 0 cd /path/to/Scope && python scripts/build_data_moat.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, classify_sector

MOAT_DIR = Path(__file__).resolve().parent.parent / "data" / "moat"


def run() -> None:
    MOAT_DIR.mkdir(parents=True, exist_ok=True)

    conn = db_connection()

    # Every alert older than 30 days with a ticker, joined with backtest results
    rows = conn.execute("""
        SELECT
            a.id, a.rule, a.ticker, a.severity, a.created_at,
            a.member_id, a.tags, a.confidence_score, a.lifecycle_stage,
            a.why_matters,
            b.return_7d, b.return_30d, b.price_at AS price_at_alert,
            mf.defense_pct, mf.energy_pct, mf.tech_pct, mf.finance_pct, mf.pharma_pct,
            m.full_name AS member_name, m.party, m.chamber, m.state
        FROM alerts a
        LEFT JOIN backtest_results b   ON b.alert_id = a.id
        LEFT JOIN members m            ON m.bioguide_id = a.member_id
        LEFT JOIN member_funding mf    ON mf.bioguide_id = a.member_id
        WHERE a.created_at <= datetime('now', '-30 days')
          AND a.ticker IS NOT NULL AND a.ticker != ''
          AND a.rule != 'RULE_10'
        ORDER BY a.created_at DESC
    """).fetchall()

    # Enrich with concurrent signals (other rules on same ticker within ±7 days)
    labeled = []
    for row in rows:
        alert_id  = row["id"]
        ticker    = row["ticker"]
        created   = row["created_at"]
        sector    = classify_sector(ticker)

        # Count concurrent signals
        concurrent = conn.execute("""
            SELECT COUNT(DISTINCT rule) as cnt, GROUP_CONCAT(DISTINCT rule) as rules
            FROM alerts
            WHERE ticker = ? AND id != ?
              AND datetime(created_at) BETWEEN datetime(?, '-7 days') AND datetime(?, '+7 days')
        """, (ticker, alert_id, created, created)).fetchone()

        # Parse tags
        tags_obj = {}
        try:
            tags_obj = json.loads(row["tags"] or "{}")
        except Exception:
            pass

        labeled.append({
            "id":               alert_id,
            "rule":             row["rule"],
            "ticker":           ticker,
            "sector":           sector,
            "severity":         row["severity"],
            "created_at":       created,
            "lifecycle_stage":  row["lifecycle_stage"],
            "confidence_score": row["confidence_score"],
            "member_id":        row["member_id"],
            "member_name":      row["member_name"],
            "party":            row["party"],
            "chamber":          row["chamber"],
            "state":            row["state"],
            "concurrent_rules": concurrent["cnt"] if concurrent else 0,
            "concurrent_rule_list": (concurrent["rules"] or "").split(",") if concurrent else [],
            "tags":             tags_obj,
            # Funding profile (for conflict scoring)
            "member_defense_pct": row["defense_pct"],
            "member_energy_pct":  row["energy_pct"],
            "member_tech_pct":    row["tech_pct"],
            "member_finance_pct": row["finance_pct"],
            "member_pharma_pct":  row["pharma_pct"],
            # Outcomes (may be None if backtest hasn't run)
            "price_at_alert":  row["price_at_alert"],
            "return_7d":       row["return_7d"],
            "return_30d":      row["return_30d"],
        })

    conn.close()

    export_path = MOAT_DIR / f"labeled_{datetime.now().strftime('%Y-%m')}.json"
    with open(export_path, "w") as f:
        json.dump(labeled, f, indent=2, default=str)

    # Also write a summary stats file
    total   = len(labeled)
    with_outcomes = sum(1 for r in labeled if r["return_30d"] is not None)
    positive_30d  = sum(1 for r in labeled if (r["return_30d"] or 0) > 5)
    win_rate = positive_30d / max(with_outcomes, 1) * 100

    summary = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "total_examples": total,
        "with_outcomes":  with_outcomes,
        "win_rate_30d":   round(win_rate, 1),
        "rules_breakdown": {},
    }

    for row in labeled:
        rule = row["rule"]
        if rule not in summary["rules_breakdown"]:
            summary["rules_breakdown"][rule] = {"count": 0, "with_outcomes": 0, "avg_return_30d": 0}
        summary["rules_breakdown"][rule]["count"] += 1
        if row["return_30d"] is not None:
            summary["rules_breakdown"][rule]["with_outcomes"] += 1

    summary_path = MOAT_DIR / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"[DataMoat] {total} labeled examples → {export_path}")
    print(f"[DataMoat] {with_outcomes} have 30d outcomes, {win_rate:.1f}% win rate (>5% 30d return)")


if __name__ == "__main__":
    run()
