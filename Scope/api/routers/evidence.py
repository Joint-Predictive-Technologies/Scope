"""
Evidence endpoints — power the right-side evidence drawer.

No auth (read-only, same data the public feed already exposes). Returns a single
alert with its confidence breakdown, source, related signals on the same ticker,
and a timeline, so the drawer can render without extra round-trips.
"""
from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from jpt_common import db_connection, rule10_rules_from_tags, rule10_eligible_rules

router = APIRouter()

_SOURCE = {
    "RULE_01B": ("House Clerk", "https://disclosures-clerk.house.gov"),
    "RULE_02":  ("House Clerk", "https://disclosures-clerk.house.gov"),
    "RULE_06":  ("SEC EDGAR", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4"),
    "RULE_07":  ("Polymarket", "https://polymarket.com/markets?category=politics"),
    "RULE_08":  ("Federal Register", "https://www.federalregister.gov"),
    "RULE_09":  ("Senate LDA", "https://lda.senate.gov"),
    "RULE_11":  ("USASpending.gov", "https://www.usaspending.gov"),
    "RULE_12":  ("Senate LDA (foreign)", "https://lda.senate.gov"),
    "RULE_13":  ("FEC.gov", "https://www.fec.gov/data/"),
    "RULE_14":  ("PatentsView", "https://patentsview.org"),
    "RULE_15":  ("SEC EDGAR", "https://efts.sec.gov/LATEST/search-index?forms=8-K"),
    "RULE_OSINT": ("GDELT / Google News", "https://www.gdeltproject.org"),
}

_WHY = {
    "RULE_01B": "A member opened a brand-new disclosed position in this security.",
    "RULE_02":  "Multiple members traded this ticker within a short window (cluster).",
    "RULE_06":  "An executive's Form 4 trade was well above their historical average.",
    "RULE_07":  "A politically-linked prediction market moved sharply on volume.",
    "RULE_08":  "A proposed federal regulation references this sector.",
    "RULE_09":  "Quarterly lobbying spend spiked year-over-year.",
    "RULE_10":  "Four or more distinct eligible rules converged on this ticker within 24h.",
    "RULE_11":  "A federal contract was awarded to this company (or its public parent).",
    "RULE_12":  "A foreign entity is disclosed lobbying in this sector.",
    "RULE_13":  "A large industry PAC contribution was recorded.",
    "RULE_14":  "A cluster of patent filings signals concentrated R&D.",
    "RULE_15":  "Political keyword density spiked in an earnings-related filing.",
    "RULE_OSINT": "A geopolitical event maps to this ticker's sector exposure.",
}


def _first_ticker(t: str) -> str:
    return (t or "").replace("$", "").split(" ")[0]


def _confidence_breakdown(alert: dict, related: list) -> dict:
    """Component breakdown; RULE_10 uses the full eligibility model."""
    import datetime as _dt
    rule = alert.get("rule", "")
    sev = alert.get("severity", "MEDIUM")
    # freshness from age
    try:
        ts = _dt.datetime.fromisoformat((alert.get("created_at") or "").replace(" ", "T"))
        age_h = (_dt.datetime.utcnow() - ts).total_seconds() / 3600
    except Exception:
        age_h = 48
    freshness = 15 if age_h < 6 else 10 if age_h < 24 else 5 if age_h < 48 else 0

    if rule == "RULE_10":
        rules = rule10_eligible_rules(rule10_rules_from_tags(alert.get("tags") or ""))
        rule_pts = min(len(rules) * 10, 60)
        sev_pts = 20 if sev == "CRITICAL" else 10
        insider = 15 if "RULE_06" in rules else 0
        contract = 10 if "RULE_11" in rules else 0
        conflicting = sum(1 for r in related if "sale" in (r.get("headline") or "").lower())
        total = min(rule_pts + sev_pts + freshness + insider + contract, 100)
        return {
            "total": total,
            "components": {
                "Eligible rule count": rule_pts,
                "Severity": sev_pts,
                "Freshness": freshness,
                "Insider significance": insider,
                "Historical / contract": contract,
            },
            "eligible_rules": rules,
            "conflicting_signals": conflicting,
        }
    # Single-rule alert — lighter breakdown
    sev_pts = 40 if sev == "CRITICAL" else 25 if sev == "HIGH" else 10
    corrob = min(len({r.get("rule") for r in related}) * 8, 24)
    total = min(sev_pts + freshness + corrob, 100)
    return {
        "total": total,
        "components": {"Severity": sev_pts, "Freshness": freshness, "Corroborating signals": corrob},
        "eligible_rules": [rule] if rule else [],
        "conflicting_signals": 0,
    }


@router.get("/alert/{alert_id}")
def alert_evidence(alert_id: int):
    conn = db_connection()
    a = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if not a:
        conn.close()
        return JSONResponse(status_code=404, content={"error": "Alert not found"})
    alert = dict(a)
    tk = _first_ticker(alert.get("ticker") or "")

    related = []
    if tk:
        related = [
            dict(r) for r in conn.execute(
                """
                SELECT id, rule, ticker, severity, headline, created_at
                FROM alerts
                WHERE ticker LIKE ? AND id != ?
                  AND datetime(created_at) >= datetime('now', '-30 days')
                ORDER BY datetime(created_at) DESC LIMIT 12
                """,
                (f"%{tk}%", alert_id),
            ).fetchall()
        ]
    conn.close()

    src = _SOURCE.get(alert.get("rule", ""), ("Source", None))
    return {
        "alert": alert,
        "ticker": tk,
        "why": alert.get("why_matters") or _WHY.get(alert.get("rule", ""), ""),
        "source": {"label": src[0], "url": alert.get("source_url") or src[1]},
        "confidence": _confidence_breakdown(alert, related),
        "related": related,
        "timeline": list(reversed(related))[-8:],
        "related_rules": sorted({r["rule"] for r in related if r.get("rule")}),
    }


@router.get("/ticker/{symbol}")
def ticker_evidence(symbol: str, days: int = 90):
    sym = _first_ticker(symbol).upper()
    conn = db_connection()
    rows = [
        dict(r) for r in conn.execute(
            """
            SELECT id, rule, ticker, severity, headline, created_at
            FROM alerts
            WHERE ticker LIKE ? AND datetime(created_at) >= datetime('now', ?)
            ORDER BY datetime(created_at) DESC LIMIT 40
            """,
            (f"%{sym}%", f"-{days} days"),
        ).fetchall()
    ]
    conn.close()
    return {
        "ticker": sym,
        "signals": rows,
        "rules": sorted({r["rule"] for r in rows if r.get("rule")}),
        "count": len(rows),
    }
