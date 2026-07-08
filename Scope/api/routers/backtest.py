from __future__ import annotations

from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection

router = APIRouter()

_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def _fetch_and_cache(alert_id: int, ticker: str, created_at: str) -> dict | None:
    """Fetch price-at-alert, +7d, +30d from Yahoo Finance and cache in backtest_results."""
    conn = db_connection()
    try:
        dt = datetime.fromisoformat(created_at.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        p1 = int(dt.timestamp())
        p2 = p1 + 32 * 86400

        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?interval=1d&period1={p1}&period2={p2}"
        )
        r = requests.get(url, headers=_YF_HEADERS, timeout=8)
        data = r.json()

        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]

        if len(closes) < 2:
            conn.close()
            return None

        price_at  = round(closes[0], 4)
        price_7d  = round(closes[min(7,  len(closes) - 1)], 4)
        price_30d = round(closes[min(30, len(closes) - 1)], 4)
        ret_30d   = round(((price_30d - price_at) / price_at) * 100, 2)

        conn.execute(
            """INSERT OR REPLACE INTO backtest_results
               (alert_id, ticker, price_at, price_7d, price_30d, return_30d)
               VALUES (?,?,?,?,?,?)""",
            (alert_id, ticker, price_at, price_7d, price_30d, ret_30d),
        )
        conn.commit()
        conn.close()
        return {
            "alert_id": alert_id, "ticker": ticker,
            "price_at": price_at, "price_7d": price_7d,
            "price_30d": price_30d, "return_30d": ret_30d,
        }
    except Exception as exc:
        conn.close()
        return None


# ── data ──────────────────────────────────────────────────────────────────────

@router.get("/data")
def get_backtest_data(
    days:  int = Query(default=90, ge=7, le=365),
    rule:  str = Query(default=""),
    limit: int = Query(default=150, ge=1, le=500),
):
    conn = db_connection()
    params: list = [f"-{days} days"]
    extra = ""
    if rule:
        extra = " AND a.rule = ?"
        params.append(rule.upper())
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT a.id, a.rule, a.severity, a.headline, a.ticker, a.created_at,
               b.price_at, b.price_7d, b.price_30d, b.return_30d
        FROM alerts a
        LEFT JOIN backtest_results b ON a.id = b.alert_id
        WHERE datetime(a.created_at) >= datetime('now', ?)
          AND a.ticker IS NOT NULL AND a.ticker != ''
          AND a.ticker NOT LIKE '% %'
          {extra}
        ORDER BY a.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    calculated = [r for r in rows if r["return_30d"] is not None]
    avg_ret = (
        round(sum(r["return_30d"] for r in calculated) / len(calculated), 2)
        if calculated else None
    )
    by_rule: dict[str, list[float]] = {}
    for r in calculated:
        by_rule.setdefault(r["rule"], []).append(r["return_30d"])
    rule_avgs = {k: round(sum(v) / len(v), 2) for k, v in by_rule.items()}

    conn.close()
    return {
        "alerts": [dict(r) for r in rows],
        "stats": {
            "total":         len(rows),
            "calculated":    len(calculated),
            "avg_return_30d": avg_ret,
            "by_rule":        rule_avgs,
        },
    }


# ── calculate a single alert's price performance ──────────────────────────────

@router.post("/calculate/{alert_id}")
def calculate_alert(alert_id: int):
    conn = db_connection()

    # Return cached result if it already exists
    cached = conn.execute(
        "SELECT * FROM backtest_results WHERE alert_id = ?", (alert_id,)
    ).fetchone()
    if cached:
        conn.close()
        return dict(cached)

    alert = conn.execute(
        "SELECT id, ticker, created_at FROM alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    conn.close()

    if not alert:
        return JSONResponse(status_code=404, content={"error": "Alert not found"})

    ticker = (alert["ticker"] or "").replace("$", "").split()[0]
    if not ticker:
        return JSONResponse(status_code=400, content={"error": "Alert has no ticker"})

    result = _fetch_and_cache(alert_id, ticker, alert["created_at"])
    if result:
        return result
    return JSONResponse(status_code=502, content={"error": "Could not fetch price data from Yahoo Finance"})
