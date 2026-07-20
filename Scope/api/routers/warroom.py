"""
War-room API — cluster war rooms + per-entity notes and entity-level annotations.

Two view types share this router:
- Clusters: derived from RULE_CLUSTER alerts (event-centric war room).
- Notes / entity annotations: a single free-text note and a thumbs annotation on
  the whole thesis or cluster (distinct from per-alert annotations in Part 1),
  stored in war_rooms keyed by (entity_type, entity_id).

Fingerprint URL form is `<members>__<ticker>__<direction>` (URL-safe); the stored
alert fingerprint is `CLUSTER::<members>::<ticker>::<direction>`.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jpt_common import db_connection, opportunity_score_breakdown

router = APIRouter()


# ── fingerprint helpers ───────────────────────────────────────────────────────

def fp_to_url(stored: str) -> str:
    """CLUSTER::A+B::TICK::dir  ->  A+B__TICK__dir"""
    return (stored or "").replace("CLUSTER::", "").replace("::", "__")


def url_to_fp(url_id: str) -> str:
    """A+B__TICK__dir  ->  CLUSTER::A+B::TICK::dir"""
    return "CLUSTER::" + (url_id or "").replace("__", "::")


def _tags(row) -> dict:
    try:
        return json.loads(row["tags"] or "{}")
    except Exception:
        return {}


def _detail(row) -> dict:
    try:
        return json.loads(row["detail"] or "{}")
    except Exception:
        return {}


# ── clusters index ────────────────────────────────────────────────────────────

@router.get("/clusters")
def list_clusters(limit: int = Query(default=100, ge=1, le=500)):
    """Recent RULE_CLUSTER alerts as cluster cards (most recent first)."""
    conn = db_connection()
    rows = conn.execute(
        """SELECT id, ticker, severity, headline, tags, created_at,
                  evidence_confidence, opportunity_score, lifecycle_stage
           FROM alerts WHERE rule = 'RULE_CLUSTER'
           ORDER BY datetime(created_at) DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        t = _tags(r)
        out.append({
            "fingerprint": fp_to_url(t.get("fingerprint", "")),
            "ticker": r["ticker"], "severity": r["severity"], "headline": r["headline"],
            "direction": t.get("direction"), "distinct_members": t.get("distinct_members"),
            "created_at": r["created_at"], "lifecycle_stage": r["lifecycle_stage"],
            "opportunity_score": r["opportunity_score"],
            "evidence_confidence": r["evidence_confidence"],
        })
    return out


# ── cluster detail ────────────────────────────────────────────────────────────

@router.get("/clusters/{fingerprint}")
def cluster_detail(fingerprint: str):
    stored_fp = url_to_fp(fingerprint)
    conn = db_connection()
    # Find the alert carrying this fingerprint (newest wins if somehow duplicated).
    row = conn.execute(
        """SELECT * FROM alerts WHERE rule='RULE_CLUSTER'
           AND tags LIKE ? ORDER BY id DESC LIMIT 1""",
        (f'%"{stored_fp}"%',),
    ).fetchone()
    if not row:
        conn.close()
        return JSONResponse(status_code=404, content={"error": "Cluster not found"})

    tags = _tags(row)
    detail = _detail(row)
    ticker = row["ticker"]
    members = detail.get("members", [])
    member_ids = tags.get("members", [])

    # Enrich members with party/state from the members table.
    for m in members:
        mrow = conn.execute(
            "SELECT full_name, party, state FROM members WHERE bioguide_id=?",
            (m.get("member_id"),),
        ).fetchone()
        if mrow:
            m["party"] = mrow["party"]
            m["state"] = mrow["state"]

    # Detection-time score decomposition (immutable — from stored fields).
    decomposition = opportunity_score_breakdown(
        row["novelty_score"], row["absorption_pct"], row["time_horizon"])

    # Related alerts: other rules on the same ticker within ~30 days.
    related = [dict(r) for r in conn.execute(
        """SELECT id, rule, severity, headline, created_at, ticker,
                  evidence_confidence, opportunity_score
           FROM alerts
           WHERE ticker = ? AND rule != 'RULE_CLUSTER'
             AND datetime(created_at) >= datetime(?, '-30 days')
           ORDER BY datetime(created_at) DESC LIMIT 40""",
        (ticker, row["created_at"]),
    ).fetchall()]

    # Conflicting evidence: for a consensus cluster, opposing insider/other signal.
    conflicting = []
    direction = tags.get("direction")
    if direction in ("consensus_buy", "consensus_sell"):
        for a in related:
            hl = (a["headline"] or "").lower()
            opp = "sold" if direction == "consensus_buy" else "bought"
            if opp in hl or ("insider" in hl and opp in hl):
                conflicting.append(a)

    # Upgrade history: superseded smaller clusters on this ticker whose member set
    # is a subset of this one.
    cur_set = set(member_ids)
    upgrades = []
    for r in conn.execute(
        """SELECT id, headline, created_at, tags, lifecycle_stage FROM alerts
           WHERE rule='RULE_CLUSTER' AND ticker=? AND id != ?
           ORDER BY datetime(created_at) ASC""",
        (ticker, row["id"]),
    ).fetchall():
        prev_members = set(_tags(r).get("members", []))
        if prev_members and prev_members < cur_set:
            upgrades.append({"id": r["id"], "headline": r["headline"],
                             "created_at": r["created_at"],
                             "distinct_members": len(prev_members),
                             "lifecycle_stage": r["lifecycle_stage"]})

    war = _get_war_room(conn, "cluster", fingerprint)
    conn.close()

    return {
        "alert": {
            "id": row["id"], "ticker": ticker, "severity": row["severity"],
            "headline": row["headline"], "created_at": row["created_at"],
            "why_matters": row["why_matters"], "source_url": row["source_url"],
            "verify_url": row["verify_url"], "lifecycle_stage": row["lifecycle_stage"],
            "evidence_confidence": row["evidence_confidence"],
            "opportunity_score": row["opportunity_score"],
            "novelty_score": row["novelty_score"], "time_horizon": row["time_horizon"],
        },
        "fingerprint": fingerprint,
        "direction": direction,
        "distinct_members": tags.get("distinct_members"),
        "members": members,
        "window_hours": detail.get("window_hours"),
        "decomposition": decomposition,
        "related": related,
        "conflicting": conflicting,
        "upgrades": upgrades,
        "war_room": war,
    }


