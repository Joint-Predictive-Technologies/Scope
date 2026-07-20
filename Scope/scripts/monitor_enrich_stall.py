#!/usr/bin/env python3
"""
MONITOR_ENRICH_STALL — make a silent scoring stall loud.

Write path (b) rules insert alerts raw and depend on scripts/enrich_scores.py
(10-min job) to backfill scores. If that job stalls, new alerts pile up at
default scores and nobody notices for weeks. This monitor detects that and logs
a CRITICAL activity_log row (and optional Telegram push).

"Stalled" criterion — mirrors enrich_scores' own definition of "unscored", so it
cannot false-positive on legitimately-low scores (a *scored* alert always has
opportunity_score>0 AND evidence_confidence>0):

    opportunity_score = 0  AND  evidence_confidence = 0
    AND created_at older than 30 minutes

The 30-minute grace means an alert has missed ~3 enrich cycles (10-min cadence)
before we alarm — so transient timing right after an insert never triggers it.

Scheduled hourly (see api/main.py). Also records a healthy row each run so the
monitor's own liveness is visible in the activity_log.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, log_activity

SOURCE = "MONITOR_ENRICH_STALL"
GRACE_MINUTES = 30

_STALL_WHERE = (
    "COALESCE(opportunity_score,0) = 0 AND COALESCE(evidence_confidence,0) = 0 "
    f"AND datetime(created_at) < datetime('now', '-{GRACE_MINUTES} minutes')"
)


def _telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception:
        pass


def run() -> int:
    t0 = time.time()
    conn = db_connection()
    row = conn.execute(
        f"SELECT COUNT(*) AS n, MIN(created_at) AS oldest, MAX(rule) AS a_rule "
        f"FROM alerts WHERE {_STALL_WHERE}"
    ).fetchone()
    count = row["n"] or 0
    oldest = row["oldest"]

    if count > 0:
        # Sample which rules are affected for the note.
        rules = [
            f"{r['rule']}:{r['c']}"
            for r in conn.execute(
                f"SELECT rule, COUNT(*) c FROM alerts WHERE {_STALL_WHERE} "
                f"GROUP BY rule ORDER BY c DESC LIMIT 5"
            ).fetchall()
        ]
        notes = (f"CRITICAL: {count} alert(s) unscored >{GRACE_MINUTES}min — "
                 f"enrich_scores may be stalled. oldest={oldest}. by_rule: {', '.join(rules)}")
        print(f"[{SOURCE}] {notes}")
        _telegram(f"🚨 *Scope scoring stall* — {count} alerts unscored for "
                  f">{GRACE_MINUTES}min (oldest {oldest}). enrich_scores may have stopped.")
    else:
        notes = f"OK: 0 alerts unscored beyond {GRACE_MINUTES}min grace"
        print(f"[{SOURCE}] {notes}")

    log_activity(conn, SOURCE, scanned=count, flagged=count, emitted=count,
                 duration_seconds=round(time.time() - t0, 2), notes=notes)
    conn.close()
    return count


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Detect stalled alert scoring (enrich_scores).")
    p.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    p.parse_args()
    sys.exit(1 if run() > 0 else 0)
