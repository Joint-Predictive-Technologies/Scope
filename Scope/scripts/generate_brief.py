#!/usr/bin/env python3
"""
generate_brief.py — Daily Brief generator.
Queries DB for last 48h activity, calls Groq, caches in daily_briefs table.
Run manually or via cron: 0 6 * * * cd /app && python Scope/scripts/generate_brief.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection

GROQ_MODEL = "llama-3.3-70b-versatile"


def _gather_data(conn, days: float = 1) -> dict:
    window = f"-{days} day" if days <= 1 else f"-{int(days)} days"

    congressional = conn.execute(
        """
        SELECT t.raw_ticker_string AS ticker, t.transaction_type, t.amount_band,
               m.full_name, m.party, m.state, t.filing_date
        FROM transactions t
        JOIN members m ON t.member_id = m.bioguide_id
        WHERE t.filing_date >= date('now', ?)
        ORDER BY t.filing_date DESC LIMIT 25
        """,
        (window,),
    ).fetchall()

    rule06 = conn.execute(
        """
        SELECT headline, detail, ticker, severity, created_at
        FROM alerts WHERE rule = 'RULE_06'
          AND datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC LIMIT 10
        """,
        (window,),
    ).fetchall()

    rule08 = conn.execute(
        """
        SELECT headline, detail, ticker, created_at
        FROM alerts WHERE rule = 'RULE_08'
          AND datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC LIMIT 10
        """,
        (window,),
    ).fetchall()

    rule07 = conn.execute(
        """
        SELECT headline, detail, ticker, created_at
        FROM alerts WHERE rule = 'RULE_07'
          AND datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC LIMIT 10
        """,
        (window,),
    ).fetchall()

    rule10 = conn.execute(
        """
        SELECT headline, detail, ticker, severity, created_at
        FROM alerts WHERE rule = 'RULE_10'
          AND datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC LIMIT 10
        """,
        (window,),
    ).fetchall()

    rule09 = conn.execute(
        """
        SELECT headline, detail, ticker, created_at
        FROM alerts WHERE rule = 'RULE_09'
          AND datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC LIMIT 10
        """,
        (window,),
    ).fetchall()

    rule11 = conn.execute(
        """
        SELECT headline, detail, ticker, severity, created_at
        FROM alerts WHERE rule = 'RULE_11'
          AND datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC LIMIT 10
        """,
        (window,),
    ).fetchall()

    tickers_seen = sorted({
        r["ticker"].strip() for r in congressional if r["ticker"] and r["ticker"].strip()
    })

    return {
        "congressional": {
            "count": len(congressional),
            "top_tickers": tickers_seen[:10],
            "rows": [dict(r) for r in congressional[:8]],
        },
        "insider": {
            "count": len(rule06),
            "rows": [dict(r) for r in rule06[:5]],
        },
        "regulatory": {
            "count": len(rule08),
            "rows": [dict(r) for r in rule08[:5]],
        },
        "prediction_markets": {
            "count": len(rule07),
            "rows": [dict(r) for r in rule07[:5]],
        },
        "corroborations": {
            "count": len(rule10),
            "rows": [dict(r) for r in rule10[:5]],
        },
        "lobbying": {
            "count": len(rule09),
            "rows": [dict(r) for r in rule09[:5]],
        },
        "contracts": {
            "count": len(rule11),
            "rows": [dict(r) for r in rule11[:5]],
        },
    }


def _build_prompt(data: dict, date_str: str) -> str:
    def _fmt_rows(rows: list[dict], fields: list[str]) -> str:
        out = []
        for r in rows:
            parts = [str(r.get(f, "")) for f in fields if r.get(f)]
            out.append(" | ".join(parts))
        return "\n".join(out) if out else "(none)"

    cong = data["congressional"]
    cong_rows = _fmt_rows(cong["rows"], ["ticker", "transaction_type", "amount_band", "full_name", "party", "state"])

    ins = data["insider"]
    ins_rows = _fmt_rows(ins["rows"], ["headline", "detail"])

    reg = data["regulatory"]
    reg_rows = _fmt_rows(reg["rows"], ["headline", "detail"])

    mkt = data["prediction_markets"]
    mkt_rows = _fmt_rows(mkt["rows"], ["headline", "detail"])

    corr = data["corroborations"]
    corr_rows = _fmt_rows(corr["rows"], ["ticker", "headline", "detail"])

    lob = data["lobbying"]
    lob_rows = _fmt_rows(lob["rows"], ["headline", "detail"])

    con = data["contracts"]
    con_rows = _fmt_rows(con["rows"], ["headline", "detail"])

    return f"""Today is {date_str}. You are a political intelligence analyst writing the Scope Daily Brief for macro and event-driven investors. Be direct, specific, and actionable. Name tickers, sectors, and individuals. Do not hedge excessively.

