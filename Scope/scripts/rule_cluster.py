#!/usr/bin/env python3
"""
RULE_CLUSTER — congressional trading clusters.

Fires when 3+ DISTINCT members trade the same (normalized) ticker inside a
rolling 72-hour window (by transaction_date — the actual event, not the
variable-lag filing_date).

Severity:  HIGH for 3-4 members, CRITICAL for 5+.
Direction: per-member buy/sell aggregated into consensus_buy / consensus_sell /
           mixed. All three fire; mixed carries has_conflict=True.
Identity:  (sorted member set, normalized ticker, direction). A member joining a
           previously-alerted smaller cluster is a NEW alert that SUPERSEDES the
           smaller one ("expanded to N members"), not a duplicate.
Novelty:   computed on the cluster identity (insert_alert's novelty_key), so a
           cluster launches novel even though RULE_01B already fired the ticker
           per member.
Horizon:   IMMEDIATE (jpt_common map). Write path: insert_alert (path a).

Windowing note (flagged as a design decision): PTRs are disclosed 30-45 days
AFTER the trade, so "trades in the last 72h of wall-clock time" is essentially
always empty. "Rolling 72-hour window" therefore means trades within 72h OF EACH
OTHER, found among recently-disclosed data: we scan a SCAN_HORIZON_DAYS window of
transaction_date and slide a 72h window over it, alerting the strongest (most
members; ties → most recent) cluster per ticker. Dedup on cluster identity keeps
the 4h re-runs from re-alerting the same cluster.

Runs every 4h (api/main.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import (
    db_connection, insert_alert, normalize_ticker, classify_sector, record_activity,
)

RULE = "RULE_CLUSTER"
WINDOW_HOURS = 72
SCAN_HORIZON_DAYS = 45     # disclosure-lag horizon for the transaction scan
MIN_MEMBERS = 3
CRITICAL_MEMBERS = 5
DEDUP_LOOKBACK_DAYS = 45   # >= scan horizon: a fired identity won't re-fire while in scope
MAX_PER_RUN = 15           # flood guard on the seeding run

HOUSE_DISCLOSURE_HUB = "https://disclosures-clerk.house.gov/FinancialDisclosure"


# Every disposal `parse_house_pdfs.normalize_transaction_type` can emit. It is the
# authority: the scheduled House parser (every 4h) is what writes `transaction_type`, and
# it returns exactly purchase / sale / sale_partial / sale_full / exchange.
#
# ⚠️ `sale_full` WAS MISSING, AND A MISSING DISPOSAL IS SILENT. It maps from the PTR code
# "S (full)" (parse_house_pdfs:262) — the least ambiguous sale there is — and without it a
# member whose only trade was a full sale classified as "other", which
# `_cluster_direction`/the caller drop from consensus entirely. So a genuine 3-member
# sell cluster containing one full-seller silently became a 2-member near-miss and never
# fired. Zero rows carry `sale_full` today, so nothing was lost yet; it is prophylaxis.
#
# Found while planning the RULE_02 directional-count fix: RULE_02 matches disposals with
# `startswith("sale")` and therefore already handled it, so the two rules disagreed on
# exactly this value. They now agree on every value the parser can produce, which is what
# `test_the_disposal_set_covers_every_type_the_parser_emits` pins.
_DISPOSAL_TYPES = {"sale", "sale_partial", "sale_full"}


def _member_direction(types: set[str]) -> str:
    """buy / sell / mixed / other from a member's transaction_type set on the ticker."""
    has_buy = "purchase" in types
    has_sell = bool(_DISPOSAL_TYPES & types)
    if has_buy and has_sell:
        return "mixed"
    if has_buy:
        return "buy"
    if has_sell:
        return "sell"
    return "other"  # exchange-only etc. — logged, not counted toward consensus


def _cluster_direction(member_dirs: dict[str, str]) -> str:
    dirs = {d for d in member_dirs.values() if d in ("buy", "sell", "mixed")}
    if dirs == {"buy"}:
        return "consensus_buy"
    if dirs == {"sell"}:
        return "consensus_sell"
    return "mixed"


def _fingerprint(members: list[str], ticker: str, direction: str) -> str:
    return f"CLUSTER::{'+'.join(sorted(members))}::{ticker}::{direction}"


