"""Read-only surface for insider clusters. Emits nothing, scores nothing, ranks nothing.

⚠️ THIS ROUTER RESTATES NO THRESHOLD. Every parameter is IMPORTED from the module, so the
published `parameters` block cannot drift from the logic that produced the clusters. A
surface that documents a window it does not use is a lie told by layout.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Query

from jpt_common import db_connection
from scripts.insider_clusters import (LARGE_CAP_MIN, MIN_INSIDERS, SCAN_HORIZON_DAYS,
                                      WINDOW_DAYS, corpus_meta, find_clusters)

router = APIRouter()

_DISCLAIMER = (
    "Discovery surface, not a signal. Two or more insiders buying the same small-cap in "
    "a short window is a place to LOOK, not evidence of anything. This carries no "
    "confidence score and contributes no instrument to the convergence gate."
)


@router.get("/insider-clusters")
def insider_clusters(
    window_days: int = Query(WINDOW_DAYS, ge=1, le=90),
    min_insiders: int = Query(MIN_INSIDERS, ge=2, le=10),
):
    conn = db_connection()
    error = None
    meta = None
    try:
        clusters = find_clusters(conn, window_days=window_days,
                                 min_insiders=min_insiders)
        meta = corpus_meta(conn, SCAN_HORIZON_DAYS)
    except sqlite3.OperationalError as exc:
        # NARROW ON PURPOSE. A bare `except Exception` here previously turned a missing
        # table into `count: 0` with HTTP 200 — a schema failure indistinguishable from
        # an honest quiet day, on a surface whose entire claim is that its zeros are
        # honest. Anything that is not a missing table is re-raised as a 500.
        if "no such table" not in str(exc):
            raise
        clusters = []
        error = ("The Form 4 transaction store does not exist yet — run "
                 "scripts/ingest_form4_transactions.py. This is an EMPTY STORE, not an "
                 "absence of clusters.")
    finally:
        conn.close()

    return {
        "kind": "discovery",
        "disclaimer": _DISCLAIMER,
        "parameters": {
            "window_days": window_days,
            "min_insiders": min_insiders,
            "scan_horizon_days": SCAN_HORIZON_DAYS,
            "market_cap_ceiling": LARGE_CAP_MIN,
            "tunable": True,
            "note": ("Window is on the TRADE date; the scan horizon covers Form 4 "
                     "disclosure lag. None of these thresholds is calibrated."),
        },
        "count": len(clusters),
        "error": error,
        "corpus": meta,
        "clusters": [{
            "ticker": c["ticker"],
            "insider_count": len(c["insiders"]),
            "insiders": [{"name": i["name"], "title": i["title"]}
                         for i in c["insiders"]],
            "combined_value": c["total_value"],
            "accessions": c["accessions"],
            "window": {"start": c["window_start"], "end": c["window_end"]},
            "market_cap": c["market_cap"],
            # COUNTED IN TRADES, the same unit as `combined_value`. Counting these in
            # filings while summing in trades made the number a function of how an agent
            # packaged the legs.
            "undisclosed_10b5_1_trades": c["undisclosed_10b5_1_trades"],
            "zero_value_trades": c["zero_value_trades"],
            # Published BESIDE the dollars, deliberately, and SPLIT by reason.
            # `combined_value` alone said nothing about legs the identity filter removed
            # — which is how a cluster once lost 68% of its dollars unnoticed. And
            # "resolved, not a person" is a different fact from "could not resolve": the
            # latter means a rate-limited worker may publish fewer insiders than an
            # unthrottled one, so the cluster is marked provisional rather than quietly
            # short.
            "legs_dropped_unidentified": c["dropped_unidentified"],
            "legs_dropped_institution": c["dropped_institution"],
            "legs_dropped_unresolved": c["dropped_unresolved"],
            "provisional": c["provisional"],
        } for c in clusters],
    }
