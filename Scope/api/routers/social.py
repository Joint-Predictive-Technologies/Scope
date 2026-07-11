from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jpt_common import db_connection

router = APIRouter()


class VoteRequest(BaseModel):
    session_id: str


class CommentRequest(BaseModel):
    session_id: str
    body: str


class RateRequest(BaseModel):
    session_id: str
    vote: str  # 'useful' | 'not_useful'


# ── batch endpoint (used by alerts feed to bulk-load vote+comment counts) ─────

@router.get("/batch")
def get_batch(
    ids:        str = Query(description="Comma-separated alert IDs"),
    session_id: str = Query(default=""),
):
    """Return vote counts, whether the caller voted, and comment counts for a list of alert IDs."""
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid ids"})
    if not id_list:
        return {}

    conn = db_connection()

    # Vote counts
    placeholders = ",".join("?" * len(id_list))
    vote_rows = conn.execute(
        f"SELECT alert_id, COUNT(*) AS cnt FROM alert_votes WHERE alert_id IN ({placeholders}) GROUP BY alert_id",
        id_list,
    ).fetchall()
    vote_counts = {r["alert_id"]: r["cnt"] for r in vote_rows}

    # Caller's own votes
    my_votes: set[int] = set()
    if session_id:
        my_rows = conn.execute(
            f"SELECT alert_id FROM alert_votes WHERE alert_id IN ({placeholders}) AND session_id = ?",
            id_list + [session_id],
        ).fetchall()
        my_votes = {r["alert_id"] for r in my_rows}

    # Comment counts
    comment_rows = conn.execute(
        f"SELECT alert_id, COUNT(*) AS cnt FROM alert_comments WHERE alert_id IN ({placeholders}) GROUP BY alert_id",
        id_list,
    ).fetchall()
    comment_counts = {r["alert_id"]: r["cnt"] for r in comment_rows}

    conn.close()

    return {
        str(aid): {
            "votes":    vote_counts.get(aid, 0),
            "mine":     aid in my_votes,
            "comments": comment_counts.get(aid, 0),
        }
        for aid in id_list
    }


# ── votes ─────────────────────────────────────────────────────────────────────

@router.post("/{alert_id}/vote")
def toggle_vote(alert_id: int, req: VoteRequest):
    conn = db_connection()
    existing = conn.execute(
        "SELECT id FROM alert_votes WHERE alert_id = ? AND session_id = ?",
        (alert_id, req.session_id),
    ).fetchone()

    if existing:
        conn.execute(
            "DELETE FROM alert_votes WHERE alert_id = ? AND session_id = ?",
            (alert_id, req.session_id),
        )
        voted = False
    else:
        conn.execute(
            "INSERT OR IGNORE INTO alert_votes (alert_id, session_id) VALUES (?, ?)",
            (alert_id, req.session_id),
        )
        voted = True

    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM alert_votes WHERE alert_id = ?", (alert_id,)
    ).fetchone()[0]
    conn.close()
    return {"alert_id": alert_id, "voted": voted, "count": count}


# ── annotations: useful / not-useful rating (Phase 3) ─────────────────────────

@router.post("/{alert_id}/rate")
def rate_alert(alert_id: int, req: RateRequest):
    """Record a useful/not_useful annotation (one per session per alert).

    Feeds future signal weighting. Re-rating updates the existing row; rating the
    same value again clears it (toggle off)."""
    if req.vote not in ("useful", "not_useful"):
        return JSONResponse(status_code=400, content={"error": "vote must be useful|not_useful"})
    conn = db_connection()
    existing = conn.execute(
        "SELECT id, vote FROM alert_votes WHERE alert_id = ? AND session_id = ?",
        (alert_id, req.session_id),
    ).fetchone()
    if existing:
        if existing["vote"] == req.vote:
            conn.execute("DELETE FROM alert_votes WHERE id = ?", (existing["id"],))
            my_vote = None
        else:
            conn.execute("UPDATE alert_votes SET vote = ?, created_at = datetime('now') WHERE id = ?",
                         (req.vote, existing["id"]))
            my_vote = req.vote
    else:
        conn.execute(
            "INSERT INTO alert_votes (alert_id, session_id, vote) VALUES (?, ?, ?)",
            (alert_id, req.session_id, req.vote),
        )
        my_vote = req.vote
    conn.commit()
    counts = conn.execute(
        "SELECT vote, COUNT(*) c FROM alert_votes WHERE alert_id = ? GROUP BY vote",
        (alert_id,),
    ).fetchall()
    conn.close()
    tally = {r["vote"] or "useful": r["c"] for r in counts}
    return {"alert_id": alert_id, "my_vote": my_vote,
            "useful": tally.get("useful", 0), "not_useful": tally.get("not_useful", 0)}


# ── comments ──────────────────────────────────────────────────────────────────

@router.get("/{alert_id}/comments")
def get_comments(alert_id: int):
    conn = db_connection()
    rows = conn.execute(
        """SELECT id, body, created_at,
                  substr(session_id, 6, 8) AS anon_id
           FROM alert_comments
           WHERE alert_id = ?
           ORDER BY created_at ASC""",
        (alert_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/{alert_id}/comment")
def post_comment(alert_id: int, req: CommentRequest):
    body = req.body.strip()
    if not body:
        return JSONResponse(status_code=400, content={"error": "Comment cannot be empty"})
    if len(body) > 500:
        return JSONResponse(status_code=400, content={"error": "Max 500 characters"})

    conn = db_connection()
    conn.execute(
        "INSERT INTO alert_comments (alert_id, session_id, body) VALUES (?, ?, ?)",
        (alert_id, req.session_id, body),
    )
    conn.commit()
    row = conn.execute(
        """SELECT id, body, created_at, substr(session_id, 6, 8) AS anon_id
           FROM alert_comments WHERE rowid = last_insert_rowid()"""
    ).fetchone()
    conn.close()
    return dict(row)
