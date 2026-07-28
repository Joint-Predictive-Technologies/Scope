#!/usr/bin/env python3
"""
verify_prod_state.py — READ-ONLY production health check.

Closes the two `UNVERIFIED — needs prod` items left by the 2026-07-27 merges:

  CHECK 1  RULE_15 (earnings) — did any FABRICATED alert emit before the repair landed?
           Pre-repair, `q=<ticker>` was a bare EDGAR full-text search and every hit was
           stored under the queried ticker without checking the CIK, so Boeing's political
           score was computed from *BA Credit Card Trust* filings. The merged code
           quarantines those rows, but alerts emitted BEFORE the merge would still be sitting
           in prod.

  CHECK 2  `tickers` — is the 13F CINS fallback actually functional in prod?
           RULE_16's fallback resolves foreign-domiciled issuers (Aon, Accenture, Willis
           Towers Watson) by issuer name against `tickers`. Its only writer,
           resolve_tickers.py, is scheduled NOWHERE, so the table can silently go stale or
           be empty — in which case the fallback recovers nothing.

SAFETY — this script is SELECT-ONLY and enforces it three ways:
  1. it opens SQLite with `mode=ro` (URI), so the driver itself refuses writes;
  2. every statement is routed through _q(), which rejects anything that is not a SELECT;
  3. it never takes a statement from user input.
It reads the DB location from an ENVIRONMENT VARIABLE and never prints it, so a path that
happens to embed a credential cannot leak into logs.

Usage (on a host that can see the production volume):

    PROD_DATABASE_PATH=/app/data/jpt.db python scripts/verify_prod_state.py

Exit codes: 0 = both checks clean, 1 = something needs attention, 2 = could not connect.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys

WRITE = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|PRAGMA)\b",
                   re.IGNORECASE)

# The tickers the pre-repair RULE_15 would have fabricated, and their signature jumps.
FABRICATED_SIGNATURE = {"BA": 109, "PFE": 92, "ABBV": 68, "XOM": 637}
CINS_PROBE = ("AON", "ACN", "WTW", "CB")


def _q(conn, sql: str, params: tuple = ()) -> list:
    """Run one SELECT. Refuses anything else, belt-and-braces with the ro connection."""
    if not sql.lstrip().upper().startswith("SELECT") or WRITE.search(sql):
        raise RuntimeError(f"refusing a non-SELECT statement: {sql[:60]!r}")
    return conn.execute(sql, params).fetchall()


def connect():
    path = os.getenv("PROD_DATABASE_PATH") or os.getenv("DATABASE_PATH")
    if not path:
        print("ERROR: set PROD_DATABASE_PATH to the production SQLite file "
              "(Railway volume, typically /app/data/jpt.db).")
        print("       Nothing was read. Nothing was written.")
        sys.exit(2)
    if not os.path.exists(path):
        print("ERROR: PROD_DATABASE_PATH does not exist on this host.")   # never echo it
        sys.exit(2)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)   # driver-level read-only
    conn.row_factory = sqlite3.Row
    return conn


def check_rule15(conn) -> bool:
    print("=" * 72)
    print("CHECK 1 — RULE_15 (earnings): clean, or fabricated pre-merge rows?")
    print("=" * 72)
    n = _q(conn, "SELECT COUNT(*) c FROM alerts WHERE rule='RULE_15'")[0]["c"]
    print(f"  alerts WHERE rule='RULE_15' : {n}")

    ok = True
    if n == 0:
        print("\n  ✅ CLEAN — RULE_15 has never emitted. The rule baselined silently,")
        print("     which is exactly the intended behaviour. Nothing to do.")
    else:
        rows = _q(conn, "SELECT id, ticker, severity, substr(headline,1,90) h, "
                        "substr(created_at,1,19) t FROM alerts WHERE rule='RULE_15' "
                        "ORDER BY id")
        print(f"\n  ⚠️  {n} RULE_15 alert(s) exist. Listing for review:\n")
        suspect = []
        for r in rows:
            pct = re.search(r"up (\d+)% QoQ", r["h"] or "")
            flag = ""
            if r["ticker"] in FABRICATED_SIGNATURE and pct and \
                    abs(int(pct.group(1)) - FABRICATED_SIGNATURE[r["ticker"]]) <= 3:
                flag = "  <-- MATCHES THE PRE-MERGE FABRICATED SIGNATURE"
                suspect.append(r["id"])
            print(f"     id={r['id']:<6} {r['t']}  [{r['severity']:<8}] "
                  f"{r['ticker']:<6} {r['h']}{flag}")
        if suspect:
            print(f"\n  ⚠️  ROWS TO REVIEW: ids {suspect}")
            print("     These match the pre-repair fabrication (e.g. Boeing scored from")
            print("     BA Credit Card Trust filings). They emitted BEFORE the fix landed.")
            print("     This is NOT a reason to revert — the fix is deployed and the")
            print("     quarantine prevents any repeat. Review and resolve them quietly.")
            ok = False
        else:
            print("\n  ✅ None match the fabricated signature — these look like genuine")
            print("     post-repair earnings alerts.")

    print("\n  earnings_sentiment by ticker:")
    try:
        rows = _q(conn, "SELECT ticker, COUNT(*) n, SUM(political_score>0) scored, "
                        "MIN(filing_date) f, MAX(filing_date) l "
                        "FROM earnings_sentiment GROUP BY ticker ORDER BY ticker")
    except sqlite3.OperationalError:
        print("     (table absent — the rule has never ingested in prod)")
        return ok
    if not rows:
        print("     EMPTY — a true cold start. The backlog-flood risk does not exist here.")
    else:
        for r in rows:
            print(f"     {r['ticker']:<6} rows={r['n']:<3} scored={r['scored']:<3} "
                  f"{r['f']} .. {r['l']}")
        print(f"\n     {len(rows)} tickers stored. Any row ingested before the repair is")
        print("     quarantined by `ingested_at < REPAIR_EPOCH` and cannot be compared.")
    return ok


def check_tickers(conn) -> bool:
    print("\n" + "=" * 72)
    print("CHECK 2 — `tickers`: is the 13F CINS fallback functional in prod?")
    print("=" * 72)
    try:
        row = _q(conn, "SELECT COUNT(*) n, MIN(updated_at) lo, MAX(updated_at) hi "
                       "FROM tickers")[0]
    except sqlite3.OperationalError:
        print("  ❌ FALLBACK INERT — the `tickers` table does not exist in prod.")
        _recommend()
        return False

    print(f"  rows={row['n']}   updated_at {row['lo']} .. {row['hi']}")
    found = {r["symbol"]: r["company_name"] for r in
             _q(conn, "SELECT symbol, company_name FROM tickers WHERE symbol IN "
                      "(?,?,?,?)", CINS_PROBE)}
    print("\n  CINS probe (the names the fallback exists to recover):")
    for s in CINS_PROBE:
        note = "" if s != "CB" else "   (Chubb — expected absent/unmatched, known limitation)"
        print(f"     {s:<4} {'FOUND: ' + found[s] if s in found else 'not present'}{note}")

    core = [s for s in ("AON", "ACN", "WTW") if s in found]
    if row["n"] == 0:
        print("\n  ❌ FALLBACK INERT — `tickers` is empty.")
        _recommend()
        return False
    if len(core) < 3:
        print(f"\n  ⚠️  PARTIAL — only {len(core)}/3 of AON/ACN/WTW present, so the CINS")
        print("     fallback recovers less than it does locally.")
        _recommend()
        return False
    print("\n  ✅ HEALTHY — the table is populated and AON/ACN/WTW all resolve, so the")
    print("     13F CINS fallback works in prod. Chubb stays visibly unmapped by design.")
    return True


def _recommend() -> None:
    print("\n  FOLLOW-UP (human-gated): `tickers` has exactly one writer,")
    print("  resolve_tickers.py, and it is scheduled NOWHERE — so the table goes stale")
    print("  or never populates. Schedule ONLY the safe half:")
    print("     upsert_sec_tickers()   — populates `tickers` from SEC. Safe, additive.")
    print("     resolve_transactions() — UPDATEs `transactions` (congressional trade")
    print("                              data). MUST stay human-gated; do NOT schedule.")
    print("  main() calls both unconditionally and has no argparse, so splitting it into")
    print("  a tickers-only entry point is a prerequisite.")


def main() -> None:
    conn = connect()
    try:
        a = check_rule15(conn)
        b = check_tickers(conn)
    finally:
        conn.close()
    print("\n" + "=" * 72)
    print(f"VERDICT: RULE_15 {'clean' if a else 'NEEDS REVIEW'} | "
          f"tickers {'healthy' if b else 'NEEDS FOLLOW-UP'}")
    print("Read-only: no statement other than SELECT was executed.")
    print("=" * 72)
    sys.exit(0 if (a and b) else 1)


if __name__ == "__main__":
    main()
