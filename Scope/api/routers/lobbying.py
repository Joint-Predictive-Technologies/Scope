"""
Lobbying dataset API — top spenders from the Senate LDA (populated by
scripts/ingest_lobbying.py). Powers the browsable Lobbying page beyond the
RULE_09 spike alerts.
"""
from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, Query

from jpt_common import db_connection, resolve_org

router = APIRouter()


@router.get("/top")
def top_spenders(
    q: str = Query(default=""),
    category: str = Query(default=""),
    year: int | None = Query(default=None),
    foreign_only: bool = Query(default=False),
    limit: int = Query(default=60, ge=1, le=200),
):
    """Aggregated lobbying spend by client, most recent activity first."""
    conn = db_connection()

    conds = ["1=1"]
    params: list = []
    if q:
        conds.append("(client_name LIKE ? OR registrant_name LIKE ? OR issues LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    if category:
        conds.append("category = ?")
        params.append(category)
    if year:
        conds.append("filing_year = ?")
        params.append(year)
    if foreign_only:
        conds.append("is_foreign = 1")
    where = " AND ".join(conds)

    rows = conn.execute(
        f"""
        SELECT client_name,
               MAX(category)               AS category,
               MAX(ticker)                 AS ticker,
               MAX(is_foreign)             AS is_foreign,
               SUM(amount)                 AS total_amount,
               COUNT(*)                    AS filing_count,
               MAX(filing_year)            AS latest_year,
               MAX(issues)                 AS issues,
               MAX(document_url)           AS document_url
        FROM lobbying_filings
        WHERE {where}
        GROUP BY client_name
        ORDER BY total_amount DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/entity")
def entity_profile(q: str = Query(...)):
    """
    Entity-centric influence profile. Resolves aliases (e.g. AIPAC), returns
    Senate-LDA lobbying disclosures with YoY + issues, and clearly labels the
    campaign-finance / foreign-principal sections as available or not-ingested —
    never mixing categories or inventing data.
    """
    conn = db_connection()
    rec, confidence, matched_by = resolve_org(q)
    canonical = rec["canonical"] if rec else q
    aliases = rec["aliases"] if rec else []
    sectors = rec.get("sectors", []) if rec else []
    kind = rec.get("kind", "Organization") if rec else "Organization"

    # Match lobbying filings by canonical name (and the raw query as fallback).
    like_terms = [canonical] + ([q] if q.lower() != canonical.lower() else [])
    clause = " OR ".join(["client_name LIKE ?"] * len(like_terms))
    params = [f"%{t}%" for t in like_terms]
    filings = [
        dict(r) for r in conn.execute(
            f"""
            SELECT filing_year, period, amount, issues, registrant_name,
                   category, is_foreign, document_url
            FROM lobbying_filings
            WHERE {clause}
            ORDER BY filing_year DESC, period DESC
            """,
            params,
        ).fetchall()
    ]

    # Per-year totals + YoY.
    by_year: dict = {}
    issues_count: dict = {}
    for f in filings:
        by_year[f["filing_year"]] = by_year.get(f["filing_year"], 0.0) + (f["amount"] or 0)
        for iss in (f["issues"] or "").split(","):
            iss = iss.strip()
            if iss:
                issues_count[iss] = issues_count.get(iss, 0) + 1
    # Filings per year — used to avoid a misleading YoY off a partial year.
    year_counts: dict = {}
    for f in filings:
        year_counts[f["filing_year"]] = year_counts.get(f["filing_year"], 0) + 1
    years_sorted = sorted(by_year.keys())
    yoy = None
    yoy_note = None
    if len(years_sorted) >= 2:
        ly, py = years_sorted[-1], years_sorted[-2]
        if year_counts.get(ly, 0) >= 3 and by_year[py] > 0:  # latest year ~complete
            yoy = round((by_year[ly] - by_year[py]) / by_year[py] * 100, 1)
        else:
            yoy_note = f"{ly} is a partial year ({year_counts.get(ly,0)} filings) — YoY omitted"
    top_issues = sorted(issues_count.items(), key=lambda x: -x[1])[:6]

    # Related alerts (lobbying / foreign / corroboration mentioning the entity).
    related_alerts = [
        dict(r) for r in conn.execute(
            """
            SELECT id, rule, ticker, severity, headline, created_at
            FROM alerts
            WHERE (headline LIKE ? OR headline LIKE ?)
            ORDER BY datetime(created_at) DESC LIMIT 10
            """,
            (f"%{canonical}%", f"%{q}%"),
        ).fetchall()
    ]
    conn.close()

    lda_url = ("https://lda.senate.gov/filings/public/filing/search/?registrant_name="
               + urllib.parse.quote(canonical))
    return {
        "entity": {
            "canonical": canonical,
            "aliases": aliases,
            "kind": kind,
            "sectors": sectors,
            "match_confidence": confidence,
            "matched_by": matched_by,
            "resolved": bool(rec),
        },
        "lobbying": {
            "available": bool(filings),
            "source": "Senate Lobbying Disclosure Act (LDA)",
            "filing_count": len(filings),
            "total_spend": round(sum(f["amount"] or 0 for f in filings), 2),
            "by_year": [{"year": y, "spend": round(by_year[y], 2)} for y in years_sorted],
            "yoy_pct": yoy,
            "yoy_note": yoy_note,
            "top_issues": [{"issue": i, "count": n} for i, n in top_issues],
            "filings": filings[:12],
            "source_url": lda_url,
        },
        "campaign_finance": {
            "available": False,
            "category": "FEC campaign finance / PAC",
            "note": "No FEC or PAC campaign-finance records are ingested for this "
                    "entity in the current dataset. This is a separate category "
                    "from Senate LDA lobbying and is not shown as lobbying.",
        },
        "foreign_principal": {
            "available": False,
            "category": "FARA foreign-principal registration",
            "note": "No FARA foreign-principal registration found for this entity "
                    "in the current dataset.",
        },
        "related_alerts": related_alerts,
    }


@router.get("/summary")
def summary():
    """Totals by category + overall, for the page header strip."""
    conn = db_connection()
    by_cat = [
        {"category": r[0], "total": r[1] or 0, "clients": r[2]}
        for r in conn.execute(
            """
            SELECT category, SUM(amount), COUNT(DISTINCT client_name)
            FROM lobbying_filings
            GROUP BY category
            ORDER BY SUM(amount) DESC
            """
        ).fetchall()
    ]
    overall = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT client_name), SUM(amount), "
        "SUM(CASE WHEN is_foreign=1 THEN 1 ELSE 0 END) FROM lobbying_filings"
    ).fetchone()
    conn.close()
    return {
        "filings":       overall[0] or 0,
        "clients":       overall[1] or 0,
        "total_spend":   overall[2] or 0,
        "foreign_filings": overall[3] or 0,
        "by_category":   by_cat,
    }
