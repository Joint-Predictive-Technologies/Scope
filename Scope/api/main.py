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

from api.routers import alerts, chat, members, tickers

STATIC_DIR = Path(__file__).parent / "static"
CODE_DIR   = Path(__file__).resolve().parent.parent

LIVE_RULES = [
    "rule_06_form4.py",
    "rule_07_polymarket.py",
    "rule_08_federal_register.py",
    "rule_09_lobbying.py",
    "scripts/rule_10_corroboration.py",
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
        print(f"[scheduler] running live rules …", flush=True)
        await asyncio.to_thread(_run_rules, LIVE_RULES)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _alert_count() == 0:
        print("[startup] DB empty — seeding now …", flush=True)
        await asyncio.to_thread(_run_rules, LIVE_RULES)
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

app.include_router(alerts.router,  prefix="/alerts",  tags=["Alerts"])
app.include_router(chat.router,    prefix="/chat",    tags=["Chat"])
app.include_router(members.router, prefix="/members", tags=["Members"])
app.include_router(tickers.router, prefix="/tickers", tags=["Tickers"])


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
