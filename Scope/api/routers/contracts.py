from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection

router = APIRouter()

CODE_DIR = Path(__file__).resolve().parent.parent.parent

_refreshing = False


@router.post("/refresh")
def refresh_contracts():
    """Run rule_11_contracts.py to ingest latest USASpending data (synchronous, ~30s)."""
    global _refreshing
    if _refreshing:
        return JSONResponse(status_code=409, content={"error": "Refresh already in progress"})
    _refreshing = True
    script = CODE_DIR / "scripts" / "rule_11_contracts.py"
    try:
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, cwd=str(CODE_DIR),
            timeout=120,
        )
        if r.returncode != 0:
            return JSONResponse(status_code=500, content={"error": r.stderr[-500:]})
        lines = [l for l in r.stdout.splitlines() if "[RULE_11]" in l]
        return {"ok": True, "output": lines}
    except subprocess.TimeoutExpired:
        return JSONResponse(status_code=504, content={"error": "Script timed out after 120s"})
    finally:
        _refreshing = False


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
        f"""SELECT id, recipient_name, ticker, amount, agency, award_date, award_id, description, ingested_at
            FROM contracts WHERE {where}
            ORDER BY {order} LIMIT :limit""",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
