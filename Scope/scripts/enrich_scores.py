#!/usr/bin/env python3
"""
Compute Phase-2 intelligence scores (novelty, opportunity, evidence confidence,
time horizon, source quality) for alerts.

- Default: only score rows still at schema defaults — cheap, run every ~10 min
  from the scheduler so every newly-inserted alert gets scored no matter which
  rule script wrote it.
- --all: re-score every alert (one-time backfill after a scoring change).

Usage:
    python scripts/enrich_scores.py           # unscored only
    python scripts/enrich_scores.py --all     # full backfill
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, enrich_alert_scores, log_activity


def run(score_all: bool = False) -> int:
    start = time.time()
    conn = db_connection()
    n = enrich_alert_scores(conn, only_unscored=not score_all)
    log_activity(conn, "SCORING", scanned=n, emitted=0,
                 duration_seconds=round(time.time() - start, 2),
                 notes=f"{'full backfill' if score_all else 'incremental'} — {n} scored")
    conn.close()
    print(f"[SCORING] {'re-scored' if score_all else 'scored'} {n} alerts")
    return n


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Compute Phase-2 alert scores.")
    p.add_argument("--all", action="store_true", help="Re-score every alert (backfill).")
    p.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    run(score_all=args.all)
