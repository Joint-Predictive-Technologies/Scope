"""
Market Thesis (theme) API — powers /intelligence and /thesis/<id> war rooms.
Themes are created/evolved by RULE_10 corroboration (Feature 4).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection

router = APIRouter()


def _parse_json(val, default):
    try:
        return json.loads(val) if val else default
    except Exception:
        return default


@router.get("")
def list_themes(
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Active theses sorted by opportunity score (the /intelligence list)."""
    conn = db_connection()
    where = "" if include_inactive else "WHERE status NOT IN ('Resolved','Fading')"
    rows = conn.execute(
        f"""
        SELECT id, title, status, region, sector, primary_ticker,
               evidence_confidence, opportunity_score, novelty_score,
               time_horizon, conflict_status, signal_count,
               what_changed, first_signal_at, last_updated
        FROM themes {where}
        ORDER BY opportunity_score DESC, datetime(last_updated) DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/{theme_id}")
def theme_detail(theme_id: int):
    conn = db_connection()
    theme = conn.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
    if not theme:
        conn.close()
        return JSONResponse(status_code=404, content={"error": "Theme not found"})
    theme = dict(theme)

    signals = [
        dict(r) for r in conn.execute(
            """
            SELECT a.id, a.rule, a.ticker, a.severity, a.headline, a.detail,
                   a.why_matters, a.verify_url, a.source_url, a.created_at,
                   a.evidence_confidence, a.opportunity_score, a.time_horizon,
                   a.novelty_score, a.absorption_pct,
                   ts.added_at
            FROM alerts a
            JOIN theme_signals ts ON a.id = ts.alert_id
            WHERE ts.theme_id = ?
            ORDER BY datetime(a.created_at) DESC
            """,
            (theme_id,),
        ).fetchall()
    ]

    # Group into a date-bucketed timeline (newest day first).
    timeline: dict[str, list] = {}
    for s in signals:
        day = (s.get("created_at") or "")[:10]
        timeline.setdefault(day, []).append(s)
    timeline_list = [{"date": d, "signals": timeline[d]} for d in sorted(timeline, reverse=True)]

    related = [
        dict(r) for r in conn.execute(
            """
            SELECT id, title, status, opportunity_score
            FROM themes
            WHERE (COALESCE(sector,'') = COALESCE(?, '') OR COALESCE(region,'') = COALESCE(?, ''))
              AND id != ? AND status NOT IN ('Resolved')
            ORDER BY opportunity_score DESC LIMIT 5
            """,
            (theme.get("sector"), theme.get("region"), theme_id),
        ).fetchall()
    ]

    outcome = conn.execute(
        "SELECT * FROM thesis_outcomes WHERE theme_id = ? ORDER BY id DESC LIMIT 1",
        (theme_id,),
    ).fetchone()
    conn.close()

    theme["supporting_rules"] = _parse_json(theme.get("supporting_rules"), [])
    theme["affected_tickers"] = _parse_json(theme.get("affected_tickers"), [])
    theme["watch_triggers"] = _parse_json(theme.get("watch_triggers"), [])
    theme["invalidation_conditions"] = _parse_json(theme.get("invalidation_conditions"), [])
    return {
        "theme": theme,
        "signals": signals,
        "timeline": timeline_list,
        "related": related,
        "outcome": dict(outcome) if outcome else None,
    }
