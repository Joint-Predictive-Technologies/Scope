from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection


router = APIRouter()


@router.get("/leaderboard")
def get_leaderboard(days: int = Query(default=7, ge=1, le=90)):
    conn = db_connection()
    rows = conn.execute(
        """
        SELECT
            REPLACE(ticker, '$', '') AS ticker,
            COUNT(*)                 AS total_alerts,
            COUNT(DISTINCT rule)     AS rule_count,
            GROUP_CONCAT(DISTINCT rule) AS rules,
            MAX(CASE severity WHEN 'CRITICAL' THEN 'CRITICAL'
                              WHEN 'HIGH'     THEN 'HIGH'
                              ELSE 'MEDIUM' END) AS top_severity
        FROM alerts
        WHERE ticker IS NOT NULL AND ticker != ''
          AND ticker NOT LIKE '% %'
          AND datetime(created_at) >= datetime('now', ?)
        GROUP BY ticker
        ORDER BY rule_count DESC, total_alerts DESC
        LIMIT 10
        """,
        (f"-{days} days",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/watchlist")
def get_watchlist():
    conn = db_connection()
    rows = conn.execute(
        "SELECT symbol, added_at FROM watchlist ORDER BY added_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/watchlist/{symbol}")
def add_watchlist(symbol: str):
    conn = db_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)",
            (symbol.upper().strip(),),
        )
        conn.commit()
    except Exception as exc:
        conn.close()
        return JSONResponse(status_code=400, content={"error": str(exc)})
    conn.close()
    return {"status": "added", "symbol": symbol.upper()}


@router.delete("/watchlist/{symbol}")
def remove_watchlist(symbol: str):
    conn = db_connection()
    conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
    conn.commit()
    conn.close()
    return {"status": "removed", "symbol": symbol.upper()}


@router.get("")
def get_tickers(limit: int = Query(default=200, ge=1, le=1000)):
    conn = db_connection()
    rows = conn.execute(
        "SELECT symbol, company_name FROM tickers ORDER BY symbol LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/{symbol}/alerts")
def get_ticker_alerts(
    symbol: str,
    days: int = Query(default=365, ge=1, le=730),
    limit: int = Query(default=200, ge=1, le=500),
):
    conn = db_connection()
    rows = conn.execute(
        """
        SELECT id, rule, severity, headline, detail, tags, ticker, member_id, created_at
        FROM alerts
        WHERE ticker LIKE ?
          AND datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC
        LIMIT ?
        """,
        (f"%{symbol.upper()}%", f"-{days} days", limit),
    ).fetchall()

    # Related members from RULE_02 alerts
    members_rows = conn.execute(
        """
        SELECT DISTINCT m.bioguide_id, m.full_name, m.party, m.state
        FROM transactions t
        JOIN members m ON t.member_id = m.bioguide_id
        WHERE t.raw_ticker_string LIKE ?
        LIMIT 20
        """,
        (f"%{symbol.upper()}%",),
    ).fetchall()

    conn.close()
    return {
        "symbol": symbol.upper(),
        "alerts": [dict(r) for r in rows],
        "related_members": [dict(r) for r in members_rows],
    }
