from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection
from api.receipts import build_receipts


router = APIRouter()

PER_PAGE = 20


@router.get("/rules")
def rule_facets(days: int = Query(default=90, ge=1, le=3650)):
    """Distinct rules that have actually fired in the window, with counts — so the
    feed filter only lists real, non-empty rules. Sorted by fire count desc."""
    conn = db_connection()
    rows = conn.execute(
        """SELECT rule, COUNT(*) AS count FROM alerts
           WHERE rule IS NOT NULL AND rule != ''
             AND datetime(created_at) >= datetime('now', ?)
           GROUP BY rule
           ORDER BY count DESC, rule ASC""",
        (f"-{int(days)} days",),
    ).fetchall()
    conn.close()
    return [{"rule": r["rule"], "count": r["count"]} for r in rows]


def _row_to_dict(row) -> dict:
    return dict(row)


# ── source documents ────────────────────────────────────────────────────────
# 🔴 A SOURCE LINK IS A REAL DOCUMENT OR IT IS NOTHING.
#
# Resolves `document_url` to the actual originating filing, and returns None when
# no such document can be produced from stored data. It NEVER constructs a search
# query, a landing page, or a guessed path — a broken or generic link is worse
# than none, because it looks authoritative while lying.
#
# Measured before this was written (local DB, 3,347 alerts), by fetching, not by
# checking non-emptiness:
#   disclosures-clerk.house.gov/public_disc/ptr-pdfs/  -> 403   (274 alerts pointed here)
#   lda.senate.gov/filings/public/filing/search/       -> 403   (554 alerts pointed here)
#   news.google.com/search?q=...                       -> a SEARCH QUERY, on all 387 RULE_OSINT rows
# Both 403s would have passed any "field is non-empty" check.
#
# What IS recoverable, because the real identifier was already stored and simply
# never used:
#   RULE_01/01B  filings.raw_url is populated on 823/823 filings; 85 of 192 alerts
#                join to one on (member_id, ticker, transaction_date).
#   RULE_11      alerts.award_key is populated on 62 of 102, and
#                usaspending.gov/award/<key>/ returns 200. The page previously read
#                tags.split('|')[2] — the same positional-tag fragility behind the
#                comma-in-name bugs.


