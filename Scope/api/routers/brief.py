from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from jpt_common import db_connection

router = APIRouter()

_generating: set[str] = set()  # dates currently being generated


def _trigger_generation(date: str) -> None:
    if date in _generating:
        return
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return
    _generating.add(date)
    def _run():
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))))
            from scripts.generate_brief import generate
            generate(date_str=date, days=1)
        except Exception as e:
            print(f"[brief] auto-generate failed for {date}: {e}")
        finally:
            _generating.discard(date)
    threading.Thread(target=_run, daemon=True).start()


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
    """Return today's brief; auto-generate in background if missing."""
    today = _today()
    brief = _get_brief(today)
    if brief:
        return {**brief, "is_today": True, "generating": False}

    # Kick off background generation on first miss
    _trigger_generation(today)

    yesterday = _yesterday()
    brief = _get_brief(yesterday)
    if brief:
        return {**brief, "is_today": False, "generating": True,
                "note": "Today's brief is generating…"}

    return {"is_today": False, "generating": True,
            "note": "Generating today's brief…", "content_json": None}


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
