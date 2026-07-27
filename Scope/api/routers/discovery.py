"""Read-only discovery watch pool.

SELECT ONLY. This router never writes — the pool is populated by
`scripts/rule_reddit_discovery.py` and read here for the future discovery page.

⚠️ WHAT THIS IS NOT. Not a signal, not a recommendation, and NOT a gate leg. Every
payload is framed as "unusual traction, for review". There is deliberately no
confidence score, no predicted direction and no ranking that implies conviction —
because this is the first thing in Scope with NO GROUND TRUTH. "Was this spike worth
surfacing?" cannot be answered until months of watching, and inventing a number to
paper over that would be the dishonest move.

Discovery contributes NOTHING to RULE_10. RULE_REDDIT stays in RULE_10_EXCLUDED and
`discovery_watch` is read by no corroboration path — asserted in
tests/test_reddit_discovery.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from jpt_common import db_connection

router = APIRouter()

# The one thing the payload must never grow: a number that looks like a prediction.
_DISCLAIMER = ("Unusual reddit traction relative to this ticker's own baseline. "
               "A review candidate, not a signal — no predictive confidence is implied "
               "and this contributes nothing to corroboration.")


@router.get("/api/discovery")
def discovery_pool(limit: int = Query(50, ge=1, le=200),
                   order: str = Query("recent", pattern="^(recent|deviation)$")):
    """The watch pool, ordered for review."""
    # `deviation` is NULL for cold-start candidates — they have no baseline, so no
    # multiple exists. Sorting them together with real multiples would rank a raw
    # mention count against a ratio; NULLs go last and order among themselves by count.
    order_by = ("datetime(last_discovered) DESC" if order == "recent"
                else "deviation IS NULL, deviation DESC, mention_count DESC, "
                     "datetime(last_discovered) DESC")
    conn = db_connection()
    try:
        rows = conn.execute(
            f"""SELECT ticker, first_discovered, last_discovered, times_discovered,
                       mention_count, baseline, deviation, market_cap, trigger,
                       subreddits, sample_days
                FROM discovery_watch ORDER BY {order_by} LIMIT ?""", (limit,)
        ).fetchall()
    except Exception:
        rows = []          # pool not yet created — an empty pool is a valid state
    finally:
        conn.close()

    return {
        "kind": "discovery_watch_pool",
        "disclaimer": _DISCLAIMER,
        "count": len(rows),
        "candidates": [{
            "ticker": r["ticker"],
            "first_discovered": r["first_discovered"],
            "last_discovered": r["last_discovered"],
            "times_discovered": r["times_discovered"],
            "traction": {
                "mentions_today": r["mention_count"],
                "baseline_median": r["baseline"],
                # null for cold starts — they have no baseline, so no multiple exists.
                # It used to carry the raw mention count here, which made one field mean
                # two different things depending on the branch.
                "deviation_multiple": r["deviation"],
                "trigger": r["trigger"],
                # days WITH DATA inside the baseline window, not a global count — an
                # outage used to show "31 days" behind a baseline built from zero.
                "baseline_days_with_data": r["sample_days"],
            },
            "market_cap": r["market_cap"],
            "subreddits": (r["subreddits"] or "").split(",") if r["subreddits"] else [],
        } for r in rows],
    }
