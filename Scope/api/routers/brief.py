from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from jpt_common import db_connection

router = APIRouter()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _get_brief(date: str) -> dict | None:
    conn = db_connection()
    row = conn.execute(
        "SELECT date, content_json, generated_at FROM daily_briefs WHERE date = ?", (date,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


@router.get("/data")
def get_brief():
    """Return today's brief; fall back to yesterday's if not yet generated."""
    today = _today()
    brief = _get_brief(today)
    if brief:
        return {**brief, "is_today": True}

    yesterday = _yesterday()
    brief = _get_brief(yesterday)
    if brief:
        return {**brief, "is_today": False, "note": "Today's brief is generating…"}

    return {"is_today": False, "note": "No brief available yet.", "content_json": None}


@router.post("/generate")
def generate_brief(force: bool = False):
    """Manually trigger brief generation. Admin use."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return JSONResponse(status_code=503, content={"error": "GROQ_API_KEY not configured"})

    today = _today()
    if not force:
        existing = _get_brief(today)
        if existing:
            return {**existing, "cached": True}

    try:
        import sys, os as _os
        sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))))
        from scripts.generate_brief import generate
        result = generate(date_str=today, days=1)
        return {**result, "cached": False}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
