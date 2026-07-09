from __future__ import annotations

import os
import re
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jpt_common import db_connection

router = APIRouter()

GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_CONTEXT_TOKENS = 3000  # approximate cap on context chars sent to Groq

SYSTEM_PROMPT = """You are Scope, an AI political intelligence analyst for macro and event-driven investors.
You have access to live data including congressional trading disclosures, SEC insider filings,
Federal Register proposals, Senate lobbying records, Polymarket signals, and federal government contracts.
You surface political and alternative data signals that move markets.
You do not give investment advice. You describe, contextualize, and surface.
Be concise, specific, and always cite specific signals from the database context provided.
If the context contains relevant signals, reference them directly — do not give generic answers."""

INVESTIGATION_LABEL = "Investigation Mode — Scope searches its full database before answering."

EXAMPLE_PROMPTS = [
    "Why is defense moving today?",
    "Explain everything affecting NVDA this week",
    "Who benefits if Taiwan tensions increase?",
    "Show me all congressional activity in semiconductors",
    "What's the strongest signal in the market right now?",
]

SECTOR_KEYWORDS_CHAT = {
    "defense": ["LMT", "RTX", "NOC", "GD", "BA", "HII", "LDOS", "SAIC", "CACI", "BAH"],
    "semiconductor": ["NVDA", "AMD", "INTC", "TSM", "AVGO", "QCOM", "ARM", "MU", "AMAT", "LRCX"],
    "tech": ["NVDA", "AAPL", "MSFT", "AMD", "INTC", "TSM", "AVGO", "QCOM"],
    "energy": ["XOM", "CVX", "COP", "USO", "XLE", "OXY", "SLB"],
    "pharma": ["JNJ", "PFE", "MRK", "ABBV", "LLY", "AMGN", "GILD", "REGN"],
    "bank": ["GS", "JPM", "MS", "BAC", "C", "WFC", "BLK"],
    "crypto": ["COIN", "MSTR", "MARA", "RIOT"],
    "taiwan": ["TSM", "NVDA", "AMD", "INTC", "AVGO"],
    "ai": ["NVDA", "MSFT", "GOOGL", "META", "AMD", "ARM"],
}


class ChatRequest(BaseModel):
    message: str
    days: int = 30


def _extract_tickers(text: str) -> list[str]:
    # Explicit $TICKER or bare ALL-CAPS 2-5 char words
    return list({m.lstrip("$") for m in re.findall(r"\$[A-Z]{1,5}|(?<!\w)[A-Z]{2,5}(?!\w)", text)})


def _extract_sector_tickers(text: str) -> list[str]:
    low = text.lower()
    tickers: list[str] = []
    for kw, syms in SECTOR_KEYWORDS_CHAT.items():
        if kw in low:
            tickers.extend(syms)
    return list(set(tickers))


def _fmt_alerts(rows, max_chars: int = 1500) -> str:
    lines = []
    chars = 0
    for r in rows:
        detail = (r["detail"] or "")[:80]
        line = f"[{r['created_at'][:10]}] {r['rule']} | {r['severity']} | {r['ticker'] or '—'} | {r['headline']}"
        if detail:
            line += f"\n  → {detail}"
        lines.append(line)
        chars += len(line)
        if chars > max_chars:
            break
    return "\n\n".join(lines) if lines else "(none)"


