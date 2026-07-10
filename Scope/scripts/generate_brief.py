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


def _gather_data(conn, days: float = 2) -> dict:
    """Pull highest-ranked signals from the lookback window, prioritised by severity and rule."""
    window = f"-{int(max(days, 1))} days"

    signals = conn.execute(
        """
        SELECT id, rule, ticker, headline, detail, severity, created_at
        FROM alerts
        WHERE created_at >= datetime('now', ?)
          AND severity IN ('CRITICAL', 'HIGH')
        ORDER BY
            CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 END,
            CASE rule
                WHEN 'RULE_10' THEN 1
                WHEN 'RULE_11' THEN 2
                WHEN 'RULE_06' THEN 3
                WHEN 'RULE_OSINT' THEN 4
                ELSE 5
            END,
            created_at DESC
        LIMIT 20
        """,
        (window,),
    ).fetchall()

    # Group by rule for structured sections
    by_rule: dict = {}
    for r in signals:
        rule = r["rule"] or "OTHER"
        by_rule.setdefault(rule, []).append(dict(r))

    # Congressional trades (separate data source)
    congressional = conn.execute(
        """
        SELECT t.raw_ticker_string AS ticker, t.transaction_type, t.amount_band,
               m.full_name, m.party, m.state, t.filing_date
        FROM transactions t
        JOIN members m ON t.member_id = m.bioguide_id
        WHERE t.filing_date >= date('now', ?)
        ORDER BY t.filing_date DESC LIMIT 15
        """,
        (window,),
    ).fetchall()

    # Evidence map: ticker -> list of {id, rule} that back a claim about it.
    evidence: dict = {}
    alert_ids: list = []
    for r in signals:
        alert_ids.append(r["id"])
        tk = (r["ticker"] or "").replace("$", "").split()[0] if r["ticker"] else ""
        if tk:
            evidence.setdefault(tk, []).append({"id": r["id"], "rule": r["rule"]})

    return {
        "signals":     [dict(r) for r in signals],
        "by_rule":     by_rule,
        "congressional": [dict(r) for r in congressional],
        "total":       len(signals),
        "alert_ids":   alert_ids,
        "evidence":    evidence,
    }


def _build_prompt(data: dict, date_str: str) -> str:
    signals = data["signals"]
    by_rule = data["by_rule"]
    congressional = data["congressional"]
    total = data["total"]

    if total == 0:
        signal_block = "No HIGH or CRITICAL alerts in the last 48 hours — this is unusual and should be noted explicitly."
    else:
        lines = []
        for s in signals:
            ticker = (s.get("ticker") or "").replace("$", "").split()[0]
            parts = [
                f"[{s['rule']}]",
                f"{s['severity']}",
                ticker or "—",
                s.get("headline", "")[:120],
            ]
            if s.get("detail"):
                parts.append(f"→ {s['detail'][:80]}")
            lines.append("  " + " | ".join(p for p in parts if p))
        signal_block = "\n".join(lines)

    rule10 = by_rule.get("RULE_10", [])
    rule10_block = "\n".join(
        f"  ★ {r.get('ticker','—')} | {r.get('headline','')[:100]}" for r in rule10
    ) or "  (none in this window)"

    cong_block = "\n".join(
        f"  {r.get('ticker','—')} | {r.get('transaction_type','')} | {r.get('amount_band','')} | {r.get('full_name','')}"
        for r in congressional[:8]
    ) or "  (none in this window)"

    return f"""Today is {date_str}. You are a political intelligence analyst writing the Scope Daily Brief.

You MUST reference the specific signals below. Do NOT say there are no signals — there are {total} high-priority signals active right now.

=== CORROBORATIONS — RULE_10 (LEAD WITH THESE if present) ===
{rule10_block}

=== ALL HIGH-PRIORITY SIGNALS ({total} total, ranked by severity then rule) ===
{signal_block}

=== CONGRESSIONAL TRADES ({len(congressional)} transactions) ===
{cong_block}

Write the Scope Daily Brief in strict JSON:
{{
  "ai_summary": "4-5 sentences. Be direct and assertive. Lead with the most actionable signal — name the ticker, the rule that fired, and what it implies for positioning. If RULE_10 corroborations exist, mention the converging sources. Name specific tickers, dollar amounts where available. End with one forward-looking sentence about what to watch.",
  "sections": {{
    "corroborations": {{ "one_liner": "If RULE_10 fired: name ticker(s) and which rules converged. If none: 'No cross-source corroborations in this window.'" }},
    "insider":        {{ "one_liner": "Name ticker and direction (RULE_06). 'No unusual insider activity.' if absent." }},
    "contracts":      {{ "one_liner": "Name recipient, ticker, amount (RULE_11). 'No new contracts.' if absent." }},
    "regulatory":     {{ "one_liner": "Name sector or rule affected (RULE_08). 'No new filings.' if absent." }},
    "prediction":     {{ "one_liner": "Name the market and move direction (RULE_07). 'No significant moves.' if absent." }},
    "congressional":  {{ "one_liner": "Name the top ticker and whether buys or sells dominate. 'No recent disclosures.' if absent." }}
  }}
}}

Return ONLY valid JSON. No preamble, no markdown fences, no commentary."""


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
    alert_ids_json = json.dumps(data.get("alert_ids", []))
    evidence_json = json.dumps(data.get("evidence", {}))
    conn.execute(
        """INSERT OR REPLACE INTO daily_briefs
           (date, content_json, generated_at, alert_ids, evidence_json)
           VALUES (?, ?, ?, ?, ?)""",
        (today, content_json, generated_at, alert_ids_json, evidence_json),
    )
    conn.commit()
    conn.close()
    print(f"[brief] generated for {today} — {len(data.get('alert_ids', []))} evidence alerts")
    return {"date": today, "content_json": content_json, "generated_at": generated_at,
            "alert_ids": alert_ids_json, "evidence_json": evidence_json}


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
