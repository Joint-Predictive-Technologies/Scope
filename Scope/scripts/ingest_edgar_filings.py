#!/usr/bin/env python3
"""Ingest EDGAR filing METADATA (form type, filing date, accession) per CIK.

    python scripts/ingest_edgar_filings.py --tickers AAPL,RTX            # DRY RUN
    python scripts/ingest_edgar_filings.py --tickers AAPL,RTX --apply
    python scripts/ingest_edgar_filings.py --from-alerts --limit 200 --apply

Fills `edgar_filings` / `edgar_cik_watch` (m018). Writes NOTHING else — not
`filings`, not `alerts`, not `ticker_meta`, not any table a rule or the gate
reads. See the m018 block in `jpt_common.py` for why those tables are separate.

── WHY THIS EXISTS ─────────────────────────────────────────────────────────
Filing velocity was refused at its go/no-go gate: the only form-type column in
the database is `filings.report_type`, holding exactly 'PTR' and '4'. No 8-K,
no S-1, no S-3, no amendment marker, anywhere. This work order does NOT build
velocity — it makes the data velocity would need exist in a form that can be
trusted.

── THE SOURCE, AND WHY THIS ONE ────────────────────────────────────────────
`https://data.sec.gov/submissions/CIK##########.json` returns a filer's filing
index in ONE request: parallel arrays of form / filingDate / accessionNumber /
reportDate / primaryDocument covering the most recent ~1,000 filings, plus
`filings.files[]` naming older paginated shards. Every form type is present —
measured on AAPL: 4, 144, 10-Q, 8-K, S-3, S-8 and so on.

The full-text search endpoint (`efts.sec.gov`) was NOT chosen: it is paginated
at 10 hits, capped at 10,000 results per query, and is a search index rather
than a filer's record of account. One request per CIK against the authoritative
submissions index is both cheaper and more complete.

── RATE LIMITS, AS SEC PUBLISHES THEM ──────────────────────────────────────
SEC's fair-access policy is 10 requests/second with a declared User-Agent
carrying contact information; exceeding it earns a 403 and then an IP block.
`--rate` defaults to 8 req/s (a 0.125s floor between requests) rather than 10,
because the ceiling is the point at which throttling STARTS, not a target. One
CIK is one request of ~150KB, so 1,600 tickers is ~3.5 minutes and ~250MB —
run it deliberately, not on a schedule, until it has proven itself.

⚠️ `--include-history` fetches the older shards too, which multiplies requests
per CIK by the shard count. Off by default: the recent 1,000 filings already
cover ~15 years for a typical filer, and a velocity metric will never reach
back further than its own monitoring window anyway.

── WHAT "BACKFILL" MEANS HERE, AND WHY IT IS DECIDED BY OBSERVATION ────────
A row is backfill if EITHER:

  (a) it was written by that CIK's FIRST run — we did not watch the filing
      arrive, we discovered it retroactively. This deliberately includes
      documents filed the same day we started watching; or
  (b) its `filing_date` predates that CIK's `monitoring_since` — a later poll
      surfacing an older document is still a discovery, not new activity
      inside our observation window.

Only a filing FIRST SEEN by a later poll AND dated on/after `monitoring_since`
is organic (`is_backfill = 0`).

⚠️ THE ASYMMETRY IS DELIBERATE. Mislabelling a discovery as organic silently
deforms every baseline computed over it — that is precisely how the previous
attempt died, with 71% of `earnings_sentiment` arriving in one run and reading
as a four-month burst. Mislabelling organic as backfill only costs history,
and history is recoverable by waiting. So the rule fails toward "backfill".

⚠️ A ROW'S VERDICT IS FIXED AT FIRST SIGHT AND NEVER REWRITTEN. Re-polling uses
`INSERT ... ON CONFLICT (cik, accession_number) DO NOTHING`, so a later run cannot
flip an existing row's `is_backfill`. That is what makes the flag mean "how we came
to have this" rather than "what the last run happened to think".

🔴 THE KEY IS (cik, accession) BECAUSE ONE DOCUMENT CAN BELONG TO SEVERAL FILERS.
An accession is unique per document, not per filer, and EDGAR lists a document under
every CIK it concerns — Berkshire's 13G on Apple is accession `0001193125-24-036431`
in BOTH filers' indexes. Keyed on the accession alone, the CIK ingested second lost
the filing entirely — a silent per-filer under-count that no single row would
reveal. Measured 96 of 204,053 on a prod-ticker pass; an independent verifier's
equivalent run over a LOCAL ticker list measured 13 of 122,312. The counts differ
because the ticker lists do; the phenomenon and the fix are what the tests pin.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from jpt_common import db_connection  # noqa: E402

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SHARD_URL = "https://data.sec.gov/submissions/{name}"
# SEC requires a declared User-Agent with contact info; a bare client gets 403.
UA = "Scope Congressional Trade Tracker (sloppysecondstbb@gmail.com)"
HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}

DEFAULT_RATE = 8.0          # requests/second; SEC's published ceiling is 10
MAX_RATE = 10.0


class SourceUnavailable(RuntimeError):
    """EDGAR did not answer. Loud, never a silent zero."""


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class _Throttle:
    """A floor on the interval between requests, enforced across every call site.

    A per-call `sleep` is not the same thing: it does not account for the time the
    previous request actually took, so a slow response makes the client MORE polite
    and a fast one makes it less. This measures from the last request's start.
    """

    def __init__(self, rate: float) -> None:
        self.interval = 1.0 / max(0.1, min(rate, MAX_RATE))
        self._last = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.interval:
            time.sleep(self.interval - gap)
        self._last = time.monotonic()


def _get_json(url: str, throttle: _Throttle, timeout: int = 30) -> dict:
    throttle.wait()
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise SourceUnavailable(f"{url} -> HTTP {e.code}") from e
    except Exception as e:                      # noqa: BLE001 - re-raised as one type
        raise SourceUnavailable(f"{url} -> {type(e).__name__}: {e}") from e


def cik_map(throttle: _Throttle) -> dict[str, str]:
    """SYMBOL -> zero-padded 10-digit CIK, from SEC's own ticker file."""
    data = _get_json(TICKER_MAP_URL, throttle)
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}


