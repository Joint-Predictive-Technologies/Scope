from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection


router = APIRouter()


@router.get("")
def get_members(
    chamber: str | None = Query(default=None, description="house or senate"),
    party: str | None = Query(default=None),
    is_current: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=5000),
):
    conn = db_connection()

    conditions = ["1=1"]
    params: dict = {"limit": limit}

    if chamber:
        conditions.append("LOWER(chamber) = :chamber")
        params["chamber"] = chamber.lower()
    if party:
        conditions.append("UPPER(party) = :party")
        params["party"] = party.upper()
    if is_current is not None:
        conditions.append("is_current = :is_current")
        params["is_current"] = 1 if is_current else 0

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT * FROM members WHERE {where} ORDER BY full_name LIMIT :limit",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/{bioguide_id}/profile")
def get_member_profile(bioguide_id: str):
    conn = db_connection()
    member = conn.execute(
        "SELECT * FROM members WHERE bioguide_id = ?", (bioguide_id,)
    ).fetchone()
    if not member:
        conn.close()
        return JSONResponse(status_code=404, content={"error": "Member not found"})

    stats = conn.execute(
        """
        SELECT
            COUNT(*)  AS total_trades,
            SUM(CASE WHEN LOWER(transaction_type) LIKE '%purchase%' THEN 1 ELSE 0 END) AS total_purchases,
            SUM(CASE WHEN LOWER(transaction_type) LIKE '%sale%'     THEN 1 ELSE 0 END) AS total_sales,
            MAX(transaction_date) AS most_recent_trade
        FROM transactions WHERE member_id = ?
        """,
        (bioguide_id,),
    ).fetchone()

    most_traded = conn.execute(
        """
        SELECT raw_ticker_string AS ticker, COUNT(*) AS c
        FROM transactions
        WHERE member_id = ? AND raw_ticker_string IS NOT NULL AND raw_ticker_string != ''
        GROUP BY raw_ticker_string ORDER BY c DESC LIMIT 1
        """,
        (bioguide_id,),
    ).fetchone()

    top_tickers = conn.execute(
        """
        SELECT raw_ticker_string AS ticker, COUNT(*) AS trade_count,
               GROUP_CONCAT(DISTINCT transaction_type) AS types
        FROM transactions
        WHERE member_id = ?
          AND raw_ticker_string IS NOT NULL AND raw_ticker_string != ''
        GROUP BY raw_ticker_string
        ORDER BY trade_count DESC
        LIMIT 15
        """,
        (bioguide_id,),
    ).fetchall()

    recent_trades = conn.execute(
        """
        SELECT
            t.raw_ticker_string   AS ticker,
            t.transaction_type,
            t.amount_band,
            t.transaction_date,
            t.filing_date,
            CAST(julianday(t.filing_date) - julianday(t.transaction_date) AS INTEGER) AS filing_delay,
            f.raw_url AS filing_url
        FROM transactions t
        JOIN filings f ON t.filing_id = f.id
        WHERE t.member_id = ?
        ORDER BY t.transaction_date DESC
        LIMIT 100
        """,
        (bioguide_id,),
    ).fetchall()

    member_alerts = conn.execute(
        """
        SELECT id, rule, ticker, severity, headline, detail, created_at
        FROM alerts
        WHERE member_id = ?
        ORDER BY datetime(created_at) DESC
        LIMIT 10
        """,
        (bioguide_id,),
    ).fetchall()

    conn.close()
    return {
        "member": dict(member),
        "stats": {
            "total_trades":      stats["total_trades"] or 0,
            "total_purchases":   stats["total_purchases"] or 0,
            "total_sales":       stats["total_sales"] or 0,
            "most_recent_trade": stats["most_recent_trade"],
            "most_traded_ticker": most_traded["ticker"] if most_traded else None,
        },
        "top_tickers":   [dict(r) for r in top_tickers],
        "recent_trades": [dict(r) for r in recent_trades],
        "alerts":        [dict(r) for r in member_alerts],
    }


@router.get("/{bioguide_id}/trades")
def get_member_trades(
    bioguide_id: str,
    days: int = Query(default=90, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
):
    conn = db_connection()
    member = conn.execute(
        "SELECT * FROM members WHERE bioguide_id = ?", (bioguide_id,)
    ).fetchone()
    if not member:
        conn.close()
        return JSONResponse(status_code=404, content={"error": "Member not found"})

    trades = conn.execute(
        """
        SELECT
            t.id, t.raw_ticker_string AS ticker, t.raw_description AS description,
            t.transaction_type, t.amount_band, t.transaction_date,
            f.raw_url AS filing_url
        FROM transactions t
        JOIN filings f ON t.filing_id = f.id
        WHERE t.member_id = ?
          AND t.transaction_date >= date('now', ?)
        ORDER BY t.transaction_date DESC
        LIMIT ?
        """,
        (bioguide_id, f"-{days} days", limit),
    ).fetchall()

    conn.close()
    return {"member": dict(member), "trades": [dict(t) for t in trades]}
