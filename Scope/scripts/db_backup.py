#!/usr/bin/env python3
"""
db_backup.py — DB_BACKUP

Safe, verified hourly snapshot of the SQLite database. It uses SQLite's
**online backup API**, runs `PRAGMA integrity_check` on the copy BEFORE keeping
it, gzips it, and applies a tiered retention policy.

This replaced the raw `shutil.copy2` in jpt_common._backup_db (now retired). That
mattered more than "it was never integrity-checked": a torn raw copy is
STRUCTURALLY VALID and **passes integrity_check** while missing part of a
transaction, so verifying those copies would not have made them safe.

STORAGE STATUS: boto3 is installed, but no remote object store is configured
until the BACKUP_S3_* env vars are set. Until then this writes only a LOCAL
snapshot to data/backups/ — a stopgap: it lives on the SAME Railway volume as the
primary DB, i.e. the SAME failure domain, so it does NOT protect against a volume
incident. The upload activates automatically once the vars exist (see
upload_remote() and RESTORE.md). Local-only is better than nothing, NOT sufficient.

Scheduled HOURLY at :05 (see api/main.py). Covered by the scheduler safety net,
and watched by scripts/monitor_backup_stall.py, which alarms if no fresh snapshot
file appears — the case the safety net cannot see, since it only catches a job
that FAILS, not one that was never invoked.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import _get_db_path, record_activity

SNAPSHOT_PREFIX = "snapshot_"          # distinct from the legacy jpt_*.db copies
ORPHAN_GRACE_SECONDS = 3600            # a raw .db older than this is abandoned
HOURLY_HOURS = 24                      # keep EVERY snapshot for the last 24h
DAILY_DAYS = 30                        # then one per calendar day out to 30 days
WEEKLY_DAYS = 90                       # then one per ISO week out to 90 days
# beyond 90 days: one per calendar month


def _backup_dir(db_file: str) -> str:
    d = os.path.join(os.path.dirname(db_file), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def online_backup(src_path: str, dest_path: str) -> None:
    """Copy a live DB safely via SQLite's online backup API (never a raw file
    copy, which can catch a half-written page mid-transaction)."""
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dest_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


def integrity_ok(db_path: str) -> tuple[bool, str]:
    """Run PRAGMA integrity_check on a DB file. Returns (ok, message). A file that
    isn't a valid SQLite DB raises DatabaseError — treated as a failed check."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            result = row[0] if row else "no result"
            return (result == "ok"), result
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return False, f"not a valid database: {exc}"


def gzip_file(src_path: str, dest_path: str) -> None:
    with open(src_path, "rb") as f_in, gzip.open(dest_path, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)


def _snapshot_date(path: str) -> datetime | None:
    m = re.search(rf"{SNAPSHOT_PREFIX}(\d{{8}})_(\d{{4}})", os.path.basename(path))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def prune(backup_dir: str, now: datetime | None = None) -> int:
    """Tiered retention. Returns the number of snapshots retained.

        <= 24h   every snapshot          (hourly granularity for recent loss)
        <= 30d   one per calendar day
        <= 90d   one per ISO week
        >  90d   one per calendar month

    The hourly tier exists because the job now runs hourly. Under the previous
    policy ("keep everything <= 30 days") that would have retained ~720
    snapshots; the day tier thins them to one per day past the first 24 hours.
    Newest-first iteration means the snapshot kept for each day/week/month is the
    most recent one in that bucket.
    """
    now = now or datetime.now(timezone.utc)

    # Sweep orphaned UNCOMPRESSED snapshots first. `run()` writes
    # snapshot_*.db and then gzips it; if the process dies in between — a
    # gzip ENOSPC, or the scheduler SIGKILLing the job at its 300s timeout while
    # src.backup() is blocked on a write lock — the raw file is left behind. It
    # is a full-size copy of the DB (~5.7MB), the retention glob only ever
    # matched *.db.gz, so these accumulated forever: at hourly cadence a
    # persistent failure leaks ~138MB/day. Anything older than the grace period
    # cannot belong to a run still in flight.
    for p in glob.glob(os.path.join(backup_dir, f"{SNAPSHOT_PREFIX}*.db")):
        try:
            age_s = time.time() - os.path.getmtime(p)
        except OSError:
            continue
        if age_s > ORPHAN_GRACE_SECONDS:
            try:
                os.remove(p)
                print(f"[DB_BACKUP] swept orphaned raw snapshot {os.path.basename(p)}")
            except OSError:
                pass

    snaps = []
    for p in glob.glob(os.path.join(backup_dir, f"{SNAPSHOT_PREFIX}*.db.gz")):
        d = _snapshot_date(p)
        if d:
            snaps.append((d, p))
    snaps.sort(reverse=True)  # newest first

    keep: set[str] = set()
    seen_day: set = set()
    seen_week: set = set()
    seen_month: set = set()
    for d, p in snaps:
        age_hours = (now - d).total_seconds() / 3600.0
        age_days = (now - d).days
        if age_hours <= HOURLY_HOURS:
            keep.add(p)
        elif age_days <= DAILY_DAYS:
            key = (d.year, d.month, d.day)
            if key not in seen_day:
                seen_day.add(key)
                keep.add(p)
        elif age_days <= WEEKLY_DAYS:
            key = d.isocalendar()[:2]  # (ISO year, ISO week)
            if key not in seen_week:
                seen_week.add(key)
                keep.add(p)
        else:
            key = (d.year, d.month)
            if key not in seen_month:
                seen_month.add(key)
                keep.add(p)

    for _d, p in snaps:
        if p not in keep:
            try:
                os.remove(p)
            except OSError:
                pass
    return len(keep)


