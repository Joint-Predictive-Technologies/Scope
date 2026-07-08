#!/usr/bin/env python3
"""
RULE_ANOMALY — Attention Spike Detection
Fires when a ticker receives 2.5× its normal congressional-signal rate
in the most recent 30 days vs the prior 60-day baseline.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection

SPIKE_THRESHOLD = 2.5
ALERT_COOLDOWN_DAYS = 7


def run(emit: bool = False, dry_run: bool = False) -> None:
    conn = db_connection()

    rows = conn.execute("""
        SELECT
            REPLACE(ticker, '$', '') AS t,
            SUM(CASE WHEN datetime(created_at) >= datetime('now', '-30 days')
                     THEN 1 ELSE 0 END)                                    AS cnt_30d,
            SUM(CASE WHEN datetime(created_at) <  datetime('now', '-30 days')
                      AND datetime(created_at) >= datetime('now', '-90 days')
                     THEN 1 ELSE 0 END)                                    AS cnt_prev60d
        FROM alerts
        WHERE ticker IS NOT NULL AND ticker != ''
          AND ticker NOT LIKE '% %'
          AND datetime(created_at) >= datetime('now', '-90 days')
        GROUP BY t
        HAVING cnt_30d >= 3
        ORDER BY cnt_30d DESC
    """).fetchall()

    print(f"[RULE_ANOMALY] Evaluating {len(rows)} tickers")
    emitted = 0

    for r in rows:
        ticker    = r["t"]
        cnt_30d   = r["cnt_30d"]
        cnt_prev  = r["cnt_prev60d"]

        expected = max(1, cnt_prev / 2)
        ratio    = cnt_30d / expected

        if ratio < SPIKE_THRESHOLD:
            continue

        exists = conn.execute(
            "SELECT 1 FROM alerts WHERE rule='RULE_ANOMALY' AND ticker=?"
            " AND datetime(created_at) >= datetime('now', ?)",
            (ticker, f"-{ALERT_COOLDOWN_DAYS} days"),
        ).fetchone()
        if exists:
            print(f"[RULE_ANOMALY] [skip] {ticker} (already alerted within {ALERT_COOLDOWN_DAYS}d)")
            continue

        headline = (
            f"Attention Spike — {ticker} is getting "
            f"{ratio:.1f}× its normal signal rate"
        )
        detail = (
            f"{ticker} received {cnt_30d} signals in the last 30 days against a "
            f"trailing baseline of {expected:.1f}/month — a {ratio:.1f}× spike. "
            f"Elevated political attention often precedes a catalyst."
        )
        tags = f"{cnt_30d} signals,{ratio:.1f}x"

        print(
            f"[RULE_ANOMALY] {'[dry]' if dry_run else '[emit]'}"
            f" {ticker} — {ratio:.1f}× ({cnt_30d} vs {expected:.1f} expected)"
        )

        if not dry_run and emit:
            conn.execute(
                """INSERT INTO alerts (rule, headline, severity, tags, ticker, detail)
                   VALUES ('RULE_ANOMALY', ?, 'HIGH', ?, ?, ?)""",
                (headline, tags, ticker, detail),
            )
            conn.commit()
            emitted += 1

    print(f"[RULE_ANOMALY] Done — {emitted} alerts emitted")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_ANOMALY — Attention spike detection")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run",     action="store_true")
    args = p.parse_args()
    run(emit=args.emit_alerts, dry_run=args.dry_run)
