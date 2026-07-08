from __future__ import annotations

from fastapi import APIRouter, Query

from jpt_common import db_connection

router = APIRouter()


@router.get("/data")
def get_contracts(
    ticker: str | None = Query(default=None),
    agency: str | None = Query(default=None),
    min_amount: float  = Query(default=0),
    sort: str          = Query(default="amount"),
    limit: int         = Query(default=100, ge=1, le=500),
):
    conn = db_connection()
    conditions = ["1=1"]
    params: dict = {"limit": limit, "min_amount": min_amount}

    if ticker:
        conditions.append("UPPER(ticker) = :ticker")
        params["ticker"] = ticker.upper()
    if agency:
        conditions.append("LOWER(agency) LIKE :agency")
        params["agency"] = f"%{agency.lower()}%"
    if min_amount > 0:
        conditions.append("amount >= :min_amount")

    order = "amount DESC" if sort == "amount" else "award_date DESC"
    where = " AND ".join(conditions)

    rows = conn.execute(
        f"""SELECT id, recipient_name, ticker, amount, agency, award_date, description, ingested_at
            FROM contracts WHERE {where}
            ORDER BY {order} LIMIT :limit""",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
