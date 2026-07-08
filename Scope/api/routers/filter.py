from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jpt_common import db_connection

router = APIRouter()

GROQ_MODEL = "llama-3.3-70b-versatile"


class FilterRequest(BaseModel):
    thesis: str
    days: int = 30
    limit: int = 60


@router.post("")
def filter_by_thesis(req: FilterRequest):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return JSONResponse(status_code=503, content={"error": "GROQ_API_KEY not set"})

    conn = db_connection()
    rows = conn.execute(
        """
        SELECT id, rule, ticker, severity, headline, detail, tags, member_id, created_at
        FROM alerts
        WHERE datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC
        LIMIT ?
        """,
        (f"-{req.days} days", req.limit),
    ).fetchall()
    conn.close()

    if not rows:
        return {"thesis": req.thesis, "results": [], "total_scored": 0}

    alert_lines = []
    for r in rows:
        alert_lines.append(
            f'ID:{r["id"]} [{r["rule"]}] {r["ticker"] or ""} — {r["headline"]}'
        )

    prompt = f"""You are scoring political intelligence alerts for relevance to an investment thesis.

Thesis: "{req.thesis}"

Score each alert 0-10 for relevance. 10 = directly about this thesis, 0 = unrelated.
Return ONLY a valid JSON array, nothing else:
[{{"id": 123, "score": 7}}, {{"id": 456, "score": 2}}, ...]

Alerts to score:
{chr(10).join(alert_lines)}"""

    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=1024,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = completion.choices[0].message.content.strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return JSONResponse(
                status_code=500,
                content={"error": "LLM returned unparseable response"},
            )
        scores = json.loads(match.group())
        score_map = {s["id"]: s["score"] for s in scores}

        row_map = {r["id"]: dict(r) for r in rows}
        results = []
        for s in sorted(scores, key=lambda x: x["score"], reverse=True):
            if s["score"] >= 5 and s["id"] in row_map:
                row = row_map[s["id"]]
                row["relevance_score"] = s["score"]
                results.append(row)

        return {"thesis": req.thesis, "results": results, "total_scored": len(scores)}

    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
