"""
Rule performance + system health dashboard data.

Read-only aggregation over the alerts table — how many signals each rule has
fired, its severity mix, how often its signals were later corroborated by
RULE_10, cadence, and simple ingestion-health indicators.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from jpt_common import db_connection

router = APIRouter()


@router.get("/rules")
def rule_performance(days: int = Query(default=30, ge=1, le=365)):
    conn = db_connection()

    # Tickers corroborated by RULE_10 in the window — used for corroboration rate.
    corr_tickers = {
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT ticker FROM alerts
            WHERE rule = 'RULE_10' AND ticker IS NOT NULL AND ticker != ''
            """
        ).fetchall()
    }

    rows = conn.execute(
        """
        SELECT rule,
               COUNT(*)                                               AS total_all_time,
               SUM(CASE WHEN datetime(created_at) >= datetime('now', ?) THEN 1 ELSE 0 END) AS total_window,
               SUM(CASE WHEN severity = 'CRITICAL' THEN 1 ELSE 0 END) AS n_critical,
               SUM(CASE WHEN severity = 'HIGH'     THEN 1 ELSE 0 END) AS n_high,
               SUM(CASE WHEN severity = 'MEDIUM'   THEN 1 ELSE 0 END) AS n_medium,
               MAX(created_at)                                        AS last_fired,
               MIN(created_at)                                        AS first_fired
        FROM alerts
        GROUP BY rule
        ORDER BY total_window DESC, total_all_time DESC
        """,
        (f"-{days} days",),
    ).fetchall()

    results = []
    for r in rows:
        rule = r["rule"]
        # Corroboration rate: distinct tickers this rule flagged that also
        # appear in a RULE_10 corroboration.
        flagged = {
            x[0]
            for x in conn.execute(
                "SELECT DISTINCT ticker FROM alerts WHERE rule = ? "
                "AND ticker IS NOT NULL AND ticker != ''",
                (rule,),
            ).fetchall()
        }
        corr_rate = (
            round(len(flagged & corr_tickers) / len(flagged) * 100, 1)
            if flagged else 0.0
        )

        top_tickers = [
            {"ticker": x[0], "count": x[1]}
            for x in conn.execute(
                """
                SELECT ticker, COUNT(*) c FROM alerts
                WHERE rule = ? AND ticker IS NOT NULL AND ticker != ''
                GROUP BY ticker ORDER BY c DESC LIMIT 3
                """,
                (rule,),
            ).fetchall()
        ]

        total_all = r["total_all_time"] or 0
        results.append({
            "rule":            rule,
            "total_all_time":  total_all,
            "total_window":    r["total_window"] or 0,
            "avg_per_day":     round((r["total_window"] or 0) / max(days, 1), 2),
            "severity": {
                "CRITICAL": r["n_critical"] or 0,
                "HIGH":     r["n_high"] or 0,
                "MEDIUM":   r["n_medium"] or 0,
            },
            "corroboration_rate": corr_rate,
            "top_tickers":        top_tickers,
            "last_fired":         r["last_fired"],
            "first_fired":        r["first_fired"],
        })

    conn.close()
    return {"days": days, "rules": results}


@router.get("/health")
def system_health():
    conn = db_connection()

    total_alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    last_24h = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE datetime(created_at) >= datetime('now', '-24 hours')"
    ).fetchone()[0]

    # Rules that haven't fired in >24h (possible ingestion issue).
    stale = [
        {"rule": r[0], "last_fired": r[1]}
        for r in conn.execute(
            """
            SELECT rule, MAX(created_at) last FROM alerts
            GROUP BY rule
            HAVING datetime(last) < datetime('now', '-24 hours')
            ORDER BY last ASC
            """
        ).fetchall()
    ]

    db_size = 0
    try:
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        db_size = page_count * page_size
    except Exception:
        pass

    conn.close()
    return {
        "total_alerts":      total_alerts,
        "alerts_last_24h":   last_24h,
        "ingest_rate_per_h": round(last_24h / 24, 2),
        "db_size_mb":        round(db_size / 1e6, 2),
        "stale_rules":       stale,
    }
