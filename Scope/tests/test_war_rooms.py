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
from jpt_common import (SIGNED_RULES, db_connection,  # noqa: E402
                        opportunity_score_breakdown)

_c = TestClient(app)


# ── cluster fixture ───────────────────────────────────────────────────────────
# These tests used to assert on SPCX — a real RULE_CLUSTER alert that happened to
# sit in the working database. They passed only because the suite was reading
# production data. Now each test seeds the cluster it asserts on, so it holds on
# an empty DB and is independent of whatever prod contains.
#
# Shapes mirror scripts/rule_cluster.py exactly: fingerprint
# `CLUSTER::<members joined by +>::<ticker>::<direction>` (rule_cluster.py:80),
# tags carry members/direction/distinct_members/fingerprint, and detail carries
# the per-member records the war room renders.

_T = "TCLU"                                    # synthetic test ticker
_MEMBERS = [                                   # (bioguide_id, full_name, party, state)
    ("TCLUM1", "Alpha, Ada",   "D", "CA"),
    ("TCLUM2", "Bravo, Ben",   "R", "TX"),
    ("TCLUM3", "Charlie, Cai", "I", "VT"),
]
# novelty 1.0, absorption 0, IMMEDIATE -> 40 - 0 + 20 + 5 = 65 (must equal the
# stored opportunity_score, since the war room asserts decomposition == stored).
_NOVELTY, _ABSORPTION, _HORIZON, _OPPORTUNITY = 1.0, 0.0, "IMMEDIATE", 65.0


def _seed_cluster(direction="consensus_buy", ticker=_T, members=_MEMBERS):
    """Insert a RULE_CLUSTER alert (+ its members) and return its URL fingerprint."""
    import json
    ids = sorted(m[0] for m in members)
    fp = f"CLUSTER::{'+'.join(ids)}::{ticker}::{direction}"
    names = [m[1] for m in members]
    conn = db_connection()
    for bioguide_id, full_name, party, state in members:
        conn.execute(
            "INSERT OR REPLACE INTO members (bioguide_id, full_name, party, state, chamber) "
            "VALUES (?,?,?,?,'House')", (bioguide_id, full_name, party, state))
    tags = json.dumps({
        "members": ids, "member_names": names, "direction": direction,
        "distinct_members": len(members), "fingerprint": fp, "is_upgrade": False,
        "tags": ["congressional", "cluster", direction],
    })
    detail = json.dumps({
        "fingerprint": fp, "direction": direction, "distinct_members": len(members),
        "members": [{"member_id": m[0], "name": m[1], "direction": "buy",
                     "transaction_dates": ["2026-07-01"], "sizes": ["$1,001 - $15,000"],
                     "doc_id": "TESTDOC", "filing_url": "https://example.invalid/ptr"}
                    for m in members],
        "unusual_types_members": [], "window_hours": 72,
    })
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, why_matters, tags, detail,
               novelty_score, absorption_pct, time_horizon, opportunity_score,
               evidence_confidence, source_quality, created_at)
           VALUES ('RULE_CLUSTER',?,'HIGH',?,?,?,?,?,?,?,?,?, 'Primary', datetime('now'))""",
        (ticker, f"{len(members)} members bought {ticker} in 72h",
         f"{len(members)} distinct members bought {ticker}. Identity {fp}.",
         tags, detail, _NOVELTY, _ABSORPTION, _HORIZON, _OPPORTUNITY, 20.0))
    conn.commit(); conn.close()
    return fp.replace("CLUSTER::", "").replace("::", "__")


# ── score decomposition ───────────────────────────────────────────────────────

def test_decomposition_math():
    # novelty 1.0, absorption 0, IMMEDIATE -> 40 - 0 + 20 + 5 = 65
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


def test_cluster_detail_three_members():
    fp = _seed_cluster()
    d = _c.get(f"/api/clusters/{fp}").json()
    assert d["direction"] == "consensus_buy"
    assert d["distinct_members"] == 3
    names = sorted(m["name"] for m in d["members"])
    assert names == ["Alpha, Ada", "Bravo, Ben", "Charlie, Cai"]
    # party/state enriched from the members table
    assert all(m.get("party") for m in d["members"])
    assert {m["state"] for m in d["members"]} == {"CA", "TX", "VT"}
    # decomposition present and immutable (detection-time)
    assert d["decomposition"]["total"] == d["alert"]["opportunity_score"] == _OPPORTUNITY
    # EC and OS are separate fields
    assert "evidence_confidence" in d["alert"] and "opportunity_score" in d["alert"]


def test_clusters_index():
    _seed_cluster()
    d = _c.get("/api/clusters").json()
    assert isinstance(d, list)
    hit = [c for c in d if c["ticker"] == _T]
    assert len(hit) == 1, f"seeded {_T} cluster must appear exactly once"
    assert hit[0]["direction"] == "consensus_buy"
    assert hit[0]["distinct_members"] == 3


# ── notes + entity annotation (integration with Part 1 pattern) ───────────────

def test_warroom_note_and_annotation_upsert():
    fp = _seed_cluster()
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
    # ⚠️ THIS FIXTURE BROKE ONLY WHEN TWO BRANCHES MET, WHICH IS WHY IT READS LIKE THIS.
    # It seeded RULE_01B + RULE_06 + RULE_08 + RULE_11 and relied on 3 instruments to make
    # the gate fire. Two independent changes each removed one leg:
    #   * RULE_08 became basket-excluded (its ticker comes from a keyword lookup), and
    #   * RULE_06 now corroborates only on a genuine open-market buy, failing closed on a
    #     NULL verdict.
    # Either alone still left three instruments and this test passed on both branches
    # separately. TOGETHER they left two, the gate correctly refused, no theme was created,
    # and the assertion below failed for a reason that has nothing to do with war rooms.
    # RULE_16 supplies a live fourth instrument and the verdict is explicit, so this now
    # holds whether or not RULE_08 is excluded.
    for rule in ("RULE_01B", "RULE_06", "RULE_16", "RULE_11"):
        conn.execute(
            "INSERT INTO alerts (rule, ticker, severity, headline, created_at, "
            "corroborates) VALUES (?, 'ZWAR', 'HIGH', ?, datetime('now'), ?)",
            (rule, f"{rule} ZWAR", 1 if rule in SIGNED_RULES else None))
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
