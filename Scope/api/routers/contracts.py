from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection

router = APIRouter()

CODE_DIR = Path(__file__).resolve().parent.parent.parent


@router.post("/refresh")
def refresh_contracts():
    """Run rule_11_contracts.py immediately to ingest latest USASpending data."""
    script = CODE_DIR / "scripts" / "rule_11_contracts.py"
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, cwd=str(CODE_DIR),
    )
    if r.returncode != 0:
        return JSONResponse(status_code=500, content={"error": r.stderr[-500:]})
    lines = [l for l in r.stdout.splitlines() if "[RULE_11]" in l]

    # Remove any legacy RULE_11 alerts that were emitted before this change
    conn = db_connection()
    deleted = conn.execute("DELETE FROM alerts WHERE rule = 'RULE_11'").rowcount
    conn.commit()
    conn.close()

    return {"ok": True, "deleted_alerts": deleted, "output": lines}


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
