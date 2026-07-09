from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from jpt_common import db_connection

router = APIRouter()

RULES = [
    "RULE_01B", "RULE_02", "RULE_06", "RULE_07", "RULE_08", "RULE_09",
    "RULE_10", "RULE_11", "RULE_ANOMALY", "RULE_REDDIT", "RULE_OSINT",
]


def _outcome(ret: float | None) -> str:
    if ret is None:
        return "PENDING"
    if ret >= 10:
        return "STRONG"
    if ret >= 0:
        return "POSITIVE"
    if ret >= -5:
        return "FLAT"
    return "NEGATIVE"


@router.get("/data")
def get_history(
    q:           str = Query(default=""),
    ticker:      str = Query(default=""),
    member:      str = Query(default=""),
    rule:        str = Query(default=""),
    severity:    str = Query(default=""),
    from_date:   str = Query(default=""),
    to_date:     str = Query(default=""),
    has_backtest: bool | None = Query(default=None),
    outcome:     str = Query(default=""),
    page:        int = Query(default=1, ge=1),
    limit:       int = Query(default=50, ge=1, le=200),
    days:        int = Query(default=90, ge=1, le=3650),
):
    conn = db_connection()
    conditions = ["1=1"]
    params: list = []

    if q:
        conditions.append("(a.headline LIKE ? OR a.detail LIKE ? OR a.ticker LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if ticker:
        conditions.append("UPPER(a.ticker) = ?")
        params.append(ticker.upper())
    if member:
        conditions.append("(a.member_id = ? OR a.detail LIKE ?)")
        params.extend([member, f"%{member}%"])
    if rule:
        conditions.append("a.rule = ?")
        params.append(rule.upper())
    if severity:
        conditions.append("a.severity = ?")
        params.append(severity.upper())
    if from_date:
        conditions.append("date(a.created_at) >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("date(a.created_at) <= ?")
        params.append(to_date)
    if not from_date and not to_date:
        conditions.append("datetime(a.created_at) >= datetime('now', ?)")
        params.append(f"-{days} days")
    if has_backtest is True:
        conditions.append("b.alert_id IS NOT NULL")
    elif has_backtest is False:
        conditions.append("b.alert_id IS NULL")

    where = " AND ".join(conditions)

    total_row = conn.execute(
        f"""SELECT COUNT(*) FROM alerts a
            LEFT JOIN backtest_results b ON a.id = b.alert_id
            WHERE {where}""",
        params,
    ).fetchone()
    total = total_row[0] if total_row else 0

    offset = (page - 1) * limit
    rows = conn.execute(
        f"""SELECT a.id, a.rule, a.severity, a.headline, a.detail, a.ticker,
                   a.tags, a.created_at, a.member_id,
                   b.return_7d, b.return_30d, b.price_at
            FROM alerts a
            LEFT JOIN backtest_results b ON a.id = b.alert_id
            WHERE {where}
            ORDER BY datetime(a.created_at) DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        d["outcome"] = _outcome(d.get("return_30d"))
        results.append(d)

    # Apply outcome filter after enriching
    if outcome:
        results = [r for r in results if r["outcome"] == outcome.upper()]

    return {
        "data":       results,
        "total":      total,
        "page":       page,
        "pages":      max(1, -(-total // limit)),
        "limit":      limit,
    }


@router.get("/export")
def export_history(
    q:         str = Query(default=""),
    ticker:    str = Query(default=""),
    rule:      str = Query(default=""),
    severity:  str = Query(default=""),
    from_date: str = Query(default=""),
    to_date:   str = Query(default=""),
    days:      int = Query(default=90, ge=1, le=3650),
):
    conn = db_connection()
    conditions = ["1=1"]
    params: list = []

    if q:
        conditions.append("(a.headline LIKE ? OR a.ticker LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    if ticker:
        conditions.append("UPPER(a.ticker) = ?")
        params.append(ticker.upper())
    if rule:
        conditions.append("a.rule = ?")
        params.append(rule.upper())
    if severity:
        conditions.append("a.severity = ?")
        params.append(severity.upper())
    if from_date:
        conditions.append("date(a.created_at) >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("date(a.created_at) <= ?")
        params.append(to_date)
    if not from_date and not to_date:
        conditions.append("datetime(a.created_at) >= datetime('now', ?)")
        params.append(f"-{days} days")

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"""SELECT a.id, a.rule, a.severity, a.ticker, a.headline, a.created_at,
                   b.return_7d, b.return_30d
            FROM alerts a
            LEFT JOIN backtest_results b ON a.id = b.alert_id
            WHERE {where}
            ORDER BY datetime(a.created_at) DESC
            LIMIT 5000""",
        params,
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "date", "rule", "severity", "ticker", "headline", "return_7d", "return_30d", "outcome"])
    for r in rows:
        ret30 = r["return_30d"]
        writer.writerow([
            r["id"], r["created_at"], r["rule"], r["severity"],
            r["ticker"] or "", r["headline"] or "",
            f"{r['return_7d']:.2f}" if r["return_7d"] is not None else "",
            f"{ret30:.2f}" if ret30 is not None else "",
            _outcome(ret30),
        ])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=scope_history.csv"},
    )
