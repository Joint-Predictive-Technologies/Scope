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


_generation_failed: set[str] = set()  # dates that failed generation


def _trigger_generation(date: str) -> None:
    if date in _generating or date in _generation_failed:
        return
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        _generation_failed.add(date)
        return
    _generating.add(date)
    def _run():
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))))
            from scripts.generate_brief import generate
            generate(date_str=date, days=2)
        except Exception as e:
            print(f"[brief] auto-generate failed for {date}: {e}")
            _generation_failed.add(date)
        finally:
            _generating.discard(date)
    threading.Thread(target=_run, daemon=True).start()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _get_brief(date: str) -> dict | None:
    conn = db_connection()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_briefs)").fetchall()}
    extra = ", alert_ids, evidence_json" if "alert_ids" in cols else ""
    row = conn.execute(
        f"SELECT date, content_json, generated_at{extra} FROM daily_briefs WHERE date = ?",
        (date,),
    ).fetchone()
    if not row:
        conn.close()
        return None
    brief = dict(row)

    # Staleness: material new evidence (CRITICAL / RULE_10) since generation
    # that the brief did not cite.
    stale = False
    try:
        gen = brief.get("generated_at")
        cited = set(json.loads(brief.get("alert_ids") or "[]"))
        if gen:
            newer = conn.execute(
                """
                SELECT id FROM alerts
                WHERE datetime(created_at) > datetime(?)
                  AND (severity = 'CRITICAL' OR rule = 'RULE_10')
                """,
                (gen.replace("T", " ")[:19],),
            ).fetchall()
            material_new = [r[0] for r in newer if r[0] not in cited]
            stale = len(material_new) >= 3
    except Exception:
        pass
    conn.close()
    brief["stale"] = stale
    return brief


@router.get("/data")
def get_brief():
    """Return today's brief; auto-generate in background if missing."""
    today = _today()
    brief = _get_brief(today)
    if brief:
        return {**brief, "is_today": True, "generating": False}

    # Attempt background generation (no-op if already in progress, failed, or no key)
    _trigger_generation(today)

    # Whether generation is actively running or permanently failed
    failed = today in _generation_failed
    is_gen = today in _generating

    yesterday = _yesterday()
    brief = _get_brief(yesterday)
    if brief:
        return {**brief, "is_today": False,
                "generating": is_gen and not failed,
                "note": "Today's brief is generating…" if is_gen else None}

    return {
        "is_today": False,
        "generating": is_gen and not failed,
        "note": "Generating today's brief…" if is_gen else "No brief available yet.",
        "content_json": None,
    }


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
