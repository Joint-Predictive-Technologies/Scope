from __future__ import annotations

from fastapi import APIRouter, Query

from jpt_common import db_connection


router = APIRouter()


@router.get("")
def get_tickers(limit: int = Query(default=200, ge=1, le=1000)):
    conn = db_connection()
    rows = conn.execute(
        "SELECT symbol, company_name FROM tickers ORDER BY symbol LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/watchlist")
def get_watchlist():
    conn = db_connection()
    rows = conn.execute(
        "SELECT symbol, added_at FROM watchlist ORDER BY symbol"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/{symbol}/alerts")
def get_ticker_alerts(
    symbol: str,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
):
    conn = db_connection()
    rows = conn.execute(
        """
        SELECT id, rule, severity, headline, detail, tags, created_at
        FROM alerts
        WHERE ticker LIKE ?
          AND datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC
        LIMIT ?
        """,
        (f"%{symbol.upper()}%", f"-{days} days", limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
