#!/usr/bin/env python3
"""
Scope API — FastAPI wrapper around jpt.db.
Run from the Scope/ directory:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from api.routers import alerts, chat, members, tickers

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Scope Political Intelligence API",
    description="Real-time alerts on congressional trading, insider activity, lobbying, and regulatory proposals.",
    version="1.0.0",
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


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/feed", response_class=HTMLResponse, include_in_schema=False)
def feed():
    return FileResponse(STATIC_DIR / "alerts.html")

@app.get("/ask", response_class=HTMLResponse, include_in_schema=False)
def ask():
    return FileResponse(STATIC_DIR / "chat.html")