def upload_remote(gz_path: str) -> tuple[str, str]:
    """Upload to an S3-compatible store IF credentials are configured. Returns
    (status, detail). With no (or partial) credentials this reports 'skipped',
    names the missing variables, and the local snapshot stands alone — an absent
    offsite target must never fail the backup. boto3 is already in requirements.txt;
    adding the BACKUP_S3_* env vars is the only remaining step."""
    needed = ("BACKUP_S3_BUCKET", "BACKUP_S3_ENDPOINT", "BACKUP_S3_ACCESS_KEY_ID",
              "BACKUP_S3_SECRET_ACCESS_KEY")
    present = [k for k in needed if os.getenv(k)]
    if len(present) != len(needed):
        missing = [k for k in needed if k not in present]
        return "skipped", f"no remote storage configured (missing {', '.join(missing)})"
    try:
        import boto3  # in requirements.txt; dormant until the env vars are set
        client = boto3.client(
            "s3",
            endpoint_url=os.environ["BACKUP_S3_ENDPOINT"],
            aws_access_key_id=os.environ["BACKUP_S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["BACKUP_S3_SECRET_ACCESS_KEY"],
        )
        key = f"scope-db-backups/{os.path.basename(gz_path)}"
        client.upload_file(gz_path, os.environ["BACKUP_S3_BUCKET"], key)
        return "uploaded", key
    except Exception as exc:
        return "failed", f"{type(exc).__name__}: {exc}"


def run() -> dict:
    """Take one verified snapshot. Never raises — every failure is recorded.

    Previously only ONE failure mode (a snapshot failing integrity_check) wrote a
    DB_BACKUP row; a corrupt source DB, an unwritable backups/ directory, or a
    gzip ENOSPC all propagated out of here. Those were caught by the scheduler's
    SCHEDULER_JOB_FAILURE net, so they were never silent — but they left no
    DB_BACKUP row, which is the row an operator (and MONITOR_BACKUP_STALL) looks
    at to answer "is the backup healthy?". Now every path lands there, and the
    raw snapshot is cleaned up on the way out rather than orphaned on disk.
    """
    t0 = time.time()
    raw_path = None
    try:
        return _run_inner(t0)
    except Exception as exc:
        notes = (f"CRITICAL: backup FAILED — {type(exc).__name__}: {exc}. "
                 f"No snapshot written; existing backups untouched.")
        try:
            record_activity("DB_BACKUP", scanned=0, flagged=1, emitted=0,
                            duration_seconds=round(time.time() - t0, 2), notes=notes)
        except Exception:
            pass
        print(f"[DB_BACKUP] {notes}", file=sys.stderr)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _run_inner(t0: float) -> dict:
    db_file = str(_get_db_path(None))
    backup_dir = _backup_dir(db_file)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    raw_path = os.path.join(backup_dir, f"{SNAPSHOT_PREFIX}{stamp}.db")
    gz_path = raw_path + ".gz"

    # 1) safe online backup
    try:
        online_backup(db_file, raw_path)
    except Exception:
        # don't leave a partial raw snapshot behind for prune() to sweep later
        try:
            os.remove(raw_path)
        except OSError:
            pass
        raise
    # 2) verify BEFORE keeping — a corrupt copy must never displace good backups
    ok, integrity = integrity_ok(raw_path)
    if not ok:
        try:
            os.remove(raw_path)
        except OSError:
            pass
        notes = (f"CRITICAL: integrity_check FAILED ({integrity}) — snapshot discarded, "
                 f"retention NOT pruned so last-known-good backups are preserved")
        record_activity("DB_BACKUP", scanned=0, flagged=1, emitted=0,
                        duration_seconds=round(time.time() - t0, 2), notes=notes)
        print(f"[DB_BACKUP] {notes}")
        return {"ok": False, "integrity": integrity}

    # 3) compress, drop the uncompressed copy. On failure the raw snapshot must
    #    go too — otherwise a repeating gzip error (ENOSPC is the obvious one)
    #    leaks a full-size DB copy per run.
    try:
        gzip_file(raw_path, gz_path)
    except Exception:
        for leftover in (raw_path, gz_path):
            try:
                os.remove(leftover)
            except OSError:
                pass
        raise
    try:
        os.remove(raw_path)
    except OSError:
        pass
    size_kb = os.path.getsize(gz_path) // 1024

    # 4) remote upload (skipped until storage configured), then 5) prune
    upload_status, upload_detail = upload_remote(gz_path)
    retained = prune(backup_dir)

    notes = (f"integrity=ok, size={size_kb}KB, upload={upload_status} ({upload_detail}), "
             f"local_retained={retained}")
    record_activity("DB_BACKUP", scanned=1, flagged=0, emitted=1,
                    duration_seconds=round(time.time() - t0, 2), notes=notes)
    print(f"[DB_BACKUP] {notes}")
    return {"ok": True, "size_kb": size_kb, "upload": upload_status, "retained": retained,
            "path": gz_path}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Verified compressed DB backup (local + optional remote).")
    p.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    p.parse_args()
    result = run()
    sys.exit(0 if result.get("ok") else 1)
