from __future__ import annotations

import os
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jpt_common import db_connection


router = APIRouter()

GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_CONTEXT_ALERTS = 20

SYSTEM_PROMPT = """You are Scope, an AI political intelligence analyst for macro and event-driven investors.
You have access to live data including congressional trading disclosures, SEC insider filings,
Federal Register proposals, Senate lobbying records, Polymarket signals, and federal government contracts.
You surface political and alternative data signals that move markets.
You do not give investment advice. You describe, contextualize, and surface.
Be concise, specific, and cite the data you're drawing from."""


class ChatRequest(BaseModel):
    message: str
    days: int = 30


def _extract_tickers(text: str) -> list[str]:
    return [m.lstrip("$") for m in re.findall(r"\$?[A-Z]{2,5}", text)]


def _fetch_context(message: str, days: int) -> str:
    conn = db_connection()

    tickers = _extract_tickers(message)

    if tickers:
        placeholders = ",".join("?" * len(tickers))
        like_clauses = " OR ".join(f"ticker LIKE ?" for _ in tickers)
        like_params = [f"%{t}%" for t in tickers]
        rows = conn.execute(
            f"""
            SELECT rule, ticker, severity, headline, detail, created_at
            FROM alerts
            WHERE ({like_clauses})
              AND datetime(created_at) >= datetime('now', ?)
            ORDER BY datetime(created_at) DESC
            LIMIT {MAX_CONTEXT_ALERTS}
            """,
            like_params + [f"-{days} days"],
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT rule, ticker, severity, headline, detail, created_at
            FROM alerts
            WHERE datetime(created_at) >= datetime('now', ?)
            ORDER BY
                CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END,
                datetime(created_at) DESC
            LIMIT {MAX_CONTEXT_ALERTS}
            """,
            (f"-{days} days",),
        ).fetchall()

    conn.close()

    if not rows:
        return "No recent alerts found in the database for this query."

    lines = []
    for r in rows:
        detail = r["detail"] or ""
        entry = f"[{r['created_at'][:10]}] {r['rule']} | {r['severity']} | {r['ticker'] or '—'} | {r['headline']}"
        if detail:
            entry += f"\n  → {detail}"
        lines.append(entry)

    return "\n\n".join(lines)


@router.post("")
def chat(req: ChatRequest):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return JSONResponse(
            status_code=503,
            content={"error": "GROQ_API_KEY not configured on server."},
        )

    context = _fetch_context(req.message, req.days)

    prompt = f"""Here are the most relevant recent signals from the Scope database:

{context}

User question: {req.message}"""

    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=512,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        answer = completion.choices[0].message.content.strip()
        return {"answer": answer, "context_alerts": len(context.split("\n\n"))}

    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
