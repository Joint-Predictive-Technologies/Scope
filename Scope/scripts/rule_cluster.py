#!/usr/bin/env python3
"""
RULE_CLUSTER — Congressional cluster detection.

Three or more members of congress trading the same ticker within 72 hours with
no public catalyst is one of the strongest signals in congressional data — the
pattern needs no LLM to interpret. Distinct from RULE_02 (7-day, 3+ members):
this is the tighter 72h window with direction + a public-catalyst check.

Public-catalyst proxy: whether a Federal Register (RULE_08) or government
contract (RULE_11) alert fired on the same ticker in the last 72h. When one
exists, the cluster is still emitted but flagged as "catalyst present" and
capped at HIGH; a clean cluster (no catalyst) keeps full severity.

Scheduled every 4h (see api/main.py).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, insert_alert, record_activity

RULE = "RULE_CLUSTER"


def _catalyst_present(conn, ticker: str) -> bool:
    """True if a public-catalyst rule (Fed Register / contract) fired recently."""
    row = conn.execute(
        """SELECT 1 FROM alerts
           WHERE rule IN ('RULE_08','RULE_11')
             AND ticker = ?
             AND datetime(created_at) >= datetime('now','-72 hours')
           LIMIT 1""",
        (ticker,),
    ).fetchone()
    return row is not None


def run(emit: bool = True) -> None:
    _t0 = time.time()
    conn = db_connection()
    scanned = flagged = emitted = 0

    # Normalize ticker (strip $, upper) and cluster within 72h.
    clusters = conn.execute(
        """
        SELECT UPPER(TRIM(REPLACE(raw_ticker_string,'$',''))) AS ticker,
               COUNT(DISTINCT member_id) AS member_count,
               GROUP_CONCAT(DISTINCT member_id) AS member_ids,
               SUM(CASE WHEN LOWER(transaction_type) LIKE '%purchase%'
                         OR LOWER(transaction_type) LIKE '%buy%' THEN 1 ELSE 0 END) AS buys,
               SUM(CASE WHEN LOWER(transaction_type) LIKE '%sale%'
                         OR LOWER(transaction_type) LIKE '%sell%' THEN 1 ELSE 0 END) AS sells,
               MIN(transaction_date) AS first_trade,
               MAX(transaction_date) AS last_trade
        FROM transactions
        WHERE date(transaction_date) >= date('now','-3 days')
          AND member_id IS NOT NULL
          AND raw_ticker_string IS NOT NULL
          AND TRIM(REPLACE(raw_ticker_string,'$','')) != ''
        GROUP BY ticker
        HAVING COUNT(DISTINCT member_id) >= 3
        ORDER BY member_count DESC
        """
    ).fetchall()
    scanned = len(clusters)

    for c in clusters:
        ticker = (c["ticker"] or "").split()[0] if c["ticker"] else ""
        if not ticker:
            continue
        member_count = c["member_count"]
        buys, sells = c["buys"] or 0, c["sells"] or 0
        flagged += 1

        if buys > sells * 2:
            direction, base = "buying", True
        elif sells > buys * 2:
            direction, base = "selling", True
        else:
            direction, base = "mixed activity", False

        catalyst = _catalyst_present(conn, ticker)
        if not base:
            severity = "MEDIUM"
        elif catalyst:
            severity = "HIGH"
        else:
            severity = "CRITICAL" if member_count >= 5 else "HIGH"

        # Dedup: one cluster alert per ticker per 7 days.
        if conn.execute(
            "SELECT 1 FROM alerts WHERE rule=? AND ticker=? "
            "AND datetime(created_at) >= datetime('now','-7 days') LIMIT 1",
            (RULE, ticker),
        ).fetchone():
            continue

        member_ids = [m.strip() for m in (c["member_ids"] or "").split(",") if m.strip()]
        names = []
        for mid in member_ids[:5]:
            m = conn.execute(
                "SELECT full_name, party, state FROM members WHERE bioguide_id = ?", (mid,)
            ).fetchone()
            if m:
                names.append(f"{m['full_name']} ({m['party'] or '?'}-{m['state'] or '?'})")
        names_str = ", ".join(names[:3])
        if len(names) > 3:
            names_str += f" and {len(names) - 3} others"

        catalyst_note = (" A public catalyst (regulation/contract) also fired on "
                         f"{ticker} in this window." if catalyst
                         else " No public catalyst detected in this window.")
        headline = (f"Congressional cluster: {member_count} members {direction} {ticker} "
                    f"within 72h{'' if catalyst else ' — no public catalyst'}")
        why = (f"{member_count} congress members independently disclosed {direction} in "
               f"{ticker} between {c['first_trade']} and {c['last_trade']}. "
               f"Members: {names_str or 'n/a'}.{catalyst_note} "
               "Cluster trades are among the strongest signals of non-public information "
               "flow in congressional data.")

        if emit:
            insert_alert(
                conn, rule=RULE, ticker=ticker, severity=severity,
                headline=headline, why_matters=why,
                distinct_rule_count=member_count,
                event_date=c["last_trade"],
                tags={"member_ids": member_ids, "direction": direction,
                      "member_count": member_count, "buy_count": buys,
                      "sell_count": sells, "catalyst_present": catalyst},
            )
            emitted += 1
            print(f"[RULE_CLUSTER] EMIT {severity}: {headline}")

    conn.close()
    record_activity(RULE, scanned=scanned, flagged=flagged, emitted=emitted,
                    duration_seconds=round(time.time() - _t0, 2))
    print(f"[RULE_CLUSTER] {scanned} tickers checked, {emitted} cluster alerts emitted")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RULE_CLUSTER — congressional cluster detection")
    p.add_argument("--emit-alerts", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(emit=not args.dry_run)
