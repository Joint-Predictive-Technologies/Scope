#!/usr/bin/env python3
"""
Scope API — FastAPI wrapper around jpt.db.
Run from the Scope/ directory:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import sys
import os
import asyncio
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse

from api.routers import (
    alerts, chat, members, tickers,
    filter as filter_router,
    social, backtest, sectors, digest,
)

STATIC_DIR = Path(__file__).parent / "static"
CODE_DIR   = Path(__file__).resolve().parent.parent

LIVE_RULES = [
    "rule_06_form4.py",
    "rule_07_polymarket.py",
    "rule_08_federal_register.py",
    "rule_09_lobbying.py",
    "scripts/rule_01b_first_touch.py",
    "scripts/rule_anomaly.py",
    "scripts/rule_10_corroboration.py",
    "scripts/telegram_bot.py",
]
REFRESH_INTERVAL_HOURS = 4


def _alert_count() -> int:
    import sqlite3
    default_db = CODE_DIR / "data" / "jpt.db"
    db_path = os.getenv("DATABASE_PATH") or str(default_db)
    try:
        conn = sqlite3.connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return -1


def _hours_since_last_alert() -> float:
    import sqlite3
    from datetime import datetime, timezone
    default_db = CODE_DIR / "data" / "jpt.db"
    db_path = os.getenv("DATABASE_PATH") or str(default_db)
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT MAX(created_at) FROM alerts").fetchone()
        conn.close()
        if not row or not row[0]:
            return float("inf")
        last = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600
    except Exception:
        return float("inf")


def _run_rules(rules: list[str]) -> dict[str, str]:
    results = {}
    for rule in rules:
        r = subprocess.run(
            [sys.executable, rule, "--emit-alerts"],
            capture_output=True, text=True,
            cwd=str(CODE_DIR),
        )
        results[rule] = "ok" if r.returncode == 0 else r.stderr[-300:].strip()
        print(f"[rules] {rule}: {results[rule]}", flush=True)
    return results


async def _refresh_loop():
    """Background task: re-run all live rules every REFRESH_INTERVAL_HOURS."""
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_HOURS * 3600)
        print("[scheduler] running live rules …", flush=True)
        await asyncio.to_thread(_run_rules, LIVE_RULES)


@asynccontextmanager
async def lifespan(app: FastAPI):
    hours_stale = _hours_since_last_alert()
    if hours_stale >= REFRESH_INTERVAL_HOURS:
        print(f"[startup] data is {hours_stale:.1f}h old — background refresh starting …", flush=True)
        asyncio.create_task(asyncio.to_thread(_run_rules, LIVE_RULES))
    else:
        print(f"[startup] data is fresh ({hours_stale:.1f}h old) — skipping seed", flush=True)
    asyncio.create_task(_refresh_loop())
    yield


app = FastAPI(
    title="Scope Political Intelligence API",
    description="Real-time alerts on congressional trading, insider activity, lobbying, and regulatory proposals.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(alerts.router,        prefix="/alerts",   tags=["Alerts"])
app.include_router(chat.router,          prefix="/chat",     tags=["Chat"])
app.include_router(members.router,       prefix="/members",  tags=["Members"])
app.include_router(tickers.router,       prefix="/tickers",  tags=["Tickers"])
app.include_router(filter_router.router, prefix="/filter",   tags=["Filter"])
app.include_router(social.router,        prefix="/social",   tags=["Social"])
app.include_router(backtest.router,      prefix="/backtest", tags=["Backtest"])
app.include_router(sectors.router,       prefix="/sectors",  tags=["Sectors"])
app.include_router(digest.router,        prefix="/digest",   tags=["Digest"])


@app.get("/health", tags=["Health"])
def health():
    import sqlite3
    from pathlib import Path
    default_db = Path(__file__).resolve().parent.parent / "data" / "jpt.db"
    db_path = os.getenv("DATABASE_PATH") or str(default_db)
    groq_set = bool(os.getenv("GROQ_API_KEY", "").strip())
    db_exists = Path(db_path).exists()
    alert_count = 0
    db_error = None
    if db_exists:
        try:
            conn = sqlite3.connect(db_path)
            alert_count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            conn.close()
        except Exception as e:
            db_error = str(e)
    return {
        "status": "ok",
        "db_path": db_path,
        "db_exists": db_exists,
        "db_error": db_error,
        "alert_count": alert_count,
        "groq_key_set": groq_set,
        "cwd": os.getcwd(),
    }


@app.get("/admin/refresh", tags=["Admin"])
def admin_refresh(key: str = ""):
    """Manually trigger all live rules. Pass ?key=ADMIN_KEY."""
    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key or key != admin_key:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"error": "Invalid or missing key"})
    results = _run_rules(LIVE_RULES)
    return {"status": "done", "alert_count": _alert_count(), "results": results}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/feed", response_class=HTMLResponse, include_in_schema=False)
def feed():
    return FileResponse(STATIC_DIR / "alerts.html")

@app.get("/ask", response_class=HTMLResponse, include_in_schema=False)
def ask():
    return FileResponse(STATIC_DIR / "chat.html")

@app.get("/members-list", response_class=HTMLResponse, include_in_schema=False)
def members_list_page():
    return FileResponse(STATIC_DIR / "members.html")

@app.get("/watchlist", response_class=HTMLResponse, include_in_schema=False)
def watchlist_page():
    return FileResponse(STATIC_DIR / "watchlist.html")

@app.get("/ticker/{symbol}", response_class=HTMLResponse, include_in_schema=False)
def ticker_page(symbol: str):
    return FileResponse(STATIC_DIR / "ticker.html")

@app.get("/member/{bioguide_id}", response_class=HTMLResponse, include_in_schema=False)
def member_page(bioguide_id: str):
    return FileResponse(STATIC_DIR / "member.html")

@app.get("/backtest", response_class=HTMLResponse, include_in_schema=False)
def backtest_page():
    return FileResponse(STATIC_DIR / "backtest.html")

@app.get("/sectors", response_class=HTMLResponse, include_in_schema=False)
def sectors_page():
    return FileResponse(STATIC_DIR / "sectors.html")

@app.get("/digest", response_class=HTMLResponse, include_in_schema=False)
def digest_page():
    return FileResponse(STATIC_DIR / "digest.html")
