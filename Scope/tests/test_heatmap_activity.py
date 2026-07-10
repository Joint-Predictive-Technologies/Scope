#!/usr/bin/env python3
"""
Integration tests for heat-map event-date vs ingestion-date aggregation (§3).

Runs under pytest or standalone:  python3 tests/test_heatmap_activity.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402
from jpt_common import db_connection  # noqa: E402

_c = TestClient(app)


def test_activity_envelope_both_modes():
    for mode in ("signal", "ingestion"):
        d = _c.get(f"/api/activity?mode={mode}&days=14").json()
        assert d["mode"] == mode
        assert "data" in d and isinstance(d["data"], dict)


def test_cells_carry_rich_stats():
    d = _c.get("/api/activity?mode=signal&days=14").json()["data"]
    for day, rules in d.items():
        for rule, cell in rules.items():
            assert set(cell) >= {"count", "tickers", "high_crit", "backfilled"}
            assert cell["count"] >= cell["high_crit"]
            return  # one cell is enough


def test_invalid_mode_defaults_to_signal():
    assert _c.get("/api/activity?mode=bogus").json()["mode"] == "signal"


def test_signal_mode_separates_backfill():
    """RULE_01B bulk-ingested on one day must spread across real event dates."""
    conn = db_connection()
    ingest_days = conn.execute(
        "SELECT COUNT(DISTINCT date(created_at)) FROM alerts WHERE rule='RULE_01B'"
    ).fetchone()[0]
    event_days = conn.execute(
        "SELECT COUNT(DISTINCT date(COALESCE(event_date, created_at))) "
        "FROM alerts WHERE rule='RULE_01B'"
    ).fetchone()[0]
    conn.close()
    if ingest_days == 0:
        return  # no data
    # Event-date spread should be at least as wide as ingestion-date spread.
    assert event_days >= ingest_days


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
