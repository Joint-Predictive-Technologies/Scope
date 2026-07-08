from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection


router = APIRouter()


def _row_to_dict(row) -> dict:
    return dict(row)


@router.get("")
def get_alerts(
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    ticker: str | None = Query(default=None, description="Filter by ticker symbol"),
    rule: str | None = Query(default=None, description="Filter by rule ID e.g. RULE_06"),
    severity: str | None = Query(default=None, description="CRITICAL, HIGH, or MEDIUM"),
    watchlist: bool = Query(default=False, description="Filter to watchlist tickers only"),
    limit: int = Query(default=100, ge=1, le=500),
):
    conn = db_connection()

    conditions = ["datetime(a.created_at) >= datetime('now', :lookback)"]
    params: dict = {"lookback": f"-{days} days", "limit": limit}

    if ticker:
        conditions.append("a.ticker LIKE :ticker")
        params["ticker"] = f"%{ticker.upper()}%"
    if rule:
        conditions.append("a.rule = :rule")
        params["rule"] = rule.upper()
    if severity:
        conditions.append("a.severity = :severity")
        params["severity"] = severity.upper()
    if watchlist:
        conditions.append(
            "EXISTS (SELECT 1 FROM watchlist w WHERE a.ticker LIKE '%' || w.symbol || '%')"
        )

    where = " AND ".join(conditions)

    rows = conn.execute(
        f"""
        SELECT
            a.id, a.rule, a.ticker, a.severity, a.headline, a.detail,
            a.tags, a.member_id, a.created_at,
            m.full_name, m.party, m.state
        FROM alerts a
        LEFT JOIN members m ON a.member_id = m.bioguide_id
        WHERE {where}
        ORDER BY datetime(a.created_at) DESC
        LIMIT :limit
        """,
        params,
    ).fetchall()

    conn.close()
    return [_row_to_dict(r) for r in rows]


@router.get("/{alert_id}")
def get_alert(alert_id: int):
    conn = db_connection()
    row = conn.execute(
        """
        SELECT
            a.id, a.rule, a.ticker, a.severity, a.headline, a.detail,
            a.tags, a.member_id, a.created_at,
            m.full_name, m.party, m.state
        FROM alerts a
        LEFT JOIN members m ON a.member_id = m.bioguide_id
        WHERE a.id = ?
        """,
        (alert_id,),
    ).fetchone()
    conn.close()

    if not row:
        return JSONResponse(status_code=404, content={"error": "Alert not found"})

    return _row_to_dict(row)