Here is the raw data for the past 48 hours:

=== CROSS-SOURCE CORROBORATIONS — RULE_10 ({corr['count']} signals — LEAD WITH THESE if any exist) ===
{corr_rows}

=== CONGRESSIONAL TRADING ({cong['count']} transactions) ===
Top tickers: {', '.join(cong['top_tickers']) or 'none'}
{cong_rows}

=== INSIDER ACTIVITY — RULE_06 ({ins['count']} alerts) ===
{ins_rows}

=== GOVERNMENT CONTRACTS — RULE_11 ({con['count']} alerts) ===
{con_rows}

=== REGULATORY — RULE_08 ({reg['count']} alerts) ===
{reg_rows}

=== PREDICTION MARKETS — RULE_07 ({mkt['count']} alerts) ===
{mkt_rows}

=== LOBBYING — RULE_09 ({lob['count']} alerts) ===
{lob_rows}

Write the Scope Daily Brief in strict JSON with this exact structure:
{{
  "ai_summary": "3-4 sentences. Lead with the most actionable signal (corroborations first if present). Name specific tickers, amounts, or people. Explain why it matters for positioning. End with one forward-looking sentence.",
  "sections": {{
    "corroborations":       {{ "one_liner": "Name the ticker(s) and converging rules. If none: 'No cross-source corroborations in this window.'" }},
    "congressional":        {{ "one_liner": "Name the top ticker(s) and whether buys or sells dominate." }},
    "insider":              {{ "one_liner": "Name the company/ticker if available, state direction." }},
    "contracts":            {{ "one_liner": "Name the recipient and amount if available. 'No new contracts' if none." }},
    "regulatory":           {{ "one_liner": "Name the sector or specific rule. 'No new filings' if none." }},
    "prediction_markets":   {{ "one_liner": "Name the market and direction. 'No significant moves' if none." }},
    "lobbying":             {{ "one_liner": "Name the company or sector showing the spike. 'No spikes' if none." }}
  }}
}}

Return ONLY valid JSON. No preamble, no markdown fences."""


def generate(date_str: str | None = None, days: float = 2) -> dict:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    conn = db_connection()
    today = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check cache
    cached = conn.execute(
        "SELECT content_json, generated_at FROM daily_briefs WHERE date = ?", (today,)
    ).fetchone()
    if cached:
        print(f"[brief] cached for {today}")
        conn.close()
        return {"date": today, "content_json": cached["content_json"], "generated_at": cached["generated_at"]}

    data = _gather_data(conn, days)
    prompt = _build_prompt(data, today)

    from groq import Groq
    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=1200,
        messages=[
            {"role": "system", "content": "You are a financial intelligence analyst. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
    )
    raw = resp.choices[0].message.content.strip()

    # Extract JSON (handle possible preamble)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON in Groq response: {raw[:300]}")
    content_json = m.group(0)
    # Validate parse
    json.loads(content_json)

    generated_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO daily_briefs (date, content_json, generated_at) VALUES (?, ?, ?)",
        (today, content_json, generated_at),
    )
    conn.commit()
    conn.close()
    print(f"[brief] generated for {today}")
    return {"date": today, "content_json": content_json, "generated_at": generated_at}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Generate daily brief")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument("--days", type=float, default=2, help="lookback window in days")
    p.add_argument("--force", action="store_true", help="Overwrite existing brief")
    args = p.parse_args()

    if args.force and args.date:
        conn = db_connection()
        conn.execute("DELETE FROM daily_briefs WHERE date = ?", (args.date,))
        conn.commit()
        conn.close()

    result = generate(date_str=args.date, days=args.days)
    print(json.dumps(json.loads(result["content_json"]), indent=2))
