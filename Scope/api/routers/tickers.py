from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection
from api.receipts import build_receipts

router = APIRouter()

_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _fetch_market_cap(symbol: str) -> int | None:
    try:
        url = (
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
            "?modules=summaryDetail"
        )
        r = requests.get(url, headers=_YF_HEADERS, timeout=6)
        data = r.json()
        raw = (
            data["quoteSummary"]["result"][0]["summaryDetail"]
            .get("marketCap", {})
            .get("raw")
        )
        return int(raw) if raw else None
    except Exception:
        return None


def _fetch_social_spike(symbol: str) -> bool:
    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
        r = requests.get(url, timeout=6)
        messages = r.json().get("messages", [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent = [
            m for m in messages
            if datetime.fromisoformat(
                m["created_at"].replace("Z", "+00:00")
            ) >= cutoff
        ]
        return len(recent) >= 5
    except Exception:
        return False


def _stale(ts_str: str | None, hours: float = 24) -> bool:
    if not ts_str:
        return True
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt) >= timedelta(hours=hours)
    except Exception:
        return True


# ── watchlist ─────────────────────────────────────────────────────────────────

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


# ── leaderboard ───────────────────────────────────────────────────────────────

@router.get("/leaderboard")
def get_leaderboard(days: int = Query(default=30, ge=1, le=90)):
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


# ── ticker list ───────────────────────────────────────────────────────────────

@router.get("")
def get_tickers(limit: int = Query(default=200, ge=1, le=1000)):
    conn = db_connection()
    rows = conn.execute(
        "SELECT symbol, company_name FROM tickers ORDER BY symbol LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── meta (market cap + social) ────────────────────────────────────────────────

@router.get("/{symbol}/meta")
def get_ticker_meta(symbol: str):
    symbol = symbol.upper()
    conn = db_connection()
    row = conn.execute(
        "SELECT * FROM ticker_meta WHERE symbol = ?", (symbol,)
    ).fetchone()
    now = datetime.now(timezone.utc).isoformat()

    if row and not _stale(row["cap_updated"]):
        conn.close()
        return dict(row)

    market_cap   = _fetch_market_cap(symbol)
    social_spike = _fetch_social_spike(symbol)

    if row:
        conn.execute(
            """UPDATE ticker_meta
               SET market_cap=?, cap_updated=?, social_spike=?, social_at=?
               WHERE symbol=?""",
            (market_cap, now, int(social_spike), now, symbol),
        )
    else:
        conn.execute(
            """INSERT INTO ticker_meta (symbol, market_cap, cap_updated, social_spike, social_at)
               VALUES (?,?,?,?,?)""",
            (symbol, market_cap, now, int(social_spike), now),
        )
    conn.commit()

    result = conn.execute(
        "SELECT * FROM ticker_meta WHERE symbol = ?", (symbol,)
    ).fetchone()
    conn.close()
    return dict(result) if result else {
        "symbol": symbol, "market_cap": market_cap,
        "social_spike": int(social_spike), "cap_updated": now,
    }


# ── price action during disclosure window ─────────────────────────────────────

@router.get("/{symbol}/price-action")
def get_price_action(
    symbol: str,
    start: str = Query(description="Transaction date YYYY-MM-DD"),
    end:   str = Query(description="Filing date YYYY-MM-DD"),
):
    symbol = symbol.upper()
    conn = db_connection()

    cached = conn.execute(
        "SELECT * FROM price_action WHERE symbol=? AND start_date=? AND end_date=?",
        (symbol, start, end),
    ).fetchone()
    if cached:
        conn.close()
        return dict(cached)

    try:
        p1 = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
        p2 = int(datetime.strptime(end,   "%Y-%m-%d").timestamp()) + 86400

        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?interval=1d&period1={p1}&period2={p2}"
        )
        r = requests.get(url, headers=_YF_HEADERS, timeout=8)
        data = r.json()

        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]

        if len(closes) < 2:
            conn.close()
            return {"symbol": symbol, "start_date": start, "end_date": end,
                    "pct_change": None, "start_price": None, "end_price": None}

        s_price = round(closes[0], 4)
        e_price = round(closes[-1], 4)
        pct     = round(((e_price - s_price) / s_price) * 100, 2)

        conn.execute(
            """INSERT OR REPLACE INTO price_action
               (symbol, start_date, end_date, start_price, end_price, pct_change)
               VALUES (?,?,?,?,?,?)""",
            (symbol, start, end, s_price, e_price, pct),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM price_action WHERE symbol=? AND start_date=? AND end_date=?",
            (symbol, start, end),
        ).fetchone()
        conn.close()
        return dict(row)

    except Exception as exc:
        conn.close()
        return {"symbol": symbol, "start_date": start, "end_date": end,
                "pct_change": None, "error": str(exc)[:120]}


# ── ticker detail — alerts + related members + transactions ───────────────────

@router.get("/{symbol}/alerts")
def get_ticker_alerts(
    symbol: str,
    days:  int = Query(default=365, ge=1, le=730),
    limit: int = Query(default=200, ge=1, le=500),
):
    sym = symbol.upper()
    conn = db_connection()

    alerts = conn.execute(
        """
        SELECT id, rule, severity, headline, detail, tags, ticker, member_id, created_at,
               lifecycle_stage, source_url, verify_url, theme_id
        FROM alerts
        WHERE ticker LIKE ?
          AND datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC
        LIMIT ?
        """,
        (f"%{sym}%", f"-{days} days", limit),
    ).fetchall()

    members = conn.execute(
        """
        SELECT DISTINCT m.bioguide_id, m.full_name, m.party, m.state
        FROM transactions t
        JOIN members m ON t.member_id = m.bioguide_id
        WHERE t.raw_ticker_string LIKE ?
        LIMIT 20
        """,
        (f"%{sym}%",),
    ).fetchall()

    transactions = conn.execute(
        """
        SELECT
            t.member_id,
            m.full_name,
            t.transaction_date,
            t.filing_date,
            t.transaction_type,
            t.amount_band,
            CAST(julianday(t.filing_date) - julianday(t.transaction_date) AS INTEGER) AS filing_delay
        FROM transactions t
        JOIN members m ON t.member_id = m.bioguide_id
        WHERE t.raw_ticker_string LIKE ?
        ORDER BY t.transaction_date DESC
        LIMIT 100
        """,
        (f"%{sym}%",),
    ).fetchall()

    alert_dicts = []
    for r in alerts:
        d = dict(r)
        d["receipts"] = build_receipts(d, conn)
        alert_dicts.append(d)
    conn.close()
    return {
        "symbol": sym,
        "alerts": alert_dicts,
        "related_members": [dict(r) for r in members],
        "transactions": [dict(r) for r in transactions],
    }