# ── notes + entity-level annotations (thesis or cluster) ──────────────────────

class NoteRequest(BaseModel):
    entity_type: str          # 'theme' | 'cluster'
    entity_id: str
    note: str | None = None


class EntityAnnotationRequest(BaseModel):
    entity_type: str
    entity_id: str
    annotation: str           # 'up' | 'down'


def _get_war_room(conn, entity_type: str, entity_id: str) -> dict:
    row = conn.execute(
        "SELECT note, annotation FROM war_rooms WHERE entity_type=? AND entity_id=?",
        (entity_type, entity_id),
    ).fetchone()
    return {"note": row["note"] if row else None,
            "annotation": row["annotation"] if row else None}


def _ensure_row(conn, entity_type: str, entity_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO war_rooms (entity_type, entity_id) VALUES (?, ?)",
        (entity_type, entity_id),
    )


@router.get("/warroom/{entity_type}/{entity_id:path}")
def get_war_room(entity_type: str, entity_id: str):
    conn = db_connection()
    out = _get_war_room(conn, entity_type, entity_id)
    conn.close()
    return out


@router.post("/warroom/note")
def set_note(req: NoteRequest):
    if req.entity_type not in ("theme", "cluster"):
        return JSONResponse(status_code=400, content={"error": "bad entity_type"})
    conn = db_connection()
    _ensure_row(conn, req.entity_type, req.entity_id)
    conn.execute(
        "UPDATE war_rooms SET note=?, updated_at=datetime('now') "
        "WHERE entity_type=? AND entity_id=?",
        (req.note, req.entity_type, req.entity_id),
    )
    conn.commit()
    out = _get_war_room(conn, req.entity_type, req.entity_id)
    conn.close()
    return out


@router.post("/warroom/annotation")
def set_entity_annotation(req: EntityAnnotationRequest):
    if req.entity_type not in ("theme", "cluster"):
        return JSONResponse(status_code=400, content={"error": "bad entity_type"})
    if req.annotation not in ("up", "down"):
        return JSONResponse(status_code=400, content={"error": "annotation must be up|down"})
    conn = db_connection()
    _ensure_row(conn, req.entity_type, req.entity_id)
    cur = conn.execute(
        "SELECT annotation FROM war_rooms WHERE entity_type=? AND entity_id=?",
        (req.entity_type, req.entity_id),
    ).fetchone()
    new = None if (cur and cur["annotation"] == req.annotation) else req.annotation
    conn.execute(
        "UPDATE war_rooms SET annotation=?, annotated_at=datetime('now') "
        "WHERE entity_type=? AND entity_id=?",
        (new, req.entity_type, req.entity_id),
    )
    conn.commit()
    conn.close()
    return {"entity_type": req.entity_type, "entity_id": req.entity_id, "annotation": new}
