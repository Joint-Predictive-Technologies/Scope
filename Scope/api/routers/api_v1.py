"""
Scope public REST API — v1.

Read-only, API-key authenticated access to the alert database for power users
and downstream integrations. All list endpoints return a standard envelope:

    {"data": [...], "total": N, "page": 1, "per_page": 50}

Auth: pass the key as the `X-API-Key` header or `?api_key=` query param.

Keys are minted by an admin (guarded by ADMIN_KEY):
    POST /api/v1/keys?key=ADMIN_KEY&name=my-integration
An initial key can also be seeded from the SCOPE_API_SEED_KEY env var.
"""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from jpt_common import db_connection, SECTOR_MAP, calculate_heat_index

router = APIRouter()

_NOISY_RULES = ("RULE_07", "RULE_OSINT", "RULE_REDDIT", "RULE_ANOMALY")


def _ensure_api_keys_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            key           TEXT PRIMARY KEY,
            name          TEXT,
            created_at    TEXT DEFAULT (datetime('now')),
            last_used     TEXT,
            request_count INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    # Seed an initial key from env once, if provided and not already present.
    seed = os.getenv("SCOPE_API_SEED_KEY", "").strip()
    if seed:
        exists = conn.execute("SELECT 1 FROM api_keys WHERE key = ?", (seed,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO api_keys (key, name) VALUES (?, 'env-seed')", (seed,)
            )
            conn.commit()


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    api_key: str | None = Query(default=None),
) -> str:
    """FastAPI dependency — validates the key and records usage."""
    key = x_api_key or api_key
    conn = db_connection()
    _ensure_api_keys_table(conn)
    if not key:
        conn.close()
        return JSONResponse(status_code=401, content={"error": "API key required"})  # type: ignore[return-value]
    row = conn.execute("SELECT key FROM api_keys WHERE key = ?", (key,)).fetchone()
    if not row:
        conn.close()
        return JSONResponse(status_code=403, content={"error": "Invalid API key"})  # type: ignore[return-value]
    conn.execute(
        "UPDATE api_keys SET last_used = datetime('now'), request_count = request_count + 1 "
        "WHERE key = ?",
        (key,),
    )
    conn.commit()
    conn.close()
    return key


def _guard(auth) -> JSONResponse | None:
    """The dependency returns a JSONResponse on failure; surface it if so."""
    return auth if isinstance(auth, JSONResponse) else None


@router.post("/keys", include_in_schema=False)
def create_key(key: str = Query(...), name: str = Query(default="unnamed")):
    """Mint a new API key. Guarded by ADMIN_KEY (?key=...)."""
    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key or key != admin_key:
        return JSONResponse(status_code=403, content={"error": "Invalid or missing admin key"})
    new_key = "scope_" + secrets.token_hex(24)
    conn = db_connection()
    _ensure_api_keys_table(conn)
    conn.execute("INSERT INTO api_keys (key, name) VALUES (?, ?)", (new_key, name))
    conn.commit()
    conn.close()
    return {"key": new_key, "name": name}


@router.get("/alerts")
def v1_alerts(
    auth=Depends(require_api_key),
    severity: str | None = Query(default=None),
    rule: str | None = Query(default=None),
    ticker: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
):
    if (err := _guard(auth)):
        return err
    conn = db_connection()
    conds = ["datetime(created_at) >= datetime('now', ?)"]
    params: list = [f"-{days} days"]
    if severity:
        conds.append("severity = ?")
        params.append(severity.upper())
    if rule:
        conds.append("rule = ?")
        params.append(rule.upper())
    if ticker:
        conds.append("ticker LIKE ?")
        params.append(f"%{ticker.upper()}%")
    where = " AND ".join(conds)

    total = conn.execute(f"SELECT COUNT(*) FROM alerts WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT id, rule, ticker, severity, headline, detail, why_matters,
               member_id, created_at, source_url
        FROM alerts WHERE {where}
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    conn.close()
    return {"data": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}


@router.get("/alerts/{alert_id}")
def v1_alert(alert_id: int, auth=Depends(require_api_key)):
    if (err := _guard(auth)):
        return err
    conn = db_connection()
    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    conn.close()
    if not row:
        return JSONResponse(status_code=404, content={"error": "Alert not found"})
    return {"data": dict(row)}


@router.get("/ticker/{symbol}/signals")
def v1_ticker_signals(
    symbol: str,
    auth=Depends(require_api_key),
    days: int = Query(default=90, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
):
    if (err := _guard(auth)):
        return err
    sym = symbol.upper().replace("$", "")
    conn = db_connection()
    where = "ticker LIKE ? AND datetime(created_at) >= datetime('now', ?)"
    params = [f"%{sym}%", f"-{days} days"]
    total = conn.execute(f"SELECT COUNT(*) FROM alerts WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT id, rule, ticker, severity, headline, detail, created_at
        FROM alerts WHERE {where}
        ORDER BY datetime(created_at) DESC LIMIT ? OFFSET ?
        """,
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    conn.close()
    return {"data": [dict(r) for r in rows], "total": total, "page": page,
            "per_page": per_page, "ticker": sym}


@router.get("/member/{bioguide_id}/trades")
def v1_member_trades(
    bioguide_id: str,
    auth=Depends(require_api_key),
    days: int = Query(default=90, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
):
    if (err := _guard(auth)):
        return err
    conn = db_connection()
    where = "t.member_id = ? AND t.transaction_date >= date('now', ?)"
    params = [bioguide_id, f"-{days} days"]
    total = conn.execute(
        f"SELECT COUNT(*) FROM transactions t WHERE {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT t.id, t.raw_ticker_string AS ticker, t.transaction_type,
               t.amount_band, t.transaction_date, t.filing_date
        FROM transactions t
        WHERE {where}
        ORDER BY t.transaction_date DESC LIMIT ? OFFSET ?
        """,
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    conn.close()
    return {"data": [dict(r) for r in rows], "total": total, "page": page,
            "per_page": per_page, "member_id": bioguide_id}


@router.get("/heat-index")
def v1_heat_index(auth=Depends(require_api_key), days: int = Query(default=30, ge=1, le=90)):
    if (err := _guard(auth)):
        return err
    conn = db_connection()
    data = []
    for sector in SECTOR_MAP:
        h = calculate_heat_index(sector, conn, days=days)
        h["sector"] = sector
        data.append(h)
    conn.close()
    data.sort(key=lambda x: x["score"], reverse=True)
    return {"data": data, "total": len(data), "page": 1, "per_page": len(data)}


@router.get("/corroborations")
def v1_corroborations(
    auth=Depends(require_api_key),
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
):
    if (err := _guard(auth)):
        return err
    conn = db_connection()
    where = "rule = 'RULE_10' AND datetime(created_at) >= datetime('now', ?)"
    params = [f"-{days} days"]
    total = conn.execute(f"SELECT COUNT(*) FROM alerts WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT id, ticker, severity, headline, detail, why_matters, tags, created_at
        FROM alerts WHERE {where}
        ORDER BY datetime(created_at) DESC LIMIT ? OFFSET ?
        """,
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    conn.close()
    return {"data": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}
