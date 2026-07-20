#!/usr/bin/env python3
"""
Tests for alert annotations (thumbs up/down) — the source-quality training signal.

Runs under pytest or standalone:  python3 tests/test_annotations.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402
from jpt_common import db_connection, annotation_counts  # noqa: E402

_c = TestClient(app)

# High IDs that won't collide with real alerts.
A1, A2, A3 = 990000001, 990000002, 990000003


def _cleanup():
    conn = db_connection()
    conn.execute("DELETE FROM alert_annotations WHERE alert_id IN (?,?,?)", (A1, A2, A3))
    conn.commit()
    conn.close()


def _db_rows(alert_id):
    conn = db_connection()
    rows = conn.execute(
        "SELECT annotation FROM alert_annotations WHERE alert_id = ? AND user_id IS NULL",
        (alert_id,),
    ).fetchall()
    conn.close()
    return [r["annotation"] for r in rows]


def test_upsert_replaces_not_appends():
    _cleanup()
    # up, then down on the same alert -> ONE row, value 'down' (replace, not append).
    assert _c.post("/api/annotations", json={"alert_id": A1, "annotation": "up"}).json()["annotation"] == "up"
    assert _c.post("/api/annotations", json={"alert_id": A1, "annotation": "down"}).json()["annotation"] == "down"
    assert _db_rows(A1) == ["down"]
    # same thumb again -> toggles off (row removed).
    assert _c.post("/api/annotations", json={"alert_id": A1, "annotation": "down"}).json()["annotation"] is None
    assert _db_rows(A1) == []
    _cleanup()


def test_bad_annotation_rejected():
    r = _c.post("/api/annotations", json={"alert_id": A1, "annotation": "sideways"})
    assert r.status_code == 400


def test_bulk_fetch():
    _cleanup()
    _c.post("/api/annotations", json={"alert_id": A1, "annotation": "up"})
    _c.post("/api/annotations", json={"alert_id": A2, "annotation": "down"})
    # A3 left un-annotated.
    d = _c.get(f"/api/annotations?alert_ids={A1},{A2},{A3}").json()
    assert d[str(A1)]["annotation"] == "up"
    assert d[str(A2)]["annotation"] == "down"
    assert d[str(A3)]["annotation"] is None
    _cleanup()


def test_end_to_end_click_lands_in_db_and_counts():
    _cleanup()
    # Simulate the card click.
    _c.post("/api/annotations", json={"alert_id": A3, "annotation": "up"})
    assert _db_rows(A3) == ["up"]
    conn = db_connection()
    counts = annotation_counts(conn, [A3])
    conn.close()
    assert counts.get(A3, {}).get("up") == 1
    _cleanup()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
