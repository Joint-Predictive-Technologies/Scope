"""
Aggregation endpoints for the Sector War Room and Foreign Influence pages.

Both reuse existing helpers (SECTOR_MAP, classify_sector, calculate_heat_index)
so there is a single source of truth for sector membership and heat scoring.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from jpt_common import db_connection, SECTOR_MAP, calculate_heat_index

router = APIRouter()


def _slug(name: str) -> str:
    return name.lower().replace(" & ", "-").replace(" ", "-")


# slug -> canonical SECTOR_MAP key
_SLUG_TO_SECTOR = {_slug(k): k for k in SECTOR_MAP}


@router.get("/sector/{sector_slug}")
def sector_war_room(sector_slug: str, days: int = Query(default=30, ge=1, le=90)):
    sector = _SLUG_TO_SECTOR.get(sector_slug.lower())
    if not sector:
        return JSONResponse(status_code=404, content={
            "error": "Unknown sector",
            "known": list(_SLUG_TO_SECTOR.keys()),
        })

    tickers = SECTOR_MAP[sector]
    ph = ",".join("?" * len(tickers))
    conn = db_connection()

    heat = calculate_heat_index(sector, conn, days=days)

    active_signals = [
        dict(r) for r in conn.execute(
            f"""
            SELECT id, rule, ticker, severity, headline, created_at
            FROM alerts
            WHERE ticker IN ({ph})
              AND severity IN ('HIGH','CRITICAL')
              AND rule NOT IN ('RULE_07','RULE_OSINT','RULE_REDDIT')
              AND datetime(created_at) >= datetime('now', ?)
            ORDER BY datetime(created_at) DESC LIMIT 40
            """,
            [*tickers, f"-{days} days"],
        ).fetchall()
    ]

    # Members most active in this sector's tickers (last 90 days).
    like_clauses = " OR ".join(["t.raw_ticker_string LIKE ?"] * len(tickers))
    like_params = [f"%{t}%" for t in tickers]
    top_members = [
        dict(r) for r in conn.execute(
            f"""
            SELECT m.bioguide_id, m.full_name, m.party, m.state,
                   COUNT(*) AS trade_count
            FROM transactions t
            JOIN members m ON t.member_id = m.bioguide_id
            WHERE ({like_clauses})
              AND t.transaction_date >= date('now', '-90 days')
            GROUP BY m.bioguide_id
            ORDER BY trade_count DESC LIMIT 10
            """,
            like_params,
        ).fetchall()
    ]

    def _rule_alerts(rule: str, limit: int = 15):
        return [
            dict(r) for r in conn.execute(
                f"""
                SELECT id, rule, ticker, severity, headline, created_at
                FROM alerts
                WHERE rule = ? AND ticker IN ({ph})
                ORDER BY datetime(created_at) DESC LIMIT ?
                """,
                [rule, *tickers, limit],
            ).fetchall()
        ]

    regulatory = _rule_alerts("RULE_08")
    contracts  = _rule_alerts("RULE_11")
    patents    = _rule_alerts("RULE_14")
    foreign    = _rule_alerts("RULE_12")

    # Per-ticker signal counts for the watchlist strip.
    ticker_counts = {
        r[0]: r[1] for r in conn.execute(
            f"""
            SELECT ticker, COUNT(*) FROM alerts
            WHERE ticker IN ({ph})
              AND datetime(created_at) >= datetime('now', ?)
            GROUP BY ticker
            """,
            [*tickers, f"-{days} days"],
        ).fetchall()
    }

    conn.close()
    return {
        "sector":         sector,
        "slug":           sector_slug.lower(),
        "heat":           heat,
        "active_signals": active_signals,
        "top_members":    top_members,
        "regulatory":     regulatory,
        "contracts":      contracts,
        "patents":        patents,
        "foreign":        foreign,
        "tickers":        [{"ticker": t, "signals": ticker_counts.get(t, 0)} for t in tickers],
    }


@router.get("/foreign-influence")
def foreign_influence(days: int = Query(default=180, ge=1, le=365)):
    conn = db_connection()

    # FARA-style foreign filings grouped by principal/country.
    fara = [
        dict(r) for r in conn.execute(
            """
            SELECT registrant, foreign_principal, country, period_start,
                   total_receipts, issues_lobbied
            FROM fara_filings
            ORDER BY total_receipts DESC LIMIT 40
            """
        ).fetchall()
    ]

    by_country: dict[str, dict] = {}
    for f in fara:
        c = (f.get("country") or "Unknown").strip() or "Unknown"
        b = by_country.setdefault(c, {"country": c, "filings": 0, "total_receipts": 0.0, "principals": set()})
        b["filings"] += 1
        b["total_receipts"] += float(f.get("total_receipts") or 0)
        if f.get("foreign_principal"):
            b["principals"].add(f["foreign_principal"])
    country_rollup = sorted(
        ({**v, "principals": sorted(v["principals"])[:6]} for v in by_country.values()),
        key=lambda x: x["total_receipts"], reverse=True,
    )

    def _themed(*terms):
        clause = " OR ".join(["LOWER(headline) LIKE ?"] * len(terms))
        params = [f"%{t.lower()}%" for t in terms]
        return [
            dict(r) for r in conn.execute(
                f"""
                SELECT id, rule, ticker, severity, headline, created_at
                FROM alerts
                WHERE rule IN ('RULE_12','RULE_13')
                  AND ({clause})
                  AND datetime(created_at) >= datetime('now', ?)
                ORDER BY datetime(created_at) DESC LIMIT 20
                """,
                [*params, f"-{days} days"],
            ).fetchall()
        ]

    aipac = _themed("israel", "aipac", "pro-israel")
    saudi = _themed("saudi", "arabia")
    china = _themed("china", "chinese", "hong kong")

    # RULE_13 conflict alerts (member trade vs. funded sector).
    conflicts = [
        dict(r) for r in conn.execute(
            """
            SELECT id, rule, ticker, severity, headline, member_id, created_at
            FROM alerts
            WHERE rule = 'RULE_13'
              AND datetime(created_at) >= datetime('now', ?)
            ORDER BY datetime(created_at) DESC LIMIT 25
            """,
            (f"-{days} days",),
        ).fetchall()
    ]

    conn.close()
    return {
        "countries":  country_rollup,
        "aipac":      aipac,
        "saudi":      saudi,
        "china":      china,
        "conflicts":  conflicts,
        "fara_count": len(fara),
    }
