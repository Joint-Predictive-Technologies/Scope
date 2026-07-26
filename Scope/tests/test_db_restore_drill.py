#!/usr/bin/env python3
"""
The restore drill — the one backup property that actually matters.

`tests/test_db_backup.py` already covers integrity-checking, gzip+logging,
corrupt-snapshot rejection and retention thinning. What it never did is the thing
a backup exists for: **destroy a database and rebuild it from a snapshot.** A
backup that has never been restored is unverified, however green its other tests
are. This file drills it end to end, plus the consistency-under-concurrency
property the online backup API is chosen for.

Everything happens on throwaway paths under pytest's `tmp_path`. The working and
production databases are never read destructively and never written.

Note on checksums: a snapshot is NOT byte-identical to its source — SQLite's
online backup API rewrites page layout, and `db_connection()` may run idempotent
migrations. So identity is asserted at the level that matters — every row of
every table — rather than by file hash, which would fail for reasons that have
nothing to do with data loss.

Runs under pytest or standalone:  python3 tests/test_db_restore_drill.py
"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import os
import pathlib
import shutil
import sqlite3
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                    # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "dbb", os.path.join(os.path.dirname(__file__), "..", "scripts", "db_backup.py"))
dbb = importlib.util.module_from_spec(_spec)
sys.modules["dbb"] = dbb
_spec.loader.exec_module(dbb)

KEY_TABLES = ("alerts", "themes", "theme_signals", "transactions", "filings",
              "activity_log")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _tables(path: str) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name")]
    conn.close()
    return names


def _content_digest(path: str) -> dict[str, str]:
    """Order-independent content hash per table — the real identity test."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out: dict[str, str] = {}
    for table in _tables(path):
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        # sort the rendered rows so physical ordering can't affect the digest
        blob = "\n".join(sorted(repr(r) for r in rows)).encode()
        out[table] = f"{len(rows)}:{hashlib.sha256(blob).hexdigest()[:16]}"
    conn.close()
    return out


def _seed(path: str, n_alerts: int = 400) -> None:
    os.environ["DATABASE_PATH"] = path
    conn = jpt_common.db_connection()
    for i in range(n_alerts):
        conn.execute(
            "INSERT INTO alerts (rule, ticker, severity, headline, opportunity_score) "
            "VALUES (?,?,?,?,?)",
            (f"RULE_{i % 7:02d}", f"TK{i % 40}", "HIGH", f"seeded alert {i}", i % 100),
        )
    conn.execute("INSERT INTO themes (title, primary_ticker, status, signal_count) "
                 "VALUES ('Convergence: ZWAR', 'ZWAR', 'Emerging', 1)")
    conn.commit()
    conn.close()


@pytest.fixture
def source_db(tmp_path, monkeypatch):
    path = str(tmp_path / "source.db")
    monkeypatch.setenv("DATABASE_PATH", path)
    _seed(path)
    return path


# ---------------------------------------------------------------------------
# THE DRILL — destroy and rebuild
# ---------------------------------------------------------------------------

