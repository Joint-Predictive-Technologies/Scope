"""
Alert annotations — thumbs up / down.

Maksim's judgment feeding back into the system: this is the training signal for
the eventual source-quality weighting work. One annotation per (alert_id,
user_id); re-annotating REPLACES the prior one (upsert). Annotating the same
value again toggles it off.

Counts (up_count/down_count) are deliberately NOT returned to the feed — we don't
want annotation behavior influenced by seeing prior votes. Aggregates are backend
-only (see annotation_counts / the daily count in decay_alerts).
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jpt_common import db_connection

router = APIRouter()


class AnnotationRequest(BaseModel):
    alert_id: int
    annotation: str          # 'up' | 'down'
    user_id: str | None = None
    note: str | None = None


def _current(conn, alert_id: int, user_id: str | None):
    row = conn.execute(
        "SELECT annotation FROM alert_annotations WHERE alert_id = ? AND user_id IS ?",
        (alert_id, user_id),
    ).fetchone()
    return row["annotation"] if row else None


@router.post("")
def set_annotation(req: AnnotationRequest):
    if req.annotation not in ("up", "down"):
        return JSONResponse(status_code=400, content={"error": "annotation must be up|down"})

    conn = db_connection()
    existing = conn.execute(
        "SELECT id, annotation FROM alert_annotations WHERE alert_id = ? AND user_id IS ?",
        (req.alert_id, req.user_id),
    ).fetchone()

    if existing:
        if existing["annotation"] == req.annotation:
            # Same thumb again -> toggle off.
            conn.execute("DELETE FROM alert_annotations WHERE id = ?", (existing["id"],))
            current = None
        else:
            conn.execute(
                "UPDATE alert_annotations SET annotation = ?, note = COALESCE(?, note), "
                "annotated_at = datetime('now') WHERE id = ?",
                (req.annotation, req.note, existing["id"]),
            )
            current = req.annotation
    else:
        conn.execute(
            "INSERT INTO alert_annotations (alert_id, annotation, user_id, note) "
            "VALUES (?, ?, ?, ?)",
            (req.alert_id, req.annotation, req.user_id, req.note),
        )
        current = req.annotation

    conn.commit()
    conn.close()
    return {"alert_id": req.alert_id, "annotation": current}


@router.get("")
def get_annotations(
    alert_id: int | None = Query(default=None),
    alert_ids: str | None = Query(default=None, description="Comma-separated alert IDs (bulk)"),
    user_id: str | None = Query(default=None),
):
    """Return the caller's own annotation state per alert. Bulk via alert_ids so
    the feed makes ONE request for many cards. Counts are intentionally omitted."""
    ids: list[int] = []
    if alert_ids:
        ids = [int(x) for x in alert_ids.split(",") if x.strip().lstrip("-").isdigit()]
    elif alert_id is not None:
        ids = [alert_id]
    if not ids:
        return {}

    conn = db_connection()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT alert_id, annotation FROM alert_annotations "
        f"WHERE alert_id IN ({placeholders}) AND user_id IS ?",
        [*ids, user_id],
    ).fetchall()
    conn.close()

    state = {r["alert_id"]: r["annotation"] for r in rows}
    return {str(aid): {"annotation": state.get(aid)} for aid in ids}
