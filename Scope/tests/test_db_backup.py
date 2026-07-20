#!/usr/bin/env python3
"""
Tests for the verified DB backup job (scripts/db_backup.py).

Runs under pytest or standalone:  python3 tests/test_db_backup.py
"""
import glob
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "db_backup", os.path.join(os.path.dirname(__file__), "..", "scripts", "db_backup.py"))
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jpt.db"))
    jpt_common.db_connection().close()  # create a real DB with schema
    yield tmp_path


def test_integrity_check_catches_corruption(tmp_path):
    good = tmp_path / "good.db"
    bk.online_backup(str(jpt_common._get_db_path(None)), str(good))
    ok, msg = bk.integrity_ok(str(good))
    assert ok and msg == "ok"

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"SQLite format 3\x00" + b"\xde\xad\xbe\xef" * 400)  # garbage body
    bad_ok, bad_msg = bk.integrity_ok(str(corrupt))
    assert bad_ok is False
    assert bad_msg != "ok"


def test_run_produces_gz_and_logs(temp_db):
    result = bk.run()
    assert result["ok"] is True
    gz = glob.glob(os.path.join(str(temp_db), "backups", "snapshot_*.db.gz"))
    assert len(gz) == 1
    conn = jpt_common.db_connection()
    row = conn.execute(
        "SELECT notes FROM activity_log WHERE source='DB_BACKUP' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row and "integrity=ok" in row["notes"]
    # no remote store configured in tests -> upload skipped, page still succeeds
    assert "upload=skipped" in row["notes"]


def test_corrupt_backup_is_discarded_and_not_pruned(temp_db, monkeypatch):
    # Seed one good existing backup that must survive a subsequent failed run.
    bk.run()
    before = set(glob.glob(os.path.join(str(temp_db), "backups", "snapshot_*.db.gz")))
    assert len(before) == 1

    # Force the next snapshot to fail integrity — it must be discarded, existing kept.
    monkeypatch.setattr(bk, "integrity_ok", lambda p: (False, "malformed database"))
    result = bk.run()
    assert result["ok"] is False
    after = set(glob.glob(os.path.join(str(temp_db), "backups", "snapshot_*.db.gz")))
    assert after == before  # last-known-good preserved, no bad .gz added
    conn = jpt_common.db_connection()
    note = conn.execute(
        "SELECT notes FROM activity_log WHERE source='DB_BACKUP' ORDER BY id DESC LIMIT 1"
    ).fetchone()["notes"]
    conn.close()
    assert "CRITICAL" in note and "integrity_check FAILED" in note


def test_retention_thins_daily_weekly_monthly(temp_db):
    backup_dir = os.path.join(str(temp_db), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    now = datetime.now(timezone.utc)

    def _mk(days_ago, hhmm):
        d = now - timedelta(days=days_ago)
        p = os.path.join(backup_dir, f"snapshot_{d.strftime('%Y%m%d')}_{hhmm}.db.gz")
        open(p, "wb").close()
        return os.path.basename(p)

    # <=30d — every daily kept (3)
    _mk(0, "0300"); _mk(10, "0300"); _mk(29, "0300")
    # 30-90d — two snapshots on the SAME day (same ISO week) -> keep exactly 1
    wk_new = _mk(40, "1500"); wk_old = _mk(40, "0300")
    # >90d — two snapshots in the SAME month -> keep exactly 1
    mo_new = _mk(120, "1500"); mo_old = _mk(122, "0300")

    retained = bk.prune(backup_dir, now=now)
    kept = {os.path.basename(p) for p in glob.glob(os.path.join(backup_dir, "snapshot_*.db.gz"))}

    assert retained == len(kept)
    assert retained == 5                      # 3 daily + 1 weekly + 1 monthly
    # weekly + monthly pairs each collapsed to the NEWER of the two
    assert wk_new in kept and wk_old not in kept
    assert mo_new in kept and mo_old not in kept


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
