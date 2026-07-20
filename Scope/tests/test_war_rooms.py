#!/usr/bin/env python3
"""
Tests for the war-room interpretation layer (thesis + cluster views).

Runs under pytest or standalone:  python3 tests/test_war_rooms.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402
from jpt_common import db_connection, opportunity_score_breakdown  # noqa: E402

_c = TestClient(app)


def _spcx_fingerprint():
    conn = db_connection()
    row = conn.execute(
        "SELECT tags FROM alerts WHERE rule='RULE_CLUSTER' AND ticker='SPCX' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    import json
    fp = json.loads(row["tags"])["fingerprint"] if row else ""
    return fp.replace("CLUSTER::", "").replace("::", "__")


# ── score decomposition ───────────────────────────────────────────────────────

def test_decomposition_math():
    # SPCX: novelty 1.0, absorption 0, IMMEDIATE -> 40 - 0 + 20 + 5 = 65
    d = opportunity_score_breakdown(1.0, 0.0, "IMMEDIATE")
    assert d["total"] == 65.0
    vals = {c["value"] for c in d["components"]}
    assert 40.0 in vals and 20.0 in vals and 5.0 in vals


def test_decomposition_absorption_penalizes():
    d = opportunity_score_breakdown(1.0, 50.0, "IMMEDIATE")
    # 40 - 15 + 20 + 5 = 50
    assert d["total"] == 50.0


# ── cluster war room ──────────────────────────────────────────────────────────

def test_cluster_page_route_serves():
    r = _c.get("/cluster/anything__X__consensus_buy")
    assert r.status_code == 200
    assert "Cluster War Room" in r.text


def test_cluster_detail_spcx_three_members():
    fp = _spcx_fingerprint()
    assert fp, "SPCX cluster must exist (run rule_cluster first)"
    d = _c.get(f"/api/clusters/{fp}").json()
    assert d["direction"] == "consensus_buy"
    assert d["distinct_members"] == 3
    names = sorted(m["name"] for m in d["members"])
    assert names == ["Cisneros, Gilbert Ray", "McGuire, John J.", "Meuser, Daniel"]
    # party/state enriched
    assert all(m.get("party") for m in d["members"])
    # decomposition present and immutable (detection-time)
    assert d["decomposition"]["total"] == d["alert"]["opportunity_score"]
    # EC and OS are separate fields
    assert "evidence_confidence" in d["alert"] and "opportunity_score" in d["alert"]


def test_clusters_index():
    d = _c.get("/api/clusters").json()
    assert isinstance(d, list)
    assert any(c["ticker"] == "SPCX" for c in d)


# ── notes + entity annotation (integration with Part 1 pattern) ───────────────

def test_warroom_note_and_annotation_upsert():
    fp = _spcx_fingerprint()
    # note upsert
    _c.post("/api/warroom/note", json={"entity_type": "cluster", "entity_id": fp, "note": "watching closely"})
    got = _c.get(f"/api/warroom/cluster/{fp}").json()
    assert got["note"] == "watching closely"
    # annotation upsert + toggle
    r1 = _c.post("/api/warroom/annotation", json={"entity_type": "cluster", "entity_id": fp, "annotation": "up"}).json()
    assert r1["annotation"] == "up"
    r2 = _c.post("/api/warroom/annotation", json={"entity_type": "cluster", "entity_id": fp, "annotation": "up"}).json()
    assert r2["annotation"] is None      # same thumb toggles off
    # cleanup
    conn = db_connection()
    conn.execute("DELETE FROM war_rooms WHERE entity_type='cluster' AND entity_id=?", (fp,))
    conn.commit(); conn.close()


# ── thesis war room ───────────────────────────────────────────────────────────

def test_thesis_page_route_and_empty_state_prompt():
    r = _c.get("/thesis/1")
    assert r.status_code == 200
    # Empty-state falsification prompt must be present in the template (not hidden).
    assert "incomplete" in r.text.lower()
    assert "invalidated if" in r.text.lower()


def test_thesis_detail_for_real_theme():
    # Seed a theme via RULE_10 corroboration, then load its detail.
    from scripts.rule_10_corroboration import run as run_r10
    conn = db_connection()
    conn.execute("DELETE FROM alerts WHERE ticker='ZWAR'")
    for rule in ("RULE_01B", "RULE_06", "RULE_08", "RULE_11"):
        conn.execute(
            "INSERT INTO alerts (rule, ticker, severity, headline, created_at) "
            "VALUES (?, 'ZWAR', 'HIGH', ?, datetime('now'))", (rule, f"{rule} ZWAR"))
    conn.commit(); conn.close()
    run_r10(dry_run=False, window_hours=24)

    conn = db_connection()
    theme = conn.execute("SELECT id FROM themes WHERE primary_ticker='ZWAR'").fetchone()
    conn.close()
    assert theme, "RULE_10 should have created a ZWAR theme"
    d = _c.get(f"/api/themes/{theme['id']}").json()
    assert "theme" in d and "signals" in d and "timeline" in d
    # signals carry detection-time novelty/absorption for decomposition
    assert all("novelty_score" in s for s in d["signals"])
    assert isinstance(d["theme"]["invalidation_conditions"], list)

    conn = db_connection()
    tid = theme["id"]
    conn.execute("DELETE FROM theme_signals WHERE theme_id=?", (tid,))
    conn.execute("DELETE FROM themes WHERE id=?", (tid,))
    conn.execute("DELETE FROM alerts WHERE ticker='ZWAR'")
    conn.commit(); conn.close()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