def _parse_date(s: str | None):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _best_window(member_dates: dict[str, list]) -> set[str] | None:
    """Given member -> [dates], return the member set of the 72h window with the
    most distinct members (ties broken by latest window start), or None if <3."""
    starts = sorted({d for dates in member_dates.values() for d in dates})
    if not starts:
        return None
    span = timedelta(hours=WINDOW_HOURS)
    best_members: set[str] = set()
    best_start = None
    for s in starts:
        in_window = {m for m, dates in member_dates.items()
                     if any(s <= d <= s + span for d in dates)}
        if (len(in_window) > len(best_members)
                or (len(in_window) == len(best_members) and best_start is not None and s > best_start)):
            best_members, best_start = in_window, s
    return best_members if len(best_members) >= MIN_MEMBERS else None


def _gather(conn) -> dict[str, dict]:
    """ticker -> {member_id: {types,dates,sizes,name,filing_url,doc_id}} over the horizon."""
    rows = conn.execute(
        f"""
        SELECT t.member_id, t.raw_ticker_string, t.transaction_type, t.amount_band,
               t.transaction_date, tk.symbol AS resolved_symbol,
               m.full_name AS member_name, f.raw_url AS filing_url, f.doc_id AS doc_id
        FROM transactions t
        LEFT JOIN tickers  tk ON t.ticker_id = tk.id
        LEFT JOIN members  m  ON t.member_id = m.bioguide_id
        LEFT JOIN filings  f  ON t.filing_id = f.id
        WHERE t.member_id IS NOT NULL AND t.member_id != 'None'
          AND t.transaction_date >= date('now', '-{SCAN_HORIZON_DAYS} days')
          AND COALESCE(tk.symbol, t.raw_ticker_string) IS NOT NULL
          AND COALESCE(tk.symbol, t.raw_ticker_string) != ''
        """
    ).fetchall()
    by_ticker: dict[str, dict] = defaultdict(lambda: defaultdict(
        lambda: {"types": set(), "dates": [], "sizes": [], "name": None,
                 "filing_url": None, "doc_id": None}))
    for r in rows:
        ticker = normalize_ticker(r["resolved_symbol"] or r["raw_ticker_string"])
        if not ticker or " " in ticker:   # skip baskets / unresolved multi-symbol
            continue
        d = _parse_date(r["transaction_date"])
        if d is None:
            continue
        rec = by_ticker[ticker][r["member_id"]]
        if r["transaction_type"]:
            rec["types"].add(r["transaction_type"].strip().lower())
        rec["dates"].append(d)
        if r["amount_band"]:
            rec["sizes"].append(r["amount_band"])
        rec["name"] = rec["name"] or r["member_name"] or r["member_id"]
        rec["filing_url"] = rec["filing_url"] or r["filing_url"]
        rec["doc_id"] = rec["doc_id"] or r["doc_id"]
    return by_ticker


def _prior_cluster_alerts(conn, ticker: str) -> list:
    """Recent, non-superseded RULE_CLUSTER alerts on this ticker (with parsed identity)."""
    rows = conn.execute(
        f"""
        SELECT id, tags FROM alerts
        WHERE rule = ? AND ticker = ?
          AND COALESCE(lifecycle_stage,'') != 'superseded'
          AND created_at >= datetime('now', '-{DEDUP_LOOKBACK_DAYS} days')
        """,
        (RULE, ticker),
    ).fetchall()
    out = []
    for r in rows:
        try:
            tg = json.loads(r["tags"] or "{}")
        except Exception:
            tg = {}
        out.append({"id": r["id"], "members": set(tg.get("members") or []),
                    "direction": tg.get("direction")})
    return out


def _verb(direction: str) -> str:
    return {"consensus_buy": "bought", "consensus_sell": "sold"}.get(direction, "traded")