def fetch_filings(cik: str, throttle: _Throttle,
                  include_history: bool = False) -> list[dict]:
    """Every filing EDGAR lists for this CIK, as flat dicts.

    The submissions document stores parallel arrays, not records: `form[i]`,
    `filingDate[i]` and `accessionNumber[i]` describe one filing. They are zipped
    here rather than indexed by position downstream, so a short array truncates
    the batch instead of silently pairing a form with another filing's date.
    """
    doc = _get_json(SUBMISSIONS_URL.format(cik=cik), throttle)
    blocks = [doc.get("filings", {}).get("recent", {})]
    if include_history:
        for shard in doc.get("filings", {}).get("files", []) or []:
            blocks.append(_get_json(SHARD_URL.format(name=shard["name"]), throttle))

    out: list[dict] = []
    for b in blocks:
        forms = b.get("form") or []
        dates = b.get("filingDate") or []
        accs = b.get("accessionNumber") or []
        reports = b.get("reportDate") or []
        docs = b.get("primaryDocument") or []
        for i in range(min(len(forms), len(dates), len(accs))):
            form = (forms[i] or "").strip()
            acc = (accs[i] or "").strip()
            date = (dates[i] or "").strip()
            if not (form and acc and date):
                continue                        # incomplete record, never invented
            out.append({
                "accession_number": acc,
                "cik": cik,
                "form_type": form,
                "filing_date": date,
                "report_date": (reports[i] or "").strip() or None if i < len(reports) else None,
                "primary_document": (docs[i] or "").strip() or None if i < len(docs) else None,
                # EDGAR marks amendments with a '/A' suffix on the form type ('8-K/A').
                # Stored as its own column so a consumer does not have to re-parse the
                # string and get the edge cases wrong.
                "is_amendment": 1 if form.upper().endswith("/A") else 0,
            })
    return out


def classify_backfill(filing_date: str, monitoring_since: str, first_run: bool) -> int:
    """The rule from the module docstring, in one place so it can be tested directly.

    `monitoring_since` is a UTC timestamp; `filing_date` is a date. Comparison is on
    the DATE part, and the boundary is `<` — a filing dated exactly on the day
    monitoring began is not backfill by clause (b). It is still caught by clause (a)
    on the first run, which is the case that actually matters.
    """
    if first_run:
        return 1
    return 1 if filing_date < monitoring_since[:10] else 0


def ingest_cik(conn, cik: str, ticker: str | None, run_id: str,
               throttle: _Throttle, include_history: bool = False,
               apply: bool = False) -> dict:
    """Ingest one CIK. Returns counters; writes only when `apply`."""
    now = _utc_now()
    row = conn.execute(
        "SELECT monitoring_since, poll_count FROM edgar_cik_watch WHERE cik=?", (cik,)
    ).fetchone()
    first_run = row is None
    monitoring_since = now if first_run else row[0]

    filings = fetch_filings(cik, throttle, include_history)

    known = {r[0] for r in conn.execute(
        "SELECT accession_number FROM edgar_filings WHERE cik=?", (cik,))}

    fresh = [f for f in filings if f["accession_number"] not in known]
    for f in fresh:
        f["is_backfill"] = classify_backfill(f["filing_date"], monitoring_since, first_run)

    counts = {
        "cik": cik, "ticker": ticker, "first_run": first_run,
        "seen": len(filings), "already_known": len(filings) - len(fresh),
        "new": len(fresh),
        "new_backfill": sum(f["is_backfill"] for f in fresh),
        "new_organic": sum(1 for f in fresh if not f["is_backfill"]),
        "forms": len({f["form_type"] for f in filings}),
    }

    if not apply:
        return counts

    conn.executemany(
        """INSERT INTO edgar_filings
               (accession_number, cik, form_type, filing_date, report_date,
                primary_document, is_amendment, is_backfill, first_seen_run_id,
                ingested_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(cik, accession_number) DO NOTHING""",
        [(f["accession_number"], f["cik"], f["form_type"], f["filing_date"],
          f["report_date"], f["primary_document"], f["is_amendment"],
          f["is_backfill"], run_id, now) for f in fresh],
    )
    if first_run:
        conn.execute(
            """INSERT INTO edgar_cik_watch
                   (cik, ticker, monitoring_since, first_run_id, last_polled_at,
                    last_run_id, poll_count)
               VALUES (?,?,?,?,?,?,1)""",
            (cik, ticker, monitoring_since, run_id, now, run_id))
    else:
        conn.execute(
            """UPDATE edgar_cik_watch
                  SET last_polled_at=?, last_run_id=?, poll_count=poll_count+1,
                      ticker=COALESCE(ticker, ?)
                WHERE cik=?""",
            (now, run_id, ticker, cik))
    conn.commit()
    return counts


