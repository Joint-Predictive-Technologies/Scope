"""
Lobbying dataset API — top spenders from the Senate LDA (populated by
scripts/ingest_lobbying.py). Powers the browsable Lobbying page beyond the
RULE_09 spike alerts.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from jpt_common import db_connection

router = APIRouter()


@router.get("/top")
def top_spenders(
    q: str = Query(default=""),
    category: str = Query(default=""),
    year: int | None = Query(default=None),
    foreign_only: bool = Query(default=False),
    limit: int = Query(default=60, ge=1, le=200),
):
    """Aggregated lobbying spend by client, most recent activity first."""
    conn = db_connection()

    conds = ["1=1"]
    params: list = []
    if q:
        conds.append("(client_name LIKE ? OR registrant_name LIKE ? OR issues LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    if category:
        conds.append("category = ?")
        params.append(category)
    if year:
        conds.append("filing_year = ?")
        params.append(year)
    if foreign_only:
        conds.append("is_foreign = 1")
    where = " AND ".join(conds)

    rows = conn.execute(
        f"""
        SELECT client_name,
               MAX(category)               AS category,
               MAX(ticker)                 AS ticker,
               MAX(is_foreign)             AS is_foreign,
               SUM(amount)                 AS total_amount,
               COUNT(*)                    AS filing_count,
               MAX(filing_year)            AS latest_year,
               MAX(issues)                 AS issues,
               MAX(document_url)           AS document_url
        FROM lobbying_filings
        WHERE {where}
        GROUP BY client_name
        ORDER BY total_amount DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/summary")
def summary():
    """Totals by category + overall, for the page header strip."""
    conn = db_connection()
    by_cat = [
        {"category": r[0], "total": r[1] or 0, "clients": r[2]}
        for r in conn.execute(
            """
            SELECT category, SUM(amount), COUNT(DISTINCT client_name)
            FROM lobbying_filings
            GROUP BY category
            ORDER BY SUM(amount) DESC
            """
        ).fetchall()
    ]
    overall = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT client_name), SUM(amount), "
        "SUM(CASE WHEN is_foreign=1 THEN 1 ELSE 0 END) FROM lobbying_filings"
    ).fetchone()
    conn.close()
    return {
        "filings":       overall[0] or 0,
        "clients":       overall[1] or 0,
        "total_spend":   overall[2] or 0,
        "foreign_filings": overall[3] or 0,
        "by_category":   by_cat,
    }