def run(dry_run: bool = False) -> dict:
    import time as _time
    _t0 = _time.time()
    load_dotenv()
    conn = db_connection()

    by_ticker = _gather(conn)
    tickers_scanned = len(by_ticker)
    qualifying = emitted = upgrades = 0
    candidates = []

    for ticker, members in by_ticker.items():
        member_dates = {mid: rec["dates"] for mid, rec in members.items()}
        window_members = _best_window(member_dates)
        if not window_members:
            continue
        # Direction only from trades inside the chosen window's members.
        member_dirs = {mid: _member_direction(members[mid]["types"]) for mid in window_members}
        counted = {mid: d for mid, d in member_dirs.items() if d in ("buy", "sell", "mixed")}
        if len(counted) < MIN_MEMBERS:
            continue
        qualifying += 1
        candidates.append((ticker, members, counted, member_dirs))

    # Strongest first; cap the seeding run.
    candidates.sort(key=lambda c: len(c[2]), reverse=True)
    for ticker, members, counted, member_dirs in candidates[:MAX_PER_RUN]:
        member_ids = sorted(counted)
        direction = _cluster_direction(counted)
        fp = _fingerprint(member_ids, ticker, direction)
        n = len(member_ids)
        severity = "CRITICAL" if n >= CRITICAL_MEMBERS else "HIGH"
        has_conflict = direction == "mixed"

        prior = _prior_cluster_alerts(conn, ticker)
        cur_set = set(member_ids)
        if any(p["members"] == cur_set and p["direction"] == direction for p in prior):
            continue  # identical identity already alerted — dedup
        if any(p["members"] > cur_set for p in prior):
            continue  # a superset cluster already alerted — don't emit a shrunk downgrade
        superseded = [p for p in prior if p["members"] and p["members"] < cur_set]
        is_upgrade = bool(superseded)

        counts = defaultdict(int)
        for d in counted.values():
            counts[d] += 1
        if direction == "mixed":
            mix = ", ".join(f"{counts[k]} {k}{'s' if counts[k] != 1 else ''}"
                            for k in ("buy", "sell", "mixed") if counts.get(k))
            headline = f"{n}-member {ticker} cluster ({mix} — mixed)"
        elif is_upgrade:
            headline = f"{ticker} cluster expanded to {n} members ({_verb(direction)})"
        else:
            headline = f"{n} members {_verb(direction)} {ticker} in {WINDOW_HOURS}h"

        names = [members[mid]["name"] for mid in member_ids]
        why = (f"{n} distinct members {_verb(direction)} {ticker} within {WINDOW_HOURS}h: "
               f"{'; '.join(names)}. Identity {fp}.")

        detail_members, exchange_notes = [], []
        for mid in member_ids:
            rec = members[mid]
            detail_members.append({
                "member_id": mid, "name": rec["name"], "direction": member_dirs[mid],
                "transaction_dates": sorted(d.isoformat() for d in rec["dates"]),
                "sizes": rec["sizes"], "doc_id": rec["doc_id"], "filing_url": rec["filing_url"],
            })
            if "exchange" in rec["types"]:
                exchange_notes.append(mid)
        detail = json.dumps({
            "fingerprint": fp, "direction": direction, "distinct_members": n,
            "members": detail_members, "unusual_types_members": exchange_notes,
            "window_hours": WINDOW_HOURS,
        })

        sector = classify_sector(ticker)
        tags = {
            "members": member_ids, "member_names": names, "direction": direction,
            "distinct_members": n, "fingerprint": fp, "is_upgrade": is_upgrade,
            "tags": ["congressional", "cluster", direction]
                    + ([sector] if sector and sector != "Other" else []),
        }

        print(f"  [{severity}] {headline}  members={member_ids}"
              + (f"  supersedes {[p['id'] for p in superseded]}" if is_upgrade else ""))
        if dry_run:
            continue

        alert_id = insert_alert(
            conn, rule=RULE, ticker=ticker, severity=severity, headline=headline,
            why_matters=why, tags=tags, detail=detail, source_url=HOUSE_DISCLOSURE_HUB,
            verify_url=(members[member_ids[0]]["filing_url"] or HOUSE_DISCLOSURE_HUB),
            # 1, not `n`. `n` is the number of MEMBERS in the cluster, and passing it
            # as distinct_rule_count told the evidence formula that a 5-member cluster
            # had 5 independent corroborators (ec = 80.0). RULE_CLUSTER is ONE
            # instrument — `congressional`. Member count is real signal and stays in
            # the headline, tags and severity; it is not corroboration BREADTH.
            distinct_rule_count=1, has_conflict=has_conflict, novelty_key=fp,
        )
        for p in superseded:
            conn.execute(
                "UPDATE alerts SET lifecycle_stage='superseded', "
                "conflict_explanation=COALESCE(conflict_explanation,'') || ? WHERE id=?",
                (f"[superseded by alert {alert_id}: cluster expanded to {n} members] ", p["id"]),
            )
        conn.commit()
        emitted += 1
        upgrades += 1 if is_upgrade else 0

    conn.close()
    notes = (f"tickers_scanned={tickers_scanned}, groups_qualifying={qualifying}, "
             f"alerts_emitted={emitted}, upgrades={upgrades}")
    record_activity(RULE, scanned=tickers_scanned, flagged=qualifying, emitted=emitted,
                    duration_seconds=round(_time.time() - _t0, 2), notes=notes)
    print(f"[{RULE}] {notes}")
    return {"tickers_scanned": tickers_scanned, "qualifying": qualifying,
            "emitted": emitted, "upgrades": upgrades}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Congressional trading clusters (RULE_CLUSTER).")
    p.add_argument("--dry-run", action="store_true", help="Print clusters without writing.")
    p.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.dry_run:
        print("Dry run — no DB writes.")
    print(f"Scanning for congressional clusters ({WINDOW_HOURS}h window)…")
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
