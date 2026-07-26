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

from jpt_common import db_connection, rule10_is_valid, rule10_rules_from_tags

GROQ_MODEL = "llama-3.3-70b-versatile"


def _gather_data(conn, days: float = 2) -> dict:
    """Pull the highest-opportunity signals from the lookback window.

    Ordered by `opportunity_score` (how much opportunity remains) — NOT by rule.
    The previous ORDER BY promoted RULE_11 to rank 2 for every window, which put
    the federal-contract feed near the top of the brief every morning regardless
    of score; that feed is heavily concentrated in the same few defence primes,
    so the brief kept opening on them. Severity is no longer a sort key either —
    it stays only as the eligibility filter in the WHERE clause, because ranking
    on it first made `opportunity_score` a mere tiebreak within a severity band.

    This changes ordering ONLY. `opportunity_score` is computed at write time in
    `jpt_common` and is untouched here.
    """
    window = f"-{int(max(days, 1))} days"

    signals = conn.execute(
        """
        SELECT id, rule, ticker, headline, detail, severity, tags, created_at,
               COALESCE(opportunity_score, 0) AS opportunity_score
        FROM alerts
        WHERE created_at >= datetime('now', ?)
          AND severity IN ('CRITICAL', 'HIGH')
        ORDER BY
            COALESCE(opportunity_score, 0) DESC,
            datetime(created_at) DESC,
            id DESC
        LIMIT 20
        """,
        (window,),
    ).fetchall()

    # Group by rule; only VALID RULE_10 (4+ distinct eligible rules) may be
    # grouped as a corroboration — this is what the brief is allowed to cite.
    by_rule: dict = {}
    for r in signals:
        rule = r["rule"] or "OTHER"
        if rule == "RULE_10" and not rule10_is_valid(rule10_rules_from_tags(r["tags"] or "")):
            continue  # drop invalid/legacy corroborations entirely
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

    # Drop invalid/legacy RULE_10 from the cited signal set entirely so the
    # brief's evidence never references an expired corroboration.
    clean_signals = [
        dict(r) for r in signals
        if not (r["rule"] == "RULE_10"
                and not rule10_is_valid(rule10_rules_from_tags(r["tags"] or "")))
    ]

    # Evidence map: ticker -> list of {id, rule} that back a claim about it.
    evidence: dict = {}
    alert_ids: list = []
    for r in clean_signals:
        alert_ids.append(r["id"])
        tk = (r["ticker"] or "").replace("$", "").split()[0] if r["ticker"] else ""
        if tk:
            evidence.setdefault(tk, []).append({"id": r["id"], "rule": r["rule"]})

    return {
        "signals":     clean_signals,
        "by_rule":     by_rule,
        "congressional": [dict(r) for r in congressional],
        "total":       len(clean_signals),
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
    ) or "  NONE — there are no valid cross-source corroborations in this window."

    cong_block = "\n".join(
        f"  {r.get('ticker','—')} | {r.get('transaction_type','')} | {r.get('amount_band','')} | {r.get('full_name','')}"
        for r in congressional[:8]
    ) or "  (none in this window)"

    return f"""Today is {date_str}. You are a political intelligence analyst writing the Scope Daily Brief.

You MUST reference the specific signals below. Do NOT say there are no signals — there are {total} high-priority signals active right now.

CRITICAL RULES ABOUT CORROBORATION:
- Only a RULE_10 entry in the CORROBORATIONS section below may be described as
  "corroborated" or as a "cross-source corroboration".
- RULE_07 (Polymarket), RULE_OSINT, RULE_REDDIT and RULE_ANOMALY are NOT
  corroboration. Never say a signal is corroborated because OSINT, a prediction
  market, Reddit, or an anomaly agrees with it.
- If the CORROBORATIONS section says NONE, you MUST state plainly that there are
  no cross-source corroborations in this window, and lead instead with the
  FIRST signal in the list below. The list is ranked by opportunity score; do not
  substitute a different signal because its rule feels more important.

=== CORROBORATIONS — RULE_10 (the ONLY thing you may call "corroborated") ===
{rule10_block}

=== ALL HIGH-PRIORITY SIGNALS ({total} total, ranked by opportunity score — most opportunity remaining first) ===
{signal_block}

=== CONGRESSIONAL TRADES ({len(congressional)} transactions) ===
{cong_block}

Write the Scope Daily Brief in strict JSON:
{{
  "ai_summary": "4-5 sentences. Be direct and assertive. Lead with the FIRST signal in the ranked list — name the ticker, the rule that fired, and what it implies for positioning. If RULE_10 corroborations exist, mention the converging sources. Name specific tickers, dollar amounts where available. End with one forward-looking sentence about what to watch.",
  "sections": {{
    "corroborations":     {{ "one_liner": "If RULE_10 fired: name ticker(s) and which rules converged." }},
    "insider":            {{ "one_liner": "Name ticker and direction (RULE_06)." }},
    "contracts":          {{ "one_liner": "Name recipient, ticker, amount (RULE_11)." }},
    "regulatory":         {{ "one_liner": "Name sector or rule affected (RULE_08)." }},
    "prediction_markets": {{ "one_liner": "Name the market and move direction (RULE_07)." }},
    "congressional":      {{ "one_liner": "Name the top ticker and whether buys or sells dominate." }}
  }}
}}

OMIT any "sections" key whose rule produced NO signal in the list above. Do NOT
emit filler like "No new contracts." — a rule that did not fire simply gets no
section. Only "corroborations" is different: if there are no valid RULE_10
corroborations, say so explicitly there, because their absence is itself
information. Emitting a section for a rule that did not fire is an error.

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
    try:
        from jpt_common import record_activity
        record_activity("BRIEF", scanned=data.get("total", 0),
                        flagged=len(data.get("alert_ids", [])), emitted=1)
    except Exception:
        pass
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
