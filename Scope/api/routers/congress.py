from __future__ import annotations

from fastapi import APIRouter, Query

from jpt_common import db_connection

router = APIRouter()


@router.get("/trades")
def get_congress_trades(
    days: int         = Query(default=90, ge=1, le=365),
    chamber: str | None = Query(default=None),
    party: str | None   = Query(default=None),
    state: str | None   = Query(default=None),
    ticker: str | None  = Query(default=None),
    limit: int          = Query(default=100, ge=1, le=500),
):
    conn = db_connection()
    conditions = [
        "t.transaction_date >= date('now', :window)",
        "date(t.transaction_date) IS NOT NULL",
        "date(t.transaction_date) <= date('now')",
    ]
    params: dict = {"window": f"-{days} days", "limit": limit}

    if chamber:
        conditions.append("LOWER(m.chamber) = :chamber")
        params["chamber"] = chamber.lower()
    if party:
        conditions.append("UPPER(m.party) = :party")
        params["party"] = party.upper()
    if state:
        conditions.append("UPPER(m.state) = :state")
        params["state"] = state.upper()
    if ticker:
        conditions.append("t.raw_ticker_string LIKE :ticker")
        params["ticker"] = f"%{ticker.upper()}%"

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"""
        SELECT
            t.id,
            t.raw_ticker_string   AS ticker,
            t.transaction_type,
            t.amount_band,
            t.transaction_date,
            t.filing_date,
            CAST(julianday(t.filing_date) - julianday(t.transaction_date) AS INTEGER) AS filing_delay,
            m.bioguide_id AS member_id,
            m.full_name,
            m.party,
            m.state,
            m.chamber
        FROM transactions t
        JOIN members m ON t.member_id = m.bioguide_id
        WHERE {where}
        ORDER BY t.transaction_date DESC
        LIMIT :limit
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/stats")
def get_congress_stats(days: int = Query(default=30, ge=1, le=365)):
    conn = db_connection()
    total = conn.execute(
        "SELECT COUNT(*) FROM transactions t JOIN members m ON t.member_id = m.bioguide_id "
        "WHERE t.transaction_date >= date('now', ?)",
        (f"-{days} days",),
    ).fetchone()[0]

    top_tickers = conn.execute(
        """SELECT raw_ticker_string AS ticker, COUNT(*) AS c
           FROM transactions
           WHERE transaction_date >= date('now', ?)
             AND raw_ticker_string IS NOT NULL AND trim(raw_ticker_string) != ''
           GROUP BY raw_ticker_string ORDER BY c DESC LIMIT 5""",
        (f"-{days} days",),
    ).fetchall()

    conn.close()
    return {
        "total_trades": total,
        "top_tickers": [dict(r) for r in top_tickers],
    }
