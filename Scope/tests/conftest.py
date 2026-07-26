"""Test-suite isolation.

Every test in this suite runs against its own disposable SQLite file. Nothing
here changes what the tests assert — it only changes *where* they connect.

Why this exists: the suite imports the real rule scripts and calls the real
`jpt_common.db_connection()`. Before this fixture, that resolved to the live
database, so a single `pytest` run inserted and deleted rows in production data.
A measured run against a copy of the working DB moved `alerts.sqlite_sequence`
8926 -> 8939 and `themes` 28 -> 30 (rows committed, then deleted by test
teardown) and left RULE_10 x2 + RULE_CLUSTER x1 rows in `activity_log`. That is
the exact signature that was mistaken for production data loss — see
`vault/Scope/02_Sessions/SESSION-2026-07-25-rule10-convergence-trace.md`.

`jpt_common._get_db_path` refuses to fall through to the real path when it
detects a test process, so if this fixture ever stops applying the suite fails
loudly instead of silently writing to the live DB.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point every test at a fresh, disposable temp DB.

    `autouse` so no test can opt out by forgetting. Tests that set
    `DATABASE_PATH` themselves (e.g. `test_db_backup.py`) still win — their
    `monkeypatch.setenv` runs after this one and is undone independently.

    The schema is created through the normal init path (`db_connection` runs
    `_initialize_schema` plus the idempotent migrations), so tests see exactly the
    structure production has — just with no rows. Starting empty is the only
    supported mode: every test seeds the rows it asserts on, so nothing depends on
    ambient data, and the result does not vary with whatever happens to be in
    someone's working database.

    Side effects stay inside `tmp_path`: `_backup_db` writes its hourly copy to
    `<tmp_path>/backups/`, which pytest discards with the rest of the directory.
    """
    db_path = tmp_path / "jpt.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    jpt_common.db_connection().close()   # create the file + full schema
    yield db_path
