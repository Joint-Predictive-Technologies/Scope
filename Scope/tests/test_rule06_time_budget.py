#!/usr/bin/env python3
"""RULE_06 Phase-A hardening tests: incremental scan window + per-run time budget.

Self-contained and offline — all network calls are monkeypatched. Run directly
(`python3 tests/test_rule06_time_budget.py`) or via pytest.
"""
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# App root (Scope/) on the path, and an isolated temp DB before jpt_common loads.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["DATABASE_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="rule06_test_"), "jpt.db"
)

import rule_06_form4 as r6  # noqa: E402


def _fresh_db() -> str:
    """Point DATABASE_PATH at a brand-new temp DB and return its path."""
    path = os.path.join(tempfile.mkdtemp(prefix="rule06_test_"), "jpt.db")
    os.environ["DATABASE_PATH"] = path
    return path


def _init_activity_log(db_path: str) -> None:
    """Create just the activity_log table (mirrors jpt_common's schema)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            events_scanned INTEGER DEFAULT 0,
            events_flagged INTEGER DEFAULT 0,
            alerts_emitted INTEGER DEFAULT 0,
            run_at TEXT DEFAULT (datetime('now')),
            duration_seconds REAL, notes TEXT
        )"""
    )
    conn.commit()
    conn.close()


def test_incremental_since_fallback_when_no_history():
    db = _fresh_db()
    _init_activity_log(db)  # empty
    today = date(2026, 7, 22)
    got = r6._incremental_since(today.isoformat())
    expect = (today - timedelta(days=r6.WINDOW_MIN_DAYS)).isoformat()
    assert got == expect, f"fallback: got {got}, expected {expect}"


def test_incremental_since_recent_run_clamps_to_min():
    db = _fresh_db()
    _init_activity_log(db)
    today = date(2026, 7, 22)
    # A run "today" → since would be yesterday, but must clamp to >= MIN days back.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO activity_log (source, run_at) VALUES (?, ?)",
        ("RULE_06", today.isoformat() + " 10:00:00"),
    )
    conn.commit()
    conn.close()
    got = r6._incremental_since(today.isoformat())
    expect = (today - timedelta(days=r6.WINDOW_MIN_DAYS)).isoformat()
    assert got == expect, f"clamp-min: got {got}, expected {expect}"


def test_incremental_since_old_run_clamps_to_max():
    db = _fresh_db()
    _init_activity_log(db)
    today = date(2026, 7, 22)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO activity_log (source, run_at) VALUES (?, ?)",
        ("RULE_06", "2026-01-01 10:00:00"),
    )
    conn.commit()
    conn.close()
    got = r6._incremental_since(today.isoformat())
    expect = (today - timedelta(days=r6.WINDOW_MAX_DAYS)).isoformat()
    assert got == expect, f"clamp-max: got {got}, expected {expect}"


def test_incremental_since_midrange_run_uses_watermark():
    db = _fresh_db()
    _init_activity_log(db)
    today = date(2026, 7, 22)
    last_run = today - timedelta(days=4)  # 4d ago → since = 5d ago (within 2..7)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO activity_log (source, run_at) VALUES (?, ?)",
        ("RULE_06", last_run.isoformat() + " 09:00:00"),
    )
    conn.commit()
    conn.close()
    got = r6._incremental_since(today.isoformat())
    expect = (last_run - timedelta(days=1)).isoformat()
    assert got == expect, f"midrange: got {got}, expected {expect}"


def test_run_honours_time_budget_and_records_activity(monkeypatch):
    db = _fresh_db()
    # 5 fake refs; the budget must stop the loop before any are processed.
    refs = [
        r6.FilingRef(adsh=f"0000-{i}", owner_cik="1", filename="", file_date="")
        for i in range(5)
    ]
    monkeypatch.setattr(r6, "search_form4_filings", lambda since, today: refs)
    monkeypatch.setattr(r6, "TIME_BUDGET_SECONDS", -1)  # force immediate break

    significant, emitted = r6.run("2026-07-20", "2026-07-22", emit_alerts=False)
    assert (significant, emitted) == (0, 0)

    row = sqlite3.connect(db).execute(
        "SELECT events_scanned, notes FROM activity_log WHERE source='RULE_06'"
    ).fetchone()
    assert row is not None, "expected a RULE_06 activity row even on budget break"
    scanned, notes = row
    assert scanned == 0, f"budget break should process 0, got {scanned}"
    assert "time budget hit" in (notes or ""), f"note missing budget marker: {notes!r}"


def test_run_normal_path_processes_all_and_records(monkeypatch):
    db = _fresh_db()
    refs = [
        r6.FilingRef(adsh="0000-a", owner_cik="1", filename="", file_date=""),
        r6.FilingRef(adsh="0000-b", owner_cik="2", filename="", file_date=""),
    ]
    monkeypatch.setattr(r6, "search_form4_filings", lambda since, today: refs)
    # No network: each filing "fetch" returns nothing → examined but skipped.
    monkeypatch.setattr(r6, "fetch_filing_xml", lambda ref: None)

    r6.run("2026-07-20", "2026-07-22", emit_alerts=False)

    scanned, notes = sqlite3.connect(db).execute(
        "SELECT events_scanned, notes FROM activity_log WHERE source='RULE_06'"
    ).fetchone()
    assert scanned == 2, f"expected all 2 processed, got {scanned}"
    assert "time budget hit" not in (notes or ""), notes


# --- direct-run harness (no pytest) ---------------------------------------
if __name__ == "__main__":
    class _MP:
        """Minimal monkeypatch shim so the file runs without pytest."""
        def __init__(self):
            self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val in reversed(self._undo):
                setattr(obj, name, val)

    passed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            mp = _MP()
            try:
                if "monkeypatch" in _fn.__code__.co_varnames:
                    _fn(mp)
                else:
                    _fn()
                print(f"  ok  {_name}")
                passed += 1
            finally:
                mp.undo()
    print(f"\n{passed} passed")