def _tickers_from_alerts(conn, limit: int | None) -> list[str]:
    """Tickers Scope has actually alerted on — the bound on the initial load.

    Scoping to the alert corpus rather than the whole EDGAR universe keeps the first
    pass to ~1,600 requests instead of ~800,000, and every one of them is a name the
    product already has a reason to care about.
    """
    sql = ("SELECT ticker, COUNT(*) c FROM alerts "
           "WHERE ticker IS NOT NULL AND TRIM(ticker) <> '' "
           "GROUP BY ticker ORDER BY c DESC")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r[0].strip().upper() for r in conn.execute(sql)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--tickers", help="comma-separated symbols")
    src.add_argument("--from-alerts", action="store_true",
                     help="every ticker Scope has alerted on, busiest first")
    ap.add_argument("--limit", type=int, help="cap the ticker count")
    ap.add_argument("--db", help="database path (default: resolved as usual)")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE,
                    help=f"requests/second, capped at {MAX_RATE:g} (default {DEFAULT_RATE:g})")
    ap.add_argument("--include-history", action="store_true",
                    help="also fetch older paginated shards (many more requests)")
    ap.add_argument("--apply", action="store_true",
                    help="write; omit for a dry run that fetches but does not insert")
    args = ap.parse_args()

    conn = db_connection(args.db)
    throttle = _Throttle(args.rate)
    run_id = "edgar-" + datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")

    if args.tickers:
        symbols = [s.strip().upper() for s in args.tickers.split(",") if s.strip()]
    else:
        symbols = _tickers_from_alerts(conn, args.limit)
    if args.limit:
        symbols = symbols[: args.limit]

    print(f"run_id       {run_id}")
    print(f"mode         {'APPLY (writes)' if args.apply else 'DRY RUN (no writes)'}")
    print(f"rate         {min(args.rate, MAX_RATE):g} req/s")
    print(f"tickers      {len(symbols)}")

    try:
        smap = cik_map(throttle)
    except SourceUnavailable as e:
        print(f"FATAL: SEC ticker map unavailable: {e}")
        return 2

    unresolved, totals = [], {"seen": 0, "new": 0, "new_backfill": 0, "new_organic": 0}
    failed = []
    for n, sym in enumerate(symbols, 1):
        cik = smap.get(sym)
        if not cik:
            unresolved.append(sym)
            continue
        try:
            c = ingest_cik(conn, cik, sym, run_id, throttle,
                           args.include_history, args.apply)
        except SourceUnavailable as e:
            failed.append((sym, str(e)))
            print(f"  [{n}/{len(symbols)}] {sym:<8} FAILED {e}")
            continue
        for k in totals:
            totals[k] += c[k]
        print(f"  [{n}/{len(symbols)}] {sym:<8} CIK{cik} "
              f"seen={c['seen']:<5} new={c['new']:<5} "
              f"backfill={c['new_backfill']:<5} organic={c['new_organic']:<5} "
              f"forms={c['forms']:<3} {'FIRST RUN' if c['first_run'] else ''}")

    print("\n── totals ─────────────────────────────────────────")
    for k, v in totals.items():
        print(f"  {k:<14} {v}")
    if unresolved:
        print(f"  unresolved symbols ({len(unresolved)}): {', '.join(unresolved[:20])}"
              + (" …" if len(unresolved) > 20 else ""))
    if failed:
        print(f"  FAILED ({len(failed)}): " + ", ".join(s for s, _ in failed[:20]))
    if not args.apply:
        print("\nDRY RUN — nothing was written. Re-run with --apply.")
    # A run that resolved nothing is a failure, not a quiet success.
    return 1 if (failed and not totals["seen"]) else 0


if __name__ == "__main__":
    sys.exit(main())