def _fetch_context(message: str, days: int) -> tuple[str, int]:
    conn = db_connection()
    msg_lower = message.lower()

    # 1. Tickers mentioned explicitly
    explicit_tickers = _extract_tickers(message)
    # 2. Tickers implied by sector keywords
    sector_tickers = _extract_sector_tickers(message)
    all_tickers = list(set(explicit_tickers + sector_tickers))[:15]

    # --- Alerts ---
    if all_tickers:
        like_clauses = " OR ".join("ticker LIKE ?" for _ in all_tickers)
        like_params = [f"%{t}%" for t in all_tickers]
        alert_rows = conn.execute(
            f"""SELECT rule, ticker, severity, headline, detail, created_at
                FROM alerts
                WHERE ({like_clauses})
                  AND datetime(created_at) >= datetime('now', ?)
                ORDER BY
                    CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END,
                    datetime(created_at) DESC
                LIMIT 25""",
            like_params + [f"-{days} days"],
        ).fetchall()
    else:
        alert_rows = conn.execute(
            """SELECT rule, ticker, severity, headline, detail, created_at
               FROM alerts
               WHERE datetime(created_at) >= datetime('now', ?)
               ORDER BY
                   CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END,
                   CASE rule WHEN 'RULE_10' THEN 1 WHEN 'RULE_06' THEN 2 ELSE 3 END,
                   datetime(created_at) DESC
               LIMIT 25""",
            (f"-{days} days",),
        ).fetchall()

    # --- RULE_10 corroborations ---
    rule10_rows = conn.execute(
        """SELECT ticker, headline, detail, created_at
           FROM alerts WHERE rule = 'RULE_10'
             AND datetime(created_at) >= datetime('now', ?)
           ORDER BY created_at DESC LIMIT 5""",
        (f"-{days} days",),
    ).fetchall()

    # --- Congressional trades ---
    if all_tickers:
        like_clauses = " OR ".join("t.raw_ticker_string LIKE ?" for _ in all_tickers)
        like_params = [f"%{t}%" for t in all_tickers]
        trade_rows = conn.execute(
            f"""SELECT t.raw_ticker_string AS ticker, t.transaction_type, t.amount_band,
                       m.full_name, m.party, m.state, t.transaction_date
                FROM transactions t JOIN members m ON t.member_id = m.bioguide_id
                WHERE ({like_clauses})
                  AND t.transaction_date >= date('now', ?)
                ORDER BY t.transaction_date DESC LIMIT 10""",
            like_params + [f"-{days} days"],
        ).fetchall()
    else:
        trade_rows = []

    # --- Government contracts ---
    if all_tickers:
        like_clauses = " OR ".join("ticker LIKE ?" for _ in all_tickers)
        like_params = [f"%{t}%" for t in all_tickers]
        contract_rows = conn.execute(
            f"""SELECT recipient_name, ticker, amount, agency, award_date
                FROM contracts
                WHERE ({like_clauses})
                ORDER BY award_date DESC LIMIT 5""",
            like_params,
        ).fetchall()
    else:
        contract_rows = []

    conn.close()

    # --- Build context block ---
    blocks: list[str] = []

    if rule10_rows:
        lines = [f"  ★ [{r['created_at'][:10]}] {r['ticker'] or '—'} | {r['headline']}" for r in rule10_rows]
        blocks.append("ACTIVE CORROBORATIONS (RULE_10 — highest confidence):\n" + "\n".join(lines))

    blocks.append(f"ALERTS ({len(alert_rows)} results, ranked by severity):\n" + _fmt_alerts(alert_rows, 1500))

    if trade_rows:
        lines = [f"  [{r['transaction_date']}] {r['ticker'] or '—'} | {r['transaction_type']} | {r['amount_band']} | {r['full_name']}" for r in trade_rows]
        blocks.append("CONGRESSIONAL TRADES:\n" + "\n".join(lines))

    if contract_rows:
        lines = [f"  {r['recipient_name']} | {r['ticker'] or '—'} | ${r['amount']:,.0f} | {r['agency']} | {r['award_date']}" for r in contract_rows]
        blocks.append("GOVERNMENT CONTRACTS:\n" + "\n".join(lines))

    context = "\n\n---\n\n".join(blocks)
    return context, len(alert_rows)


def _call_groq(api_key: str, prompt: str, retries: int = 3) -> str:
    from groq import Groq
    client = Groq(api_key=api_key)
    for attempt in range(retries):
        try:
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                max_tokens=600,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            if "429" in str(e) and attempt < retries - 1:
                time.sleep(30 * (attempt + 1))
                continue
            if "429" in str(e):
                return "Analyst temporarily unavailable — rate limit reached. Try again in 1 minute."
            raise
    return "Unable to generate response."


@router.post("")
def chat(req: ChatRequest):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return {"answer": "Ask Scope requires a Groq API key. Add GROQ_API_KEY to your .env file.", "context_alerts": 0}

    context, alert_count = _fetch_context(req.message, req.days)

    prompt = f"""Live Scope database context for your query:

{context}

---
User question: {req.message}

Answer based on the signals above. Be specific — name tickers, rules, amounts, dates."""

    try:
        answer = _call_groq(api_key, prompt)
        return {"answer": answer, "context_alerts": alert_count}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
