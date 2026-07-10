from __future__ import annotations

from fastapi import APIRouter, Query

from jpt_common import db_connection, classify_sector

router = APIRouter()


def _classify(ticker: str, text: str) -> str:
    return classify_sector(ticker, text)


@router.get("/data")
def get_sectors(days: int = Query(default=30, ge=1, le=90)):
    # Exclude high-volume noisy rules — same set excluded from heat index calculation
    _NOISY = ("'RULE_07'", "'RULE_OSINT'", "'RULE_REDDIT'", "'RULE_ANOMALY'")
    _excl  = ",".join(_NOISY)

    conn = db_connection()
    rows = conn.execute(
        f"""
        SELECT id, rule, severity, headline, tags, ticker, created_at
        FROM alerts
        WHERE datetime(created_at) >= datetime('now', ?)
          AND rule NOT IN ({_excl})
        """,
        (f"-{days} days",),
    ).fetchall()
    conn.close()

    prev_conn = db_connection()
    prev_rows = prev_conn.execute(
        f"""
        SELECT id, rule, severity, headline, tags, ticker
        FROM alerts
        WHERE datetime(created_at) >= datetime('now', ?)
          AND datetime(created_at) < datetime('now', ?)
          AND rule NOT IN ({_excl})
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
        _tparts = (alert.get("ticker") or "").replace("$", "").split()
        t = _tparts[0] if _tparts else ""
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
