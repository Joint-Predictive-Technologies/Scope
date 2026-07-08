from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from jpt_common import db_connection

router = APIRouter()


def _week_start() -> str:
    today = datetime.now(timezone.utc)
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d")


def _gather_data(conn) -> dict:
    top_ticker = conn.execute("""
        SELECT REPLACE(ticker, '$', '') AS t, COUNT(DISTINCT rule) AS rules
        FROM alerts
        WHERE datetime(created_at) >= datetime('now', '-7 days')
          AND ticker IS NOT NULL AND ticker != '' AND ticker NOT LIKE '% %'
        GROUP BY t ORDER BY rules DESC, COUNT(*) DESC LIMIT 1
    """).fetchone()

    biggest_insider = conn.execute("""
        SELECT headline, detail FROM alerts
        WHERE rule = 'RULE_06' AND datetime(created_at) >= datetime('now', '-7 days')
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()

    top_cluster = conn.execute("""
        SELECT headline, detail, ticker FROM alerts
        WHERE rule = 'RULE_02' AND datetime(created_at) >= datetime('now', '-7 days')
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()

    top_regulatory = conn.execute("""
        SELECT headline, detail FROM alerts
        WHERE rule = 'RULE_08' AND datetime(created_at) >= datetime('now', '-7 days')
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()

    first_touches_ct = conn.execute("""
        SELECT COUNT(*) FROM alerts
        WHERE rule = 'RULE_01B' AND datetime(created_at) >= datetime('now', '-7 days')
    """).fetchone()[0]

    total = conn.execute("""
        SELECT COUNT(*) FROM alerts WHERE datetime(created_at) >= datetime('now', '-7 days')
    """).fetchone()[0]

    return {
        "total":           total,
        "top_ticker":      dict(top_ticker) if top_ticker else None,
        "biggest_insider": dict(biggest_insider) if biggest_insider else None,
        "top_cluster":     dict(top_cluster) if top_cluster else None,
        "top_regulatory":  dict(top_regulatory) if top_regulatory else None,
        "first_touches":   first_touches_ct,
    }


def _generate(week_start: str) -> dict:
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if not groq_key:
        return {"error": "GROQ_API_KEY not set", "week_start": week_start}

    conn = db_connection()
    data = _gather_data(conn)
    conn.close()

    prompt = f"""Write a "This Week in Political Money" digest for the week of {week_start}.

Input data:
{json.dumps(data, indent=2)}

Return ONLY a JSON object with exactly these keys:
{{
  "top_ticker":          {{"name": "TICKER", "summary": "2-3 sentences on why it dominated this week."}},
  "biggest_insider":     {{"summary": "2-3 sentences on the most notable SEC Form 4 insider move."}},
  "cluster_of_week":     {{"summary": "2-3 sentences on the most notable congressional cluster."}},
  "regulatory_spotlight":{{"summary": "2-3 sentences on the most impactful Federal Register finding."}},
  "first_touches":       {{"count": {data["first_touches"]}, "summary": "1-2 sentences on notable first-time positions."}}
}}
Be analytical, concise, and focused on what matters for investors. No preamble."""

    try:
        from groq import Groq
        client = Groq(api_key=groq_key)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=900,
            temperature=0.3,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        content = match.group(0) if match else raw

        conn2 = db_connection()
        conn2.execute(
            "INSERT OR REPLACE INTO digests (week_start, content) VALUES (?,?)",
            (week_start, content),
        )
        conn2.commit()
        conn2.close()

        return {
            "week_start": week_start,
            "content": content,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {"error": str(exc), "week_start": week_start}


@router.get("")
def get_digest():
    week = _week_start()
    conn = db_connection()
    row = conn.execute(
        "SELECT * FROM digests WHERE week_start = ?", (week,)
    ).fetchone()
    conn.close()

    if row:
        return dict(row)

    return _generate(week)


@router.post("/regenerate")
def regenerate_digest():
    """Force re-generation of this week's digest (admin use)."""
    week = _week_start()
    conn = db_connection()
    conn.execute("DELETE FROM digests WHERE week_start = ?", (week,))
    conn.commit()
    conn.close()
    return _generate(week)