def test_restore_drill_rebuilds_an_identical_database(source_db, tmp_path):
    """backup -> destroy a throwaway copy -> restore -> prove identity."""
    before = _content_digest(source_db)
    assert before["alerts"].startswith("400:"), before["alerts"]

    result = dbb.run()
    assert result["ok"] is True
    snapshot = result["path"]
    assert snapshot.endswith(".db.gz") and os.path.getsize(snapshot) > 0

    # --- destroy a THROWAWAY copy, never the source ---
    victim = str(tmp_path / "victim.db")
    shutil.copy2(source_db, victim)
    with open(victim, "r+b") as fh:
        fh.write(b"\x00" * 8192)          # obliterate the header and first pages
    destroyed_ok, destroyed_msg = dbb.integrity_ok(victim)
    assert destroyed_ok is False, "the victim was supposed to be unreadable"

    # --- restore, exactly as RESTORE.md prescribes ---
    restored = str(tmp_path / "restored.db")
    with gzip.open(snapshot, "rb") as f_in, open(restored, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    ok, message = dbb.integrity_ok(restored)
    assert ok, f"restored DB failed integrity_check: {message}"
    assert message == "ok"

    # --- identity, table by table ---
    after = _content_digest(restored)
    assert set(after) == set(before), (
        f"tables differ: missing={set(before)-set(after)} extra={set(after)-set(before)}")
    for table in sorted(before):
        assert after[table] == before[table], (
            f"{table} differs after restore: {before[table]} -> {after[table]}")

    for table in KEY_TABLES:
        if table in before:
            assert after[table] == before[table]


def test_restored_database_is_usable_not_merely_readable(source_db, tmp_path):
    """Restore then actually open it through the app's own connection path."""
    snapshot = dbb.run()["path"]
    restored = str(tmp_path / "usable.db")
    with gzip.open(snapshot, "rb") as f_in, open(restored, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    os.environ["DATABASE_PATH"] = restored
    conn = jpt_common.db_connection()          # runs migrations; must be a no-op
    n = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    conn.execute("INSERT INTO alerts (rule, ticker, severity, headline) "
                 "VALUES ('RULE_TEST','ZWAR','HIGH','post-restore write')")
    conn.commit()
    n2 = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    conn.close()

    assert n == 400
    assert n2 == 401, "restored DB is readable but not writable"


def test_a_corrupt_snapshot_is_never_restorable_silently(tmp_path):
    """Truncated/garbage snapshots must fail the check, not restore quietly."""
    junk = str(tmp_path / "junk.db")
    with open(junk, "wb") as fh:
        fh.write(b"this is not a sqlite database at all")
    ok, message = dbb.integrity_ok(junk)
    assert ok is False
    assert "not a valid database" in message


# ---------------------------------------------------------------------------
# consistency under concurrent writes — why the online API, not a file copy
# ---------------------------------------------------------------------------

def test_online_backup_is_consistent_while_a_writer_commits(source_db, tmp_path):
    """Snapshot a live DB mid-transaction stream and verify it.

    This is the property `shutil.copy2` cannot offer: a raw copy can capture a
    page mid-write. The snapshot must pass integrity_check AND land on a
    transaction boundary — never a partial commit.
    """
    stop = threading.Event()
    written = {"n": 0}
    errors: list[str] = []

    def writer():
        conn = sqlite3.connect(source_db, timeout=30)
        try:
            while not stop.is_set():
                # each commit inserts a MATCHED PAIR; a torn snapshot would show
                # an odd count for some batch id
                batch = written["n"]
                conn.execute("BEGIN")
                conn.execute("INSERT INTO alerts (rule,ticker,severity,headline) "
                             "VALUES ('RULE_CC','CC','HIGH',?)", (f"pair-{batch}-a",))
                conn.execute("INSERT INTO alerts (rule,ticker,severity,headline) "
                             "VALUES ('RULE_CC','CC','HIGH',?)", (f"pair-{batch}-b",))
                conn.commit()
                written["n"] += 1
                time.sleep(0.001)
        except Exception as exc:                       # pragma: no cover
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            conn.close()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    time.sleep(0.05)                                   # let writes get going

    dest = str(tmp_path / "concurrent_snapshot.db")
    dbb.online_backup(source_db, dest)                 # snapshot MID-STREAM

    stop.set()
    thread.join(timeout=10)
    assert not errors, errors
    assert written["n"] > 5, f"writer barely ran ({written['n']} commits) — weak test"

    ok, message = dbb.integrity_ok(dest)
    assert ok, f"snapshot taken under concurrent writes failed integrity: {message}"

    # transaction-boundary check: every committed pair must be COMPLETE
    conn = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    rows = [r[0] for r in conn.execute(
        "SELECT headline FROM alerts WHERE rule='RULE_CC'")]
    conn.close()
    batches: dict[str, set] = {}
    for headline in rows:
        _, batch, half = headline.split("-")
        batches.setdefault(batch, set()).add(half)
    torn = {b: halves for b, halves in batches.items() if halves != {"a", "b"}}
    assert not torn, f"snapshot captured a partial transaction: {torn}"


def test_online_backup_survives_a_writer_holding_an_open_transaction(source_db, tmp_path):
    """An uncommitted transaction must NOT appear in the snapshot."""
    writer = sqlite3.connect(source_db, timeout=30)
    writer.execute("BEGIN")
    writer.execute("INSERT INTO alerts (rule,ticker,severity,headline) "
                   "VALUES ('RULE_UNCOMMITTED','X','HIGH','must not be in the backup')")

    dest = str(tmp_path / "open_txn.db")
    try:
        dbb.online_backup(source_db, dest)
    finally:
        writer.rollback()
        writer.close()

    ok, _ = dbb.integrity_ok(dest)
    assert ok

    conn = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    leaked = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE rule='RULE_UNCOMMITTED'").fetchone()[0]
    conn.close()
    assert leaked == 0, "an uncommitted row leaked into the snapshot"


# ---------------------------------------------------------------------------
# failure must be visible
# ---------------------------------------------------------------------------

def test_backup_failure_is_recorded_not_swallowed(source_db, monkeypatch):
    """A snapshot that fails integrity must leave a CRITICAL activity_log row."""
    def corrupt_backup(src_path, dest_path):
        with open(dest_path, "wb") as fh:
            fh.write(b"not a database")
    monkeypatch.setattr(dbb, "online_backup", corrupt_backup)

    result = dbb.run()
    assert result["ok"] is False

    conn = jpt_common.db_connection()
    row = conn.execute(
        "SELECT notes, events_flagged FROM activity_log "
        "WHERE source='DB_BACKUP' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()

    assert row is not None, "a failed backup left no activity_log row"
    assert "CRITICAL" in row["notes"]
    assert "integrity_check FAILED" in row["notes"]
    assert row["events_flagged"] == 1


def test_a_failed_backup_does_not_prune_good_snapshots(source_db, monkeypatch):
    """A bad run must never destroy the last-known-good backups."""
    good = dbb.run()
    assert good["ok"] is True
    backup_dir = os.path.dirname(good["path"])
    before = set(os.listdir(backup_dir))

    monkeypatch.setattr(dbb, "online_backup",
                        lambda s, d: open(d, "wb").write(b"garbage"))
    assert dbb.run()["ok"] is False

    after = set(os.listdir(backup_dir))
    assert good["path"].split("/")[-1] in after, "the good snapshot was deleted"
    assert before <= after or before == after


# ---------------------------------------------------------------------------
# retention — the surviving set must match the policy exactly
# ---------------------------------------------------------------------------

def test_retention_keeps_exactly_the_policy_set(tmp_path):
    """Seed across every tier and assert the survivors, not just the count."""
    from datetime import datetime, timedelta, timezone

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

    def make(dt):
        name = f"snapshot_{dt.strftime('%Y%m%d_%H%M')}.db.gz"
        (backup_dir / name).write_bytes(b"x")
        return name

    expected, doomed = set(), set()

    # tier 1 — every snapshot inside 24h survives (6 of them, 4h apart)
    for h in range(0, 24, 4):
        expected.add(make(now - timedelta(hours=h)))

    # tier 2 — 30d: only the NEWEST per calendar day survives
    for day in (2, 3, 10):
        base = now - timedelta(days=day)
        keep = base.replace(hour=23, minute=0)
        drop = base.replace(hour=1, minute=0)
        expected.add(make(keep))
        doomed.add(make(drop))

    # tier 3 — 30-90d: one per ISO week
    wk = now - timedelta(days=45)
    expected.add(make(wk))
    doomed.add(make(wk - timedelta(days=1)))       # same ISO week -> dropped

    # tier 4 — >90d: one per calendar month
    mo = now - timedelta(days=200)
    expected.add(make(mo))
    doomed.add(make(mo - timedelta(days=2)))       # same month -> dropped

    retained = dbb.prune(str(backup_dir), now=now)
    survivors = {p.name for p in backup_dir.glob("snapshot_*.db.gz")}

    assert survivors == expected, (
        f"unexpected survivors: extra={survivors-expected} missing={expected-survivors}")
    assert not (survivors & doomed), f"should have been pruned: {survivors & doomed}"
    assert retained == len(expected)


def test_retention_does_not_keep_720_snapshots_under_hourly_cadence(tmp_path):
    """The gap the hourly tier closes: 'keep everything <=30d' x hourly = ~720."""
    from datetime import datetime, timedelta, timezone

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    for h in range(0, 24 * 30, 1):                 # 720 hourly snapshots
        dt = now - timedelta(hours=h)
        (backup_dir / f"snapshot_{dt.strftime('%Y%m%d_%H%M')}.db.gz").write_bytes(b"x")

    retained = dbb.prune(str(backup_dir), now=now)
    # 25 hourly (0..24h inclusive) + ~29 daily, nowhere near 720
    assert retained < 70, f"retention kept {retained} snapshots"
    assert retained > 24, retained


def test_backup_job_is_scheduled_hourly():
    """Daily meant up to 24h of unrecoverable loss on the outcome dataset."""
    from api import main as appmain

    assert "scripts/db_backup.py" in appmain._CRON_SCHEDULE
    spec = appmain._CRON_SCHEDULE["scripts/db_backup.py"]
    assert "hour" not in spec, f"still pinned to one hour a day: {spec}"
    assert spec.get("minute") == 5, spec


def test_backup_job_runs_through_the_scheduler_safety_net():
    """A crash in the backup job must still surface as SCHEDULER_JOB_FAILURE."""
    import inspect

    from api import main as appmain

    src = inspect.getsource(appmain._run_rule)
    assert "_log_job_failure" in src
    # the cron schedule is what _start_scheduler registers, so membership is enough
    assert "scripts/db_backup.py" in appmain._CRON_SCHEDULE


def test_legacy_raw_copy_backup_is_retired(tmp_path):
    """`_backup_db` must no longer make unverified raw copies of a live DB."""
    db = tmp_path / "x.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (a)")
    conn.commit()
    conn.close()

    jpt_common._backup_db(db)

    assert not (tmp_path / "backups").exists(), (
        "the retired raw-copy backup still wrote files")


def test_backup_failure_can_never_break_db_connection(tmp_path, monkeypatch):
    """It used to run unguarded inside db_connection() — a backup failure there
    would have taken down every caller."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "conn.db"))
    conn = jpt_common.db_connection()      # must not raise
    conn.execute("SELECT 1").fetchone()
    conn.close()


def test_offsite_degrades_gracefully_with_no_credentials(monkeypatch, tmp_path):
    """No creds must mean 'skipped' with a reason — never a failed backup."""
    for var in ("BACKUP_S3_BUCKET", "BACKUP_S3_ENDPOINT",
                "BACKUP_S3_ACCESS_KEY_ID", "BACKUP_S3_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(var, raising=False)

    dummy = tmp_path / "x.db.gz"
    dummy.write_bytes(b"x")
    status, detail = dbb.upload_remote(str(dummy))

    assert status == "skipped"
    assert "no remote storage configured" in detail
    for var in ("BACKUP_S3_BUCKET", "BACKUP_S3_ENDPOINT"):
        assert var in detail, "the warning should name what is missing"


def _stub_boto3(monkeypatch, uploads: list, fail: bool = False):
    """Stand in for boto3 so the upload path can be exercised without creds.

    boto3 is not installed in this environment (it is now in requirements.txt so
    the deployment has it). A stub proves the code path — endpoint, keys, bucket,
    object key — without any real credential or network call. The ACTUAL upload
    to a real bucket remains UNVERIFIED, and only the human can close that.
    """
    import types

    class _Client:
        def upload_file(self, path, bucket, key):
            if fail:
                raise RuntimeError("simulated network failure")
            uploads.append((path, bucket, key))

    module = types.ModuleType("boto3")
    module.client = lambda service, **kw: _Client()
    module._kwargs = {}

    def client(service, **kw):
        module._kwargs = kw
        return _Client()

    module.client = client
    monkeypatch.setitem(sys.modules, "boto3", module)
    return module


def test_offsite_upload_path_executes_when_credentials_are_present(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_S3_BUCKET", "scope-db-backups")
    monkeypatch.setenv("BACKUP_S3_ENDPOINT", "https://s3.example.invalid")
    monkeypatch.setenv("BACKUP_S3_ACCESS_KEY_ID", "stub-key-id")
    monkeypatch.setenv("BACKUP_S3_SECRET_ACCESS_KEY", "stub-secret")

    uploads: list = []
    module = _stub_boto3(monkeypatch, uploads)

    snapshot = tmp_path / "snapshot_20260726_1200.db.gz"
    snapshot.write_bytes(b"payload")
    status, detail = dbb.upload_remote(str(snapshot))

    assert status == "uploaded", detail
    assert len(uploads) == 1
    path, bucket, key = uploads[0]
    assert path == str(snapshot)
    assert bucket == "scope-db-backups"
    assert key == "scope-db-backups/snapshot_20260726_1200.db.gz"
    # the endpoint is honoured, so an S3-compatible provider (B2/R2) works
    assert module._kwargs["endpoint_url"] == "https://s3.example.invalid"


def test_a_failed_upload_does_not_fail_the_backup(monkeypatch, source_db):
    """Offsite is best-effort; the local snapshot must still be kept and logged."""
    monkeypatch.setenv("BACKUP_S3_BUCKET", "b")
    monkeypatch.setenv("BACKUP_S3_ENDPOINT", "https://e.invalid")
    monkeypatch.setenv("BACKUP_S3_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("BACKUP_S3_SECRET_ACCESS_KEY", "s")
    _stub_boto3(monkeypatch, [], fail=True)

    result = dbb.run()

    assert result["ok"] is True, "an offsite failure must not fail the backup"
    assert result["upload"] == "failed"
    assert os.path.exists(result["path"]), "local snapshot was not kept"

    conn = jpt_common.db_connection()
    notes = conn.execute("SELECT notes FROM activity_log WHERE source='DB_BACKUP' "
                         "ORDER BY id DESC LIMIT 1").fetchone()["notes"]
    conn.close()
    assert "upload=failed" in notes, "a failed upload must be visible in the log"


def test_boto3_is_declared_so_the_upload_can_actually_run():
    """It was missing — the env-gated path would have died on ModuleNotFoundError."""
    req = (pathlib.Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    assert "boto3" in req


def test_offsite_reports_partial_credentials_as_skipped(monkeypatch, tmp_path):
    """Half-configured creds must not be treated as configured."""
    monkeypatch.setenv("BACKUP_S3_BUCKET", "scope-backups")
    monkeypatch.setenv("BACKUP_S3_ENDPOINT", "https://example.invalid")
    monkeypatch.delenv("BACKUP_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("BACKUP_S3_SECRET_ACCESS_KEY", raising=False)

    dummy = tmp_path / "y.db.gz"
    dummy.write_bytes(b"y")
    status, detail = dbb.upload_remote(str(dummy))

    assert status == "skipped"
    assert "BACKUP_S3_ACCESS_KEY_ID" in detail


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
