#!/usr/bin/env python3
"""
Alert severity decay.

Alerts naturally lose relevance as they age without corroboration. This job
downgrades stale severities so the live feed and severity counts stay honest:

  CRITICAL → HIGH   after 7 days  with no RULE_10 corroboration on the ticker
  HIGH     → MEDIUM after 14 days with no RULE_10 corroboration on the ticker

RULE_10 itself is never decayed (it *is* the corroboration signal). RULE_01B
and RULE_02 are protected from the HIGH→MEDIUM step because a first-touch or
cluster disclosure keeps its significance regardless of age.

Scheduled daily at 01:00 (see api/main.py _CRON_SCHEDULE).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection


def run(dry_run: bool = False) -> tuple[int, int]:
    conn = db_connection()

    # Preview counts first so we can report accurately even on a dry run.
    crit_to_high = conn.execute(
        """
        SELECT COUNT(*) FROM alerts
        WHERE severity = 'CRITICAL'
          AND rule != 'RULE_10'
          AND datetime(created_at) < datetime('now', '-7 days')
          AND ticker NOT IN (
              SELECT DISTINCT ticker FROM alerts
              WHERE rule = 'RULE_10'
                AND datetime(created_at) >= datetime('now', '-7 days')
                AND ticker IS NOT NULL AND ticker != ''
          )
        """
    ).fetchone()[0]

    high_to_med = conn.execute(
        """
        SELECT COUNT(*) FROM alerts
        WHERE severity = 'HIGH'
          AND rule NOT IN ('RULE_10', 'RULE_01B', 'RULE_02')
          AND datetime(created_at) < datetime('now', '-14 days')
          AND ticker NOT IN (
              SELECT DISTINCT ticker FROM alerts
              WHERE rule = 'RULE_10'
                AND datetime(created_at) >= datetime('now', '-14 days')
                AND ticker IS NOT NULL AND ticker != ''
          )
        """
    ).fetchone()[0]

    if dry_run:
        conn.close()
        print(f"[DECAY] dry-run — would downgrade {crit_to_high} CRITICAL→HIGH, "
              f"{high_to_med} HIGH→MEDIUM")
        return crit_to_high, high_to_med

    conn.execute(
        """
        UPDATE alerts SET severity = 'HIGH'
        WHERE severity = 'CRITICAL'
          AND rule != 'RULE_10'
          AND datetime(created_at) < datetime('now', '-7 days')
          AND ticker NOT IN (
              SELECT DISTINCT ticker FROM alerts
              WHERE rule = 'RULE_10'
                AND datetime(created_at) >= datetime('now', '-7 days')
                AND ticker IS NOT NULL AND ticker != ''
          )
        """
    )
    conn.execute(
        """
        UPDATE alerts SET severity = 'MEDIUM'
        WHERE severity = 'HIGH'
          AND rule NOT IN ('RULE_10', 'RULE_01B', 'RULE_02')
          AND datetime(created_at) < datetime('now', '-14 days')
          AND ticker NOT IN (
              SELECT DISTINCT ticker FROM alerts
              WHERE rule = 'RULE_10'
                AND datetime(created_at) >= datetime('now', '-14 days')
                AND ticker IS NOT NULL AND ticker != ''
          )
        """
    )
    conn.commit()
    conn.close()
    print(f"[DECAY] downgraded {crit_to_high} CRITICAL→HIGH, {high_to_med} HIGH→MEDIUM")
    return crit_to_high, high_to_med


def main() -> None:
    parser = argparse.ArgumentParser(description="Decay stale alert severities.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing.")
    # Accept --emit-alerts for scheduler-runner uniformity (no-op here).
    parser.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
