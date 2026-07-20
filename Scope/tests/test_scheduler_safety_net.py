#!/usr/bin/env python3
"""
The scheduler-level safety net: ANY scheduled-job failure — including an
import-time crash that runs before the script's own error handling — must still
produce a SCHEDULER_JOB_FAILURE activity_log row.

Runs under pytest or standalone:  python3 tests/test_scheduler_safety_net.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common  # noqa: E402
from api import main as appmain  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    jpt_common.db_connection().close()
    yield


def _failure_rows():
    conn = jpt_common.db_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT notes FROM activity_log WHERE source='SCHEDULER_JOB_FAILURE' ORDER BY id DESC"
    ).fetchall()]
    conn.close()
    return rows


def test_import_time_crash_is_logged(tmp_path):
    script = tmp_path / "broken_import_job.py"
    script.write_text(
        "import this_module_definitely_does_not_exist_xyz123  # ImportError at import time\n"
        "print('unreachable')\n"
    )
    result = appmain._run_rule(str(script))
    assert result != "ok"
    rows = _failure_rows()
    assert rows, "import-time crash must produce a SCHEDULER_JOB_FAILURE row"
    note = rows[0]["notes"]
    assert "broken_import_job.py" in note
    assert "exited with code" in note
    assert "ModuleNotFoundError" in note or "ImportError" in note


def test_nonzero_exit_is_logged(tmp_path):
    script = tmp_path / "exit_job.py"
    script.write_text("import sys\nsys.stderr.write('boom happened')\nsys.exit(3)\n")
    appmain._run_rule(str(script))
    rows = _failure_rows()
    assert rows and "exited with code 3" in rows[0]["notes"]
    assert "boom happened" in rows[0]["notes"]


def test_clean_script_writes_no_failure_row(tmp_path):
    script = tmp_path / "ok_job.py"
    script.write_text("import sys\nprint('fine')\nsys.exit(0)\n")
    result = appmain._run_rule(str(script))
    assert result == "ok"
    assert _failure_rows() == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
