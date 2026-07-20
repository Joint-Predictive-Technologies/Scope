from __future__ import annotations

import math
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection


router = APIRouter()

PER_PAGE = 20


def _row_to_dict(row) -> dict:
    return dict(row)


def _build_conditions(
    days: int,
    ticker: str | None,
    rule: str | None,
    severity: str | None,
    severity_min: str | None,
    watchlist: bool,
    since: str | None,
) -> tuple[list[str], dict]:
    conditions = ["datetime(a.created_at) >= datetime('now', :lookback)"]
    params: dict = {"lookback": f"-{days} days"}

    if ticker:
        conditions.append("a.ticker LIKE :ticker")
        params["ticker"] = f"%{ticker.upper()}%"
    if rule:
        ru = rule.upper()
        if ru == "ALL":
            pass  # no rule filter and no default exclusions
        elif ru == "NOISY":
            # Explicit opt-in to the high-volume sources only.
            conditions.append("a.rule IN ('RULE_07', 'RULE_OSINT', 'RULE_REDDIT')")
        else:
            conditions.append("a.rule = :rule")
            params["rule"] = ru
    else:
        # No rule param → hide high-volume noise rules from the default view
        conditions.append(
            "a.rule NOT IN ('RULE_07', 'RULE_OSINT', 'RULE_REDDIT')"
        )
    if severity:
        conditions.append("a.severity = :severity")
        params["severity"] = severity.upper()
    elif severity_min:
        sev = severity_min.upper()
        if sev == "CRITICAL":
            conditions.append("a.severity = 'CRITICAL'")
        elif sev == "HIGH":
            conditions.append("a.severity IN ('HIGH', 'CRITICAL')")
        # MEDIUM = all severities, no filter needed
    if watchlist:
        conditions.append(
            "EXISTS (SELECT 1 FROM watchlist w WHERE a.ticker LIKE '%' || w.symbol || '%')"
        )
    if since:
        conditions.append("datetime(a.created_at) > datetime(:since)")
        params["since"] = since.replace("Z", "").replace("T", " ")

    return conditions, params


@router.get("/count")
def count_alerts(
    hours: int          = Query(default=24, ge=1, le=8760),
    days: int           = Query(default=None),
    since: str | None   = Query(default=None),
    severity: str | None      = Query(default=None),
    severity_min: str | None  = Query(default=None),
    rule: str | None    = Query(default=None),
    ticker: str | None  = Query(default=None),
):
    """Lightweight count endpoint — used for nav badge and new-since-last-visit banner."""
    conn = db_connection()

    effective_days = days if days is not None else math.ceil(hours / 24)
    conditions, params = _build_conditions(
        days=effective_days,
        ticker=ticker,
        rule=rule,
        severity=severity,
        severity_min=severity_min,
        watchlist=False,
        since=since,
    )
    where = " AND ".join(conditions)
    count = conn.execute(
        f"SELECT COUNT(*) FROM alerts a WHERE {where}", params
    ).fetchone()[0]
    conn.close()
    return {"count": count}


@router.get("")
def get_alerts(
    days: int           = Query(default=30, ge=1, le=365),
    ticker: str | None  = Query(default=None),
    rule: str | None    = Query(default=None),
    severity: str | None      = Query(default=None),
    severity_min: str | None  = Query(default=None),
    watchlist: bool     = Query(default=False),
    limit: int          = Query(default=100, ge=1, le=500),
    page: int | None    = Query(default=None, ge=1),
    per_page: int       = Query(default=PER_PAGE, ge=1, le=100),
    since: str | None   = Query(default=None),
    mode: str | None    = Query(default=None, description="overwatch | scanner"),
):
    conn = db_connection()

    conditions, params = _build_conditions(
        days=days,
        ticker=ticker,
        rule=rule,
        severity=severity,
        severity_min=severity_min,
        watchlist=watchlist,
        since=since,
    )

    # Two-mode intelligence view (spec §11 / Priority 7).
    order = "datetime(a.created_at) DESC"
    if mode == "scanner":
        # Retail/penny: near-term catalysts, freshest + highest opportunity first.
        conditions.append("a.time_horizon IN ('IMMEDIATE','SHORT')")
        order = "a.opportunity_score DESC, datetime(a.created_at) DESC"
    elif mode == "overwatch":
        # Macro: structural rules, best-supported theses first; de-noise.
        conditions.append("a.rule NOT IN ('RULE_07','RULE_REDDIT')")
        order = "a.evidence_confidence DESC, datetime(a.created_at) DESC"
    where = " AND ".join(conditions)

    base_select = f"""
        SELECT
            a.id, a.rule, a.ticker, a.severity, a.headline, a.detail,
            a.tags, a.member_id, a.created_at, a.source_url,
            a.time_horizon, a.novelty_score, a.opportunity_score,
            a.evidence_confidence, a.source_quality, a.verify_url,
            a.theme_id, a.lifecycle_stage,
            m.full_name, m.party, m.state
        FROM alerts a
        LEFT JOIN members m ON a.member_id = m.bioguide_id
        WHERE {where}
        ORDER BY {order}
    """

    if page is not None:
        # Paginated response
        total = conn.execute(
            f"SELECT COUNT(*) FROM alerts a LEFT JOIN members m ON a.member_id = m.bioguide_id WHERE {where}",
            params,
        ).fetchone()[0]
        offset = (page - 1) * per_page
        rows = conn.execute(
            base_select + f" LIMIT {per_page} OFFSET {offset}", params
        ).fetchall()
        conn.close()
        pages = max(1, math.ceil(total / per_page))
        return {
            "items": [_row_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "pages": pages,
            "per_page": per_page,
        }
    else:
        # Legacy flat-list response (used by widgets, section pages)
        params["limit"] = limit
        rows = conn.execute(base_select + " LIMIT :limit", params).fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]


@router.get("/{alert_id}/context")
def get_alert_context(alert_id: int):
    conn = db_connection()
    alert = conn.execute(
        "SELECT rule, ticker FROM alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    if not alert:
        conn.close()
        return JSONResponse(status_code=404, content={"error": "Alert not found"})

    rule   = alert["rule"]
    ticker = alert["ticker"]

    prior = conn.execute(
        """SELECT id, created_at FROM alerts
           WHERE rule = ? AND ticker = ? AND id < ?
           ORDER BY id DESC LIMIT 1""",
        (rule, ticker, alert_id),
    ).fetchone()

    if not prior:
        conn.close()
        return {
            "has_prior": False,
            "rule": rule,
            "ticker": ticker,
            "message": f"First time {rule} has fired on {ticker}",
        }

    backtest = conn.execute(
        "SELECT return_30d FROM backtest_results WHERE alert_id = ?",
        (prior["id"],),
    ).fetchone()
    conn.close()

    return {
        "has_prior":      True,
        "rule":           rule,
        "ticker":         ticker,
        "prior_date":     prior["created_at"],
        "prior_alert_id": prior["id"],
        "return_30d":     backtest["return_30d"] if backtest else None,
    }


@router.get("/{alert_id}")
def get_alert(alert_id: int):
    conn = db_connection()
    row = conn.execute(
        """
        SELECT
            a.id, a.rule, a.ticker, a.severity, a.headline, a.detail,
            a.tags, a.member_id, a.created_at, a.source_url,
            m.full_name, m.party, m.state
        FROM alerts a
        LEFT JOIN members m ON a.member_id = m.bioguide_id
        WHERE a.id = ?
        """,
        (alert_id,),
    ).fetchone()
    conn.close()

    if not row:
        return JSONResponse(status_code=404, content={"error": "Alert not found"})

    return _row_to_dict(row)
