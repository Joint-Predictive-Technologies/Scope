#!/usr/bin/env python3
"""
Integration tests for the evidence drawer + brief evidence linkage.

Runs under pytest, or standalone:  python3 tests/test_evidence_today.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402
from jpt_common import db_connection  # noqa: E402

_c = TestClient(app)


def _any_alert_id():
    conn = db_connection()
    row = conn.execute(
        "SELECT id FROM alerts WHERE ticker IS NOT NULL AND ticker!='' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0] if row else None


def test_evidence_alert_shape():
    aid = _any_alert_id()
    if aid is None:
        return  # empty DB — nothing to assert
    d = _c.get(f"/api/evidence/alert/{aid}").json()
    assert "alert" in d and "confidence" in d and "source" in d
    assert 0 <= d["confidence"]["total"] <= 100
    assert isinstance(d["confidence"]["components"], dict)
    assert "related" in d and isinstance(d["related"], list)


def test_evidence_alert_404():
    assert _c.get("/api/evidence/alert/999999999").status_code == 404


def test_evidence_ticker_shape():
    d = _c.get("/api/evidence/ticker/LMT").json()
    assert d["ticker"] == "LMT"
    assert "signals" in d and "rules" in d and "count" in d


def test_brief_data_carries_evidence_when_present():
    import datetime
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    conn = db_connection()
    ids = [r[0] for r in conn.execute("SELECT id FROM alerts LIMIT 2").fetchall()]
    conn.execute(
        """INSERT OR REPLACE INTO daily_briefs
           (date, content_json, generated_at, alert_ids, evidence_json)
           VALUES (?,?,?,?,?)""",
        (today, json.dumps({"ai_summary": "x"}),
         datetime.datetime.utcnow().isoformat(),
         json.dumps(ids), json.dumps({"LMT": [{"id": ids[0] if ids else 1, "rule": "RULE_11"}]})),
    )
    conn.commit()
    conn.close()
    try:
        d = _c.get("/brief/data").json()
        assert "evidence_json" in d and "stale" in d and d.get("generated_at")
        assert "LMT" in json.loads(d["evidence_json"])
    finally:
        conn = db_connection()
        conn.execute("DELETE FROM daily_briefs WHERE date=?", (today,))
        conn.commit()
        conn.close()


def test_today_tab_endpoints_respond():
    # The Today panel consumes these — all must return arrays/objects, not error.
    assert isinstance(_c.get("/alerts?days=7&severity_min=HIGH&limit=8").json(), list)
    assert isinstance(_c.get("/api/heat-index?days=30").json(), list)
    assert isinstance(_c.get("/congress/trades?days=90&limit=8").json(), list)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
