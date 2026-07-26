#!/usr/bin/env python3
"""
db_backup.py — DB_BACKUP

Safe, verified daily snapshot of the SQLite database. Unlike the hourly
raw-copy in jpt_common._backup_db (which can capture a half-written file on a
live DB), this uses SQLite's **online backup API**, runs `PRAGMA
integrity_check` on the copy BEFORE keeping it, gzips it, and applies a tiered
retention policy.

STORAGE STATUS: no remote object store is configured yet (no S3/B2/R2 env vars,
no SDK). This job therefore writes a LOCAL snapshot to data/backups/ — a stopgap
only: it lives on the SAME Railway volume as the primary DB, i.e. the SAME
failure domain, so it does NOT protect against a volume incident. The remote
upload step activates automatically once credentials exist (see upload_remote()
and RESTORE.md). Until then this is better-than-nothing, NOT sufficient alone.

Scheduled daily 03:00 UTC (see api/main.py). Covered by the scheduler safety net.
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
    (status, detail). No store is configured today, so this reports 'skipped'
    and the local snapshot stands alone. When BACKUP_S3_* env vars are added
    (and boto3 to requirements), wire the upload here — the rest of the job is
    already storage-ready."""
    needed = ("BACKUP_S3_BUCKET", "BACKUP_S3_ENDPOINT", "BACKUP_S3_ACCESS_KEY_ID",
              "BACKUP_S3_SECRET_ACCESS_KEY")
    present = [k for k in needed if os.getenv(k)]
    if len(present) != len(needed):
        missing = [k for k in needed if k not in present]
        return "skipped", f"no remote storage configured (missing {', '.join(missing)})"
    try:
        import boto3  # not in requirements until storage is provisioned
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
    t0 = time.time()
    db_file = str(_get_db_path(None))
    backup_dir = _backup_dir(db_file)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    raw_path = os.path.join(backup_dir, f"{SNAPSHOT_PREFIX}{stamp}.db")
    gz_path = raw_path + ".gz"

    # 1) safe online backup
    online_backup(db_file, raw_path)
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

    # 3) compress, drop the uncompressed copy
    gzip_file(raw_path, gz_path)
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
