"""Read-only ticker COVERAGE list.

SELECT ONLY. Populated by `scripts/rule_reddit_collector.py`, read here for a future page.

⚠️ THIS IS NOT A WATCHLIST AND NOT A RECOMMENDATION. It is a list of ticker NAMES that
exist — coverage, so the real instruments (insider clusters, congressional trades,
contracts) have something to cross-reference against. A ticker being here means only
that someone cashtagged it on a post a human engaged with, and that it is not a
confirmed large cap. It means nothing else. There is no confidence, no direction, no
ranking, and `times_seen` is a COUNT, not a score — a name mentioned often is not a
better name.

Collection contributes NOTHING to RULE_10: RULE_COLLECTOR and RULE_REDDIT are both in
RULE_10_EXCLUDED and no corroboration path reads `ticker_universe`. Asserted in
tests/test_reddit_collector.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from jpt_common import db_connection

router = APIRouter()

# The one thing the payload must never grow: a number that looks like a prediction.
_DISCLAIMER = ("Coverage, not a signal. These are ticker NAMES collected so the real "
               "instruments have something to cross-reference against — presence here "
               "means only that the name exists and is not a confirmed large cap. No "
               "confidence, no direction, no ranking; it contributes nothing to "
               "corroboration.")


@router.get("/api/universe")
def coverage_list(limit: int = Query(200, ge=1, le=1000),
                  cap_status: str = Query(None, pattern="^(small|unknown)$")):
    """The coverage list, ordered by recency of last sighting.

    Ordered by RECENCY, deliberately — not by `times_seen`. Ordering by mention count
    would rank names against each other, which is exactly the "reddit found something"
    reading this list must not support.
    """
    #  rows are KEPT in the table so a wrongful exclusion stays visible and
    # re-checkable, but they are not coverage — filter them out of the default listing.
    where, params = "WHERE cap_status != 'excluded'", []
    if cap_status:
        where, params = "WHERE cap_status = ?", [cap_status]
    conn = db_connection()
    try:
        rows = conn.execute(
            f"""SELECT ticker, first_collected_at, last_seen_at, times_seen,
                       market_cap, cap_status, source
                FROM ticker_universe {where}
                ORDER BY datetime(last_seen_at) DESC LIMIT ?""",
            (*params, limit)).fetchall()
    except Exception:
        rows = []          # not yet created — an empty universe is a valid state
    finally:
        conn.close()

    return {
        "kind": "ticker_coverage_list",
        "disclaimer": _DISCLAIMER,
        "count": len(rows),
        "tickers": [{
            "ticker": r["ticker"],
            "first_collected_at": r["first_collected_at"],
            "last_seen_at": r["last_seen_at"],
            "times_seen": r["times_seen"],       # a count, not a score
            "market_cap": r["market_cap"],
            "cap_status": r["cap_status"],       # small | unknown
            "source": r["source"],
        } for r in rows],
    }