def _document_urls(rows, conn) -> dict:
    """alert id -> real document URL, only where one genuinely exists.

    Batched deliberately: one query per rule family, not one per alert.
    """
    out, index_out = {}, {}
    keys = rows[0].keys() if rows else []
    ids_01b = [r["id"] for r in rows if r["rule"] in ("RULE_01", "RULE_01B")]
    if ids_01b and "event_date" in keys:
        # 🔴 JOIN ON `raw_ticker_string`, NOT `ticker_id`. This originally used
        # `transactions.ticker_id -> tickers.id` and matched 85/192 locally — but on
        # PROD `ticker_id` is NULL on all 10,232 rows, so the join returned
        # **0 of 2,168**. The entire headline category silently produced nothing in
        # production while looking fine on the dev database.
        # `raw_ticker_string` is populated in both, and normalising both sides the
        # same way is what makes it portable.
        #
        # `transaction_date` is ISO on 10,230 prod rows and MM/DD/YYYY on 2, so both
        # shapes are accepted rather than assuming one.
        #
        # ⚠️ AMBIGUITY IS DROPPED, NOT GUESSED. 14 alerts match more than one
        # distinct filing (same member, ticker and date across two filings). There
        # is no basis to call either one "the" source, so those get no link —
        # picking one arbitrarily would be a receipt that might not be the receipt.
        q = """
            SELECT a.id AS alert_id, MIN(f.raw_url) AS url, COUNT(DISTINCT f.raw_url) AS n
            FROM alerts a
            JOIN transactions t
              ON t.member_id = a.member_id
             AND UPPER(TRIM(REPLACE(t.raw_ticker_string, '$', '')))
               = UPPER(TRIM(REPLACE(a.ticker, '$', '')))
             AND ( t.transaction_date = a.event_date
                   OR substr(t.transaction_date, 7, 4) || '-'
                      || substr(t.transaction_date, 1, 2) || '-'
                      || substr(t.transaction_date, 4, 2) = a.event_date )
            JOIN filings f ON f.id = t.filing_id
            WHERE a.id IN (%s)
              AND f.raw_url IS NOT NULL AND TRIM(f.raw_url) != ''
            GROUP BY a.id
            HAVING COUNT(DISTINCT f.raw_url) = 1
        """ % ",".join("?" * len(ids_01b))
        for r in conn.execute(q, ids_01b):
            out.setdefault(r["alert_id"], r["url"])

    # ── RULE_06: a real, correct COMPANY INDEX — not this filing ────────────
    # Kept separate from document_url on purpose. EDGAR's Form-4 list for the
    # right company is real and useful, but it is an INDEX, and calling it
    # "the source" promises a receipt it does not deliver.
    #
    # 🔴 The old link put the TICKER in EDGAR's CIK parameter. Measured on a real
    # sample of 8 RULE_06 tickers: 3 returned "No matching companies" — a 37.5%
    # silent failure rate on a guessed construction. `tickers.cik` is populated on
    # 10,619/10,619 rows, and the real CIK resolves every one of those three
    # (SFD -> Smithfield Foods, SEZL -> Sezzle, OMDA -> Omada Health).
    ids_06 = [r["id"] for r in rows if r["rule"] == "RULE_06"]
    if ids_06:
        q6 = """
            SELECT a.id AS alert_id, tk.cik AS cik
            FROM alerts a
            JOIN tickers tk ON tk.symbol = REPLACE(a.ticker, '$', '')
            WHERE a.id IN (%s) AND tk.cik IS NOT NULL AND TRIM(tk.cik) != ''
        """ % ",".join("?" * len(ids_06))
        for r in conn.execute(q6, ids_06):
            cik = str(r["cik"]).strip()
            if cik.isdigit():
                index_out[r["alert_id"]] = (
                    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                    "&CIK=%s&type=4&dateb=&owner=include&count=40" % cik.zfill(10))

    # ── RULE_OSINT / RULE_REDDIT: the real article URL is in `tags` ──────────
    # 🔴 FOUND BY THE VERIFIER, AFTER I HAD DECLARED THESE UNRECOVERABLE.
    # `alerts.source_url` on all 387 OSINT rows is a Google News SEARCH QUERY —
    # which is why the column looked useless. But `tags.source_url` carries the
    # actual article permalink on 387/387, and `tags` carries a reddit permalink
    # on 8/8. Sampled article URLs return 200 (dailytrust, irishtimes, yahoo…).
    # `osint_region.html` was already using them.
    #
    # Rendering "no source document" over a stored, working document link is the
    # same failure this change exists to fix, pointing the other way.
    for r in rows:
        if r["rule"] not in ("RULE_OSINT", "RULE_REDDIT") or r["id"] in out:
            continue
        try:
            tj = json.loads(r["tags"] or "{}")
        except Exception:
            continue
        if not isinstance(tj, dict):
            continue
        u = (tj.get("source_url") or tj.get("permalink") or tj.get("url") or "").strip()
        # Never re-admit the search query the column holds.
        if u.startswith("http") and "news.google.com/search" not in u:
            out[r["id"]] = u

    if "award_key" in keys:
        for r in rows:
            if r["rule"] != "RULE_11":
                continue
            key = (r["award_key"] or "").strip()
            # An award id is opaque but structured; only emit a link for something
            # that actually looks like one, never for arbitrary text.
            if key and key.upper().startswith(("CONT_AWD", "ASST_NON", "CONT_IDV")):
                out[r["id"]] = "https://www.usaspending.gov/award/%s/" % key
    return out, index_out


def _rows_with_receipts(rows, conn) -> list[dict]:
    """Attach the server-assembled factual receipts block to each alert."""
    docs, indexes = _document_urls(rows, conn)
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["receipts"] = build_receipts(d, conn)
        # None means "no real source document exists for this alert" — the client
        # renders an explicit unavailable state, NOT a link.
        d["document_url"] = docs.get(d["id"])
        # A real, correct index at the source system — explicitly NOT this filing.
        d["source_index_url"] = indexes.get(d["id"])
        out.append(d)
    return out


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
            a.event_date, a.award_key,
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
        items = _rows_with_receipts(rows, conn)
        conn.close()
        pages = max(1, math.ceil(total / per_page))
        return {
            "items": items,
            "total": total,
            "page": page,
            "pages": pages,
            "per_page": per_page,
        }
    else:
        # Legacy flat-list response (used by widgets, section pages)
        params["limit"] = limit
        rows = conn.execute(base_select + " LIMIT :limit", params).fetchall()
        items = _rows_with_receipts(rows, conn)
        conn.close()
        return items


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
            a.event_date, a.award_key,
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

    d = _row_to_dict(row)
    conn2 = db_connection()
    _docs, _idx = _document_urls([row], conn2)
    d["document_url"] = _docs.get(d["id"])
    d["source_index_url"] = _idx.get(d["id"])
    conn2.close()
    return d
