from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection


router = APIRouter()


@router.get("")
def get_members(
    chamber: str | None = Query(default=None, description="house or senate"),
    party: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
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

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT * FROM members WHERE {where} ORDER BY full_name LIMIT :limit",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
    return {
        "member": dict(member),
        "trades": [dict(t) for t in trades],
    }
