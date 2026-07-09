from __future__ import annotations

from fastapi import APIRouter, Query

from jpt_common import db_connection

router = APIRouter()

SECTOR_MAP: dict[str, list[str]] = {
    "Defense":    ["LMT", "RTX", "NOC", "GD", "BA", "HII", "LDOS", "SAIC", "L3H", "CACI"],
    "Technology": ["NVDA", "AAPL", "MSFT", "AMD", "INTC", "TSM", "QCOM", "AVGO", "ARM", "MU", "AMAT", "LRCX"],
    "Finance":    ["GS", "JPM", "MS", "BAC", "WFC", "BLK", "C", "AXP", "SCHW", "COF"],
    "Energy":     ["XOM", "CVX", "COP", "EOG", "SLB", "OXY", "VLO", "MPC", "PSX", "HAL"],
    "Pharma":     ["JNJ", "PFE", "MRK", "ABBV", "BMY", "LLY", "AMGN", "GILD", "REGN", "BIIB"],
    "Telecom":    ["T", "VZ", "TMUS", "CMCSA", "CHTR", "DISH"],
    "Retail":     ["AMZN", "WMT", "TGT", "COST", "HD", "LOW", "MCD", "SBUX"],
}

SECTOR_KEYWORDS: dict[str, list[str]] = {
    "Defense":    ["defense", "military", "itar", "dod", "weapons", "missile", "navy", "pentagon"],
    "Technology": ["semiconductor", " ai ", "chips", "artificial intelligence", "cloud", "software", "tech"],
    "Finance":    ["bank", "stablecoin", "crypto", "financial", "interest rate", "federal reserve", "fintech"],
    "Energy":     ["oil", "gas", "iran", "pipeline", "crude", "lng", "refinery", "opec"],
    "Pharma":     ["medicare", "drug pricing", "fda", "pharmaceutical", "clinical", "vaccine", "biotech"],
    "Telecom":    ["spectrum", "broadband", "5g", "wireless", "telecom"],
    "Retail":     ["retail", "consumer", "ecommerce", "tariff", "import"],
}


def _classify(ticker: str, text: str) -> str:
    t = ticker.upper().replace("$", "").split()[0] if ticker else ""
    for sector, tickers in SECTOR_MAP.items():
        if t in tickers:
            return sector
    low = text.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return sector
    return "Other"


@router.get("/data")
def get_sectors(days: int = Query(default=30, ge=1, le=90)):
    conn = db_connection()
    rows = conn.execute(
        """
        SELECT id, rule, severity, headline, tags, ticker, created_at
        FROM alerts
        WHERE datetime(created_at) >= datetime('now', ?)
        """,
        (f"-{days} days",),
    ).fetchall()
    conn.close()

    prev_conn = db_connection()
    prev_rows = prev_conn.execute(
        """
        SELECT id, rule, severity, headline, tags, ticker
        FROM alerts
        WHERE datetime(created_at) >= datetime('now', ?)
          AND datetime(created_at) < datetime('now', ?)
        """,
        (f"-{days * 2} days", f"-{days} days"),
    ).fetchall()
    prev_conn.close()

    # Build current-period sector counts
    buckets: dict[str, dict] = {}
    for r in rows:
        alert = dict(r)
        text   = (alert.get("headline") or "") + " " + (alert.get("tags") or "")
        sector = _classify(alert.get("ticker", ""), text)
        if sector not in buckets:
            buckets[sector] = {
                "alerts": [],
                "tickers": set(),
                "severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0},
                "rules": {},
            }
        buckets[sector]["alerts"].append(alert)
        t = (alert.get("ticker") or "").replace("$", "").split()[0]
        if t:
            buckets[sector]["tickers"].add(t)
        sev  = alert.get("severity", "MEDIUM")
        rule = alert.get("rule", "")
        buckets[sector]["severity"][sev] = buckets[sector]["severity"].get(sev, 0) + 1
        if rule:
            buckets[sector]["rules"][rule] = buckets[sector]["rules"].get(rule, 0) + 1

    # Previous-period counts for trend
    prev_counts: dict[str, int] = {}
    for r in prev_rows:
        alert  = dict(r)
        text   = (alert.get("headline") or "") + " " + (alert.get("tags") or "")
        sector = _classify(alert.get("ticker", ""), text)
        prev_counts[sector] = prev_counts.get(sector, 0) + 1

    result = {}
    for sector, data in buckets.items():
        cnt  = len(data["alerts"])
        prev = prev_counts.get(sector, 0)
        trend = "up" if cnt > prev else ("down" if cnt < prev else "flat")
        dominant_rule = max(data["rules"], key=data["rules"].get) if data["rules"] else None
        result[sector] = {
            "count":         cnt,
            "prev":          prev,
            "trend":         trend,
            "tickers":       list(data["tickers"])[:5],
            "severity":      data["severity"],
            "dominant_rule": dominant_rule,
        }

    return result
