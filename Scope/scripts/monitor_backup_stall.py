#!/usr/bin/env python3
"""
MONITOR_BACKUP_STALL — make a silent backup stall loud.

`scripts/db_backup.py` runs hourly and is the only backup there is: the old
connect-triggered raw copy in `jpt_common._backup_db` was retired (it could
capture a torn write and ran unguarded in the hot path). That leaves one gap —
if the job simply never runs, nothing complains. The scheduler safety net catches
a job that *fails*; it cannot catch a job that was never invoked, e.g. because
APScheduler didn't start (see the fallback at api/main.py, "apscheduler not
installed — falling back to single loop"). `/status` shows the last DB_BACKUP run,
but only if somebody looks.

So this monitor checks the thing that actually matters — **is there a recent,
non-empty snapshot file on disk?** — and alarms if not.

It deliberately checks BOTH sources of truth:

  1. the newest `snapshot_*.db.gz` on disk (ground truth: this is what a restore
     reads), and
  2. the newest DB_BACKUP `activity_log` row (what the job *claims*).

Disagreement between them is itself reported. A log row saying "ok" with no file
behind it is a worse failure than an honest error, and only a two-sided check
finds it.

"Stalled" = the newest snapshot is older than GRACE_HOURS, or missing, or
zero-length. The grace is 3 hours against an hourly cadence — three missed cycles
— so a single skipped or slow run never alarms.

Scheduled hourly (see api/main.py). Records a healthy row each run too, so the
monitor's own liveness is visible in the activity_log.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import _get_db_path, db_connection, log_activity

SOURCE = "MONITOR_BACKUP_STALL"
GRACE_HOURS = 3                  # 3 missed hourly cycles before alarming
SNAPSHOT_GLOB = "snapshot_*.db.gz"
MIN_SNAPSHOT_BYTES = 1024        # a snapshot smaller than this is not a database


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


def newest_snapshot(backup_dir: str) -> tuple[str | None, float | None, int]:
    """(path, age_hours, size_bytes) of the most recent snapshot, by mtime."""
    paths = glob.glob(os.path.join(backup_dir, SNAPSHOT_GLOB))
    if not paths:
        return None, None, 0
    newest = max(paths, key=os.path.getmtime)
    age_hours = (time.time() - os.path.getmtime(newest)) / 3600.0
    return newest, age_hours, os.path.getsize(newest)


def last_backup_row(conn) -> tuple[float | None, str]:
    """(age_hours, notes) of the most recent DB_BACKUP activity_log row."""
    row = conn.execute(
        "SELECT run_at, notes FROM activity_log WHERE source='DB_BACKUP' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or not row["run_at"]:
        return None, ""
    try:
        ran = datetime.fromisoformat(row["run_at"]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None, row["notes"] or ""
    age = (datetime.now(timezone.utc) - ran).total_seconds() / 3600.0
    return age, (row["notes"] or "")


def evaluate(snapshot_age, snapshot_size, snapshot_path, log_age, log_notes) -> list[str]:
    """Return a list of problems. Empty list means healthy.

    Pure function of the observations so the decision logic is testable without
    a filesystem or a database.
    """
    problems: list[str] = []

    if snapshot_path is None:
        problems.append("NO SNAPSHOT FILES AT ALL — nothing to restore from")
    else:
        if snapshot_age is not None and snapshot_age > GRACE_HOURS:
            problems.append(
                f"newest snapshot is {snapshot_age:.1f}h old (grace {GRACE_HOURS}h) — "
                f"the hourly backup job is not producing files")
        if snapshot_size < MIN_SNAPSHOT_BYTES:
            problems.append(
                f"newest snapshot is {snapshot_size}B — too small to be a database")

    if log_age is None:
        problems.append("no DB_BACKUP activity_log row has ever been written")
    elif log_age > GRACE_HOURS:
        problems.append(f"last DB_BACKUP log row is {log_age:.1f}h old")

    if "integrity_check FAILED" in log_notes:
        problems.append("the last backup FAILED its integrity check — snapshot discarded")

    # Two-sided disagreement: the job claims success but no fresh file exists.
    claims_ok = log_age is not None and log_age <= GRACE_HOURS and "integrity=ok" in log_notes
    file_fresh = snapshot_age is not None and snapshot_age <= GRACE_HOURS
    if claims_ok and not file_fresh:
        problems.append(
            "DB_BACKUP logged a successful run but there is no fresh snapshot file — "
            "the log and the disk disagree")

    return problems


def run() -> int:
    t0 = time.time()
    conn = db_connection()

    db_file = str(_get_db_path(None))
    backup_dir = os.path.join(os.path.dirname(db_file), "backups")

    path, age_hours, size = newest_snapshot(backup_dir)
    log_age, log_notes = last_backup_row(conn)
    problems = evaluate(age_hours, size, path, log_age, log_notes)

    if problems:
        notes = "CRITICAL: " + "; ".join(problems)
        print(f"[{SOURCE}] {notes}")
        _telegram(
            "🚨 *Scope backup stall* — " + problems[0] +
            f"\nbackup dir: `{backup_dir}`"
            "\nThe outcome dataset is not being protected.")
    else:
        newest = os.path.basename(path) if path else "—"
        notes = (f"OK: newest snapshot {newest} is {age_hours:.1f}h old "
                 f"({size // 1024}KB), last DB_BACKUP row {log_age:.1f}h old")
        print(f"[{SOURCE}] {notes}")

    log_activity(conn, SOURCE, scanned=1, flagged=len(problems),
                 emitted=len(problems),
                 duration_seconds=round(time.time() - t0, 2), notes=notes)
    conn.close()
    return len(problems)


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Detect a stalled or missing DB backup (db_backup.py).")
    p.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    p.parse_args()
    sys.exit(1 if run() > 0 else 0)
