#!/usr/bin/env python3
"""
MONITOR_BACKUP_STALL — the watchdog over the only backup that exists.

`db_backup.py` is now the sole backup (the connect-triggered raw copy in
`jpt_common._backup_db` was retired as unsafe). The scheduler safety net catches
a backup job that *fails*; nothing caught a job that was never invoked. This
monitor closes that, and these tests pin the cases that matter — especially the
two-sided one, where the activity_log claims success and no file exists.

`evaluate()` is a pure function of the observations, so the decision logic is
tested without touching a filesystem or a clock.

Runs under pytest or standalone:  python3 tests/test_monitor_backup_stall.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                     # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "mbs", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "monitor_backup_stall.py"))
mbs = importlib.util.module_from_spec(_spec)
sys.modules["mbs"] = mbs
_spec.loader.exec_module(mbs)

OK_NOTES = "integrity=ok, size=1300KB, upload=skipped (…), local_retained=25"
FAIL_NOTES = "CRITICAL: integrity_check FAILED (malformed) — snapshot discarded"
BIG = 1_300_000


# ---------------------------------------------------------------------------
# the decision logic
# ---------------------------------------------------------------------------

def test_healthy_when_a_fresh_snapshot_and_a_fresh_log_row_agree():
    assert mbs.evaluate(0.5, BIG, "/b/snapshot_x.db.gz", 0.5, OK_NOTES) == []


def test_alarms_when_there_are_no_snapshot_files_at_all():
    problems = mbs.evaluate(None, 0, None, 0.5, OK_NOTES)
    assert any("NO SNAPSHOT FILES" in p for p in problems), problems


def test_alarms_when_the_newest_snapshot_is_stale():
    problems = mbs.evaluate(9.0, BIG, "/b/snapshot_x.db.gz", 9.0, OK_NOTES)
    assert any("9.0h old" in p for p in problems), problems


def test_does_not_alarm_inside_the_grace_window():
    """One or two missed hourly cycles must not page anybody."""
    assert mbs.evaluate(2.9, BIG, "/b/s.db.gz", 2.9, OK_NOTES) == []


def test_grace_boundary_is_three_hours():
    assert mbs.GRACE_HOURS == 3
    assert mbs.evaluate(3.0, BIG, "/b/s.db.gz", 3.0, OK_NOTES) == []
    assert mbs.evaluate(3.1, BIG, "/b/s.db.gz", 3.1, OK_NOTES) != []


def test_alarms_on_a_zero_or_tiny_snapshot():
    """A 0-byte file is not a backup, however recent."""
    problems = mbs.evaluate(0.1, 0, "/b/s.db.gz", 0.1, OK_NOTES)
    assert any("too small to be a database" in p for p in problems), problems


def test_alarms_when_the_last_backup_failed_its_integrity_check():
    problems = mbs.evaluate(0.5, BIG, "/b/s.db.gz", 0.5, FAIL_NOTES)
    assert any("FAILED its integrity check" in p for p in problems), problems


def test_alarms_when_no_backup_has_ever_run():
    problems = mbs.evaluate(None, 0, None, None, "")
    assert any("has ever been written" in p for p in problems), problems
    assert any("NO SNAPSHOT FILES" in p for p in problems), problems


def test_the_two_sided_check_catches_a_lying_log(caplog):
    """The nastiest case: the job logs success but no fresh file exists.

    A one-sided monitor reading only activity_log would report healthy here,
    which is worse than an honest error — you'd believe you had a backup.
    """
    problems = mbs.evaluate(
        snapshot_age=48.0, snapshot_size=BIG, snapshot_path="/b/old.db.gz",
        log_age=0.2, log_notes=OK_NOTES)
    assert any("log and the disk disagree" in p for p in problems), problems


def test_a_stale_log_with_a_fresh_file_is_also_reported():
    """The inverse: files appearing without the job logging is worth knowing."""
    problems = mbs.evaluate(0.2, BIG, "/b/s.db.gz", 30.0, OK_NOTES)
    assert any("log row is 30.0h old" in p for p in problems), problems


# ---------------------------------------------------------------------------
# end to end, against a real disk and a real DB
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "jpt.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    jpt_common.db_connection().close()
    backups = tmp_path / "backups"
    backups.mkdir()
    return {"db": str(db), "backups": backups}


def _log_backup(notes: str = OK_NOTES) -> None:
    conn = jpt_common.db_connection()
    jpt_common.log_activity(conn, "DB_BACKUP", scanned=1, notes=notes)
    conn.commit()
    conn.close()


def _monitor_rows() -> list:
    conn = jpt_common.db_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT flagged_col.* FROM (SELECT events_flagged AS flagged, notes "
        "FROM activity_log WHERE source='MONITOR_BACKUP_STALL' ORDER BY id) "
        "AS flagged_col")]
    conn.close()
    return rows


def test_end_to_end_healthy(env):
    snap = env["backups"] / "snapshot_20260726_1200.db.gz"
    snap.write_bytes(b"x" * BIG)
    _log_backup()

    problems = mbs.run()

    assert problems == 0
    rows = _monitor_rows()
    assert rows and rows[-1]["flagged"] == 0
    assert rows[-1]["notes"].startswith("OK:")


def test_end_to_end_alarms_and_records_when_backups_are_missing(env):
    """No snapshot files, no DB_BACKUP row — the scheduler-never-started case."""
    problems = mbs.run()

    assert problems > 0
    rows = _monitor_rows()
    assert rows, "the monitor left no activity_log row"
    assert rows[-1]["notes"].startswith("CRITICAL:")
    assert "NO SNAPSHOT FILES" in rows[-1]["notes"]
    assert rows[-1]["flagged"] == problems


def test_end_to_end_detects_a_stale_snapshot_on_disk(env):
    snap = env["backups"] / "snapshot_20260720_0300.db.gz"
    snap.write_bytes(b"x" * BIG)
    old = time.time() - 30 * 3600            # 30 hours ago
    os.utime(snap, (old, old))
    _log_backup()

    assert mbs.run() > 0
    assert "CRITICAL" in _monitor_rows()[-1]["notes"]


def test_the_monitor_records_its_own_liveness_even_when_healthy(env):
    """A silent monitor is indistinguishable from a dead one."""
    (env["backups"] / "snapshot_20260726_1200.db.gz").write_bytes(b"x" * BIG)
    _log_backup()

    mbs.run()
    mbs.run()

    assert len(_monitor_rows()) == 2, "healthy runs must still leave a row"


def test_exit_code_is_nonzero_when_stalled(env, monkeypatch):
    """So the scheduler's SCHEDULER_JOB_FAILURE net also fires."""
    import subprocess

    script = os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "monitor_backup_stall.py")
    result = subprocess.run(
        [sys.executable, script, "--emit-alerts"],
        capture_output=True, text=True,
        env={**os.environ, "DATABASE_PATH": env["db"]},
    )
    assert result.returncode == 1, result.stdout
    assert "CRITICAL" in result.stdout


def test_scheduler_argv_is_accepted():
    """The scheduler invokes every job with --emit-alerts."""
    import subprocess

    script = os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "monitor_backup_stall.py")
    result = subprocess.run([sys.executable, script, "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0


def test_monitor_is_registered_hourly():
    from api import main as appmain

    assert appmain._RULE_SCHEDULE.get("scripts/monitor_backup_stall.py") == 60


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
