#!/usr/bin/env python3
"""
One-off coherence repair for RULE_11's stored contracts and alerts.

THE DEFECT
----------
`rule_11_contracts.py` asked USASpending for `Action Date` — not a valid Contract
Award field — so it always got `null` and dated every row with the RUN date. Two
consequences had to be repaired in the data, not just the code:

  1. Identity was `(recipient_name, award_date)` = `(recipient, run-day)`, so a
     recipient's 2nd..Nth award in a run was silently dropped.
  2. A backfill wrote `award_id` onto a row whose `amount` came from a DIFFERENT
     award. Measured before this repair: THE BOEING COMPANY held 5 rows all
     carrying $31,972,918,249 while their award_ids pointed at four separate
     awards worth $2.73B–$3.13B.

WHAT THIS SCRIPT DOES (forward-only, sourced — never invented)
--------------------------------------------------------------
  Phase 0  Migrate `contracts` off the (recipient, award_date) unique key onto
           `award_id` (via rule_11_contracts.ensure_contracts_schema).
  Phase 1  For every row holding an award_id, fetch that award from USASpending
           and rewrite amount / date / recipient / agency from the authoritative
           record. `verified_at` is stamped ONLY from a real response.
  Phase 2  For legacy rows with no award_id, attempt a SOURCED recovery: search
           the recipient's awards for one whose obligation equals the stored
           amount exactly. Adopt the id only on a UNIQUE exact match. Anything
           ambiguous is left unverified — an unknown award is recorded as
           unknown.
  Phase 3  Correct the derived RULE_11 alerts the same way: headline, severity
           and event_date re-derived from the authoritative award. Alerts that
           cannot be sourced have their false figure RETRACTED rather than
           restated. Detection-time scores (novelty / opportunity /
           evidence_confidence) are NEVER touched — they stay immutable.

Run against an isolated copy first. `--apply` is required to write.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection                                   # noqa: E402
from rule_11_contracts import (                                        # noqa: E402
    ensure_contracts_schema, _ensure_alert_key, _severity,
    fetch_award_detail, USER_AGENT, REQUEST_PAUSE, LARGE_THRESHOLD,
)

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ───────────────────────────── source lookups ─────────────────────────────────
def _cache_path(cache_dir: str, key: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)[:120]
    return os.path.join(cache_dir, f"{safe}.json")


def award_detail(award_id: str, cache_dir: str | None) -> dict | None:
    """Authoritative award record, cached on disk so a re-run costs no requests."""
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        p = _cache_path(cache_dir, award_id)
        if os.path.exists(p):
            with open(p) as fh:
                return json.load(fh) or None
    d = fetch_award_detail(award_id)
    time.sleep(REQUEST_PAUSE)
    if cache_dir:
        with open(_cache_path(cache_dir, award_id), "w") as fh:
            json.dump(d, fh)
    return d


def find_award_by_amount(recipient: str, amount: float) -> list[dict]:
    """Awards to `recipient` whose obligated total equals `amount` exactly.

    Used only to recover a legacy row's identity. Returns every candidate so the
    caller can refuse to guess when the match is not unique.
    """
    lo = max(0.0, amount - 1.0)
    payload = {
        "filters": {
            "time_period": [{"start_date": "2000-01-01",
                             "end_date": datetime.now(timezone.utc)
                                                 .strftime("%Y-%m-%d")}],
            "award_type_codes": ["A", "B", "C", "D"],
            "recipient_search_text": [recipient[:100]],
            "award_amounts": [{"lower_bound": lo, "upper_bound": amount + 1.0}],
        },
        "fields": ["Award ID", "Recipient Name", "Award Amount",
                   "Awarding Agency", "Base Obligation Date", "Start Date",
                   "Description", "generated_internal_id"],
        "sort": "Award Amount", "order": "desc", "limit": 20, "page": 1,
    }
    r = requests.post(SEARCH_URL, json=payload, timeout=45,
                      headers={"User-Agent": USER_AGENT})
    time.sleep(REQUEST_PAUSE)
    if r.status_code != 200:
        return []
    return [x for x in r.json().get("results", [])
            if abs(float(x.get("Award Amount") or 0) - amount) < 1.0]


def _detail_fields(d: dict) -> dict:
    """Pull the fields we trust out of an award-detail response."""
    rec = (d.get("recipient") or {}).get("recipient_name") or ""
    agency = ((d.get("awarding_agency") or {}).get("toptier_agency") or {}).get("name") or ""
    return {
        "amount": float(d.get("total_obligation") or 0),
        "date":   (d.get("date_signed") or "")[:10] or None,
        "recipient": rec.strip(),
        "agency": agency.strip(),
        "piid":  (d.get("piid") or "").strip(),
        "desc":  (d.get("description") or "").strip()[:500],
    }


# ───────────────────────────────── phases ─────────────────────────────────────
def phase1_verify_identified(conn, cache_dir, apply: bool) -> dict:
    """Rewrite every award-id-bearing row from its authoritative record."""
    rows = conn.execute(
        """SELECT id, recipient_name, amount, agency, award_date, award_id
           FROM contracts WHERE award_id IS NOT NULL AND award_id != ''
           ORDER BY id"""
    ).fetchall()
    st = {"checked": 0, "amount_wrong": 0, "date_wrong": 0, "corrected": 0,
          "gone_upstream": 0, "worst": []}
    for r in rows:
        st["checked"] += 1
        try:
            d = award_detail(r["award_id"], cache_dir)
        except Exception as e:                                   # noqa: BLE001
            print(f"  ! {r['award_id']}: {e}")
            continue
        if not d:
            st["gone_upstream"] += 1
            continue
        t = _detail_fields(d)
        amt_wrong  = abs(t["amount"] - float(r["amount"] or 0)) >= 1.0
        date_wrong = (t["date"] or "") != (r["award_date"] or "")
        if amt_wrong:
            st["amount_wrong"] += 1
            st["worst"].append({
                "id": r["id"], "recipient": r["recipient_name"],
                "stored_amount": float(r["amount"] or 0), "true_amount": t["amount"],
                "piid": t["piid"], "delta": float(r["amount"] or 0) - t["amount"]})
        if date_wrong:
            st["date_wrong"] += 1
        if amt_wrong or date_wrong:
            st["corrected"] += 1
            if apply:
                conn.execute(
                    """UPDATE contracts SET amount=?, award_date=?, recipient_name=?,
                                            agency=?, description=COALESCE(NULLIF(?,''), description),
                                            verified_at=?
                       WHERE id=?""",
                    (t["amount"], t["date"], t["recipient"] or r["recipient_name"],
                     t["agency"] or r["agency"], t["desc"], NOW, r["id"]))
        elif apply:
            conn.execute("UPDATE contracts SET verified_at=? WHERE id=?", (NOW, r["id"]))
    if apply:
        conn.commit()
    st["worst"].sort(key=lambda x: -abs(x["delta"]))
    return st


def phase2_recover_unidentified(conn, apply: bool, attempt: bool) -> dict:
    """Try to give legacy rows a real identity — only on a unique exact match."""
    rows = conn.execute(
        """SELECT id, recipient_name, amount, agency, award_date FROM contracts
           WHERE (award_id IS NULL OR award_id = '') ORDER BY id"""
    ).fetchall()
    st = {"legacy": len(rows), "recovered": 0, "ambiguous": 0, "no_match": 0}
    if not attempt:
        return st
    taken = {r[0] for r in conn.execute(
        "SELECT award_id FROM contracts WHERE award_id IS NOT NULL").fetchall()}
    for r in rows:
        amount = float(r["amount"] or 0)
        if amount <= 0:
            st["no_match"] += 1
            continue
        try:
            cands = [c for c in find_award_by_amount(r["recipient_name"], amount)
                     if (c.get("generated_internal_id") or "") not in taken]
        except Exception:                                        # noqa: BLE001
            cands = []
        if len(cands) == 1:
            c = cands[0]
            aid = c["generated_internal_id"]
            st["recovered"] += 1
            taken.add(aid)
            if apply:
                conn.execute(
                    """UPDATE contracts SET award_id=?, award_date=?, agency=?,
                                            recipient_name=?, verified_at=?
                       WHERE id=?""",
                    (aid,
                     (c.get("Base Obligation Date") or c.get("Start Date") or "")[:10] or None,
                     (c.get("Awarding Agency") or r["agency"] or "").strip(),
                     (c.get("Recipient Name") or r["recipient_name"]).strip(),
                     NOW, r["id"]))
        elif len(cands) > 1:
            st["ambiguous"] += 1        # refuse to guess
        else:
            st["no_match"] += 1
    if apply:
        conn.commit()
    return st


def phase2b_clear_unverifiable(conn, apply: bool, clear: bool) -> dict:
    """Blank the figures on rows that cannot be tied to any award.

    Measured during this repair: the legacy amounts match NO award of any type
    upstream — an award's obligated total moves as modifications post, so a
    figure copied from a different award at ingest time is not recoverable by
    value. Those rows therefore keep asserting a number that is provably not
    theirs.

    `amount` and `award_date` are set to NULL — "unknown", recorded as unknown.
    The row, its recipient and its ticker survive, so the ingest record is not
    erased; only the unattributable claim is. Nothing is invented, and no
    verified row is touched.
    """
    rows = conn.execute(
        """SELECT id FROM contracts
           WHERE verified_at IS NULL AND (amount IS NOT NULL OR award_date IS NOT NULL)"""
    ).fetchall()
    st = {"unverifiable": len(rows), "cleared": 0}
    if clear and apply and rows:
        conn.execute(
            """UPDATE contracts SET amount = NULL, award_date = NULL
               WHERE verified_at IS NULL""")
        conn.commit()
        st["cleared"] = len(rows)
    return st


def phase3_correct_alerts(conn, cache_dir, apply: bool) -> dict:
    """Re-derive each RULE_11 alert from source; retract what cannot be sourced.

    Scores are untouched by design: `novelty_score`, `opportunity_score` and
    `evidence_confidence` are detection-time values and stay immutable. Only the
    factual claim (headline / severity / event_date) is corrected.
    """
    alerts = conn.execute(
        """SELECT id, headline, severity, tags, ticker, event_date, award_key
           FROM alerts WHERE rule='RULE_11' ORDER BY id"""
    ).fetchall()
    st = {"total": len(alerts), "corrected": 0, "retracted": 0, "already_ok": 0,
          "sev_before": {}, "sev_after": {}}
    for a in alerts:
        st["sev_before"][a["severity"]] = st["sev_before"].get(a["severity"], 0) + 1
        parts = (a["tags"] or "").split("|")
        award_id = parts[2].strip() if len(parts) > 2 else ""
        d = None
        if award_id:
            try:
                d = award_detail(award_id, cache_dir)
            except Exception:                                    # noqa: BLE001
                d = None
        if not d:
            # No source => the figure cannot be restated. Retract it rather than
            # leave a false number on a live surface.
            new_head = (f"Gov Contract — {parts[0] if parts else a['ticker']} "
                        f"(award unidentified; original figure retracted "
                        f"2026-07-28 — see repair_rule11_contracts.py)")
            new_sev = "LOW"
            st["retracted"] += 1
            if apply:
                conn.execute(
                    """UPDATE alerts SET headline=?, severity=?,
                                         lifecycle_stage='superseded'
                       WHERE id=?""", (new_head, new_sev, a["id"]))
            st["sev_after"][new_sev] = st["sev_after"].get(new_sev, 0) + 1
            continue
        t = _detail_fields(d)
        on_wl = False
        new_sev = _severity(t["amount"], on_wl)
        when = f" on {t['date']}" if t["date"] else ""
        new_head = (f"Gov Contract — {t['recipient']} awarded "
                    f"${t['amount']:,.0f} by {t['agency']}{when}")
        st["sev_after"][new_sev] = st["sev_after"].get(new_sev, 0) + 1
        if new_head == a["headline"] and new_sev == a["severity"] \
                and a["award_key"] == award_id:
            st["already_ok"] += 1
            continue
        st["corrected"] += 1
        if apply:
            conn.execute(
                """UPDATE alerts SET headline=?, severity=?, event_date=?,
                                     award_key=?
                   WHERE id=?""",
                (new_head, new_sev, t["date"], award_id, a["id"]))
    if apply:
        conn.commit()
    return st


def main() -> int:
    ap = argparse.ArgumentParser(description="RULE_11 contracts coherence repair")
    ap.add_argument("--apply", action="store_true", help="write changes (default: report only)")
    ap.add_argument("--cache-dir", default=None, help="cache award-detail responses here")
    ap.add_argument("--recover-unidentified", action="store_true",
                    help="attempt sourced identity recovery for legacy rows")
    ap.add_argument("--clear-unverifiable", action="store_true",
                    help="NULL the amount/date on rows that match no award upstream")
    ap.add_argument("--skip-alerts", action="store_true")
    args = ap.parse_args()

    conn = db_connection()
    mode = "APPLY" if args.apply else "REPORT-ONLY"
    print(f"=== RULE_11 contracts repair [{mode}] ===")

    migrated = ensure_contracts_schema(conn) if args.apply else False
    if args.apply:
        _ensure_alert_key(conn)
    print(f"Phase 0 — schema migrated: {migrated}")

    p1 = phase1_verify_identified(conn, args.cache_dir, args.apply)
    print(f"\nPhase 1 — identified rows: checked={p1['checked']} "
          f"amount_wrong={p1['amount_wrong']} date_wrong={p1['date_wrong']} "
          f"corrected={p1['corrected']} gone_upstream={p1['gone_upstream']}")
    for w in p1["worst"][:8]:
        print(f"   id={w['id']:<4} {w['recipient'][:30]:<30} {w['piid']:<16} "
              f"stored=${w['stored_amount']:>15,.0f} → true=${w['true_amount']:>15,.0f}")

    p2 = phase2_recover_unidentified(conn, args.apply, args.recover_unidentified)
    print(f"\nPhase 2 — legacy rows: {p2['legacy']} recovered={p2['recovered']} "
          f"ambiguous={p2['ambiguous']} no_match={p2['no_match']}")

    p2b = phase2b_clear_unverifiable(conn, args.apply, args.clear_unverifiable)
    print(f"\nPhase 2b — unverifiable rows: {p2b['unverifiable']} cleared={p2b['cleared']}"
          + ("" if args.clear_unverifiable else "  (pass --clear-unverifiable to blank them)"))

    p3 = {}
    if not args.skip_alerts:
        p3 = phase3_correct_alerts(conn, args.cache_dir, args.apply)
        print(f"\nPhase 3 — alerts: total={p3['total']} corrected={p3['corrected']} "
              f"retracted={p3['retracted']} already_ok={p3['already_ok']}")
        print(f"   severity before: {p3['sev_before']}")
        print(f"   severity after:  {p3['sev_after']}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
