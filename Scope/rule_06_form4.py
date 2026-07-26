#!/usr/bin/env python3
"""
rule_06_form4.py

Detects significant executive insider trades (SEC Form 4) that deviate
from the executive's historical pattern and emits RULE_06 alerts.
"""

from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta

import requests

from jpt_common import db_connection


RULE = "RULE_06"
EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions"
HEADERS = {
    "User-Agent": "Scope/0.1 sloppysecondstbb@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
# 0.2s ≈ 5 req/s, inside SEC's published 10 req/s ceiling. Every EDGAR call
# sleeps first and the run is serial, so this constant *is* the run's wall
# clock: duration >= SLEEP x (search pages + fetches). At the old 0.5s a run
# could not make more than 600 calls inside the scheduler's 300s timeout.
SLEEP = 0.2
HISTORY_COUNT = 5
MIN_VALUE = 50_000.0
MIN_MULTIPLE = 2.0

# Source tag for this rule's rows in the shared `filings` registry. Every other
# reader of that table filters on source='house' (ingest_house_index.py:443,
# parse_house_pdfs.py:120, scripts/backfill_member_ids.py:71) or reaches it
# through transactions.filing_id, so these rows are invisible to them.
FILING_SOURCE = "sec_form4"

DEFAULT_WINDOW_DAYS = 7    # cold start / dry run: the old fixed window
LOOKBACK_DAYS = 2          # re-offer recent days; EDGAR indexes Form 4s late
MAX_WINDOW_DAYS = 7        # hard cap, so a long outage can't queue a huge scan
TIME_BUDGET_SECONDS = 240  # stop and log a resumable partial inside the 300s


class EdgarUnavailable(RuntimeError):
    """EDGAR refused a request the run cannot proceed without.

    Raised instead of returning an empty result, so that "EDGAR is down" can
    never again be reported as "no insider filings today".
    """


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None) -> requests.Response:
    time.sleep(SLEEP)
    return requests.get(url, headers=HEADERS, params=params, timeout=20)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FilingRef:
    adsh: str
    owner_cik: str   # numeric string, no leading zeros
    filename: str    # XML filename inside the archive directory
    file_date: str


@dataclass
class Transaction:
    code: str        # P or S
    shares: float
    price: float

    @property
    def value(self) -> float:
        return self.shares * self.price


@dataclass
class ParsedFiling:
    owner_name: str
    owner_title: str
    ticker: str
    company: str
    transactions: list[Transaction] = field(default_factory=list)

    @property
    def ps_value(self) -> float:
        return sum(t.value for t in self.transactions if t.code in ("P", "S"))

    @property
    def majority_action(self) -> str:
        buys = sum(t.value for t in self.transactions if t.code == "P")
        sells = sum(t.value for t in self.transactions if t.code == "S")
        if sells >= buys:
            return "sold"
        return "bought"


# ---------------------------------------------------------------------------
# EDGAR search — paginate through all Form 4 filings in the date range
# ---------------------------------------------------------------------------

def search_form4_filings(
    since: str, today: str, deadline: float | None = None
) -> tuple[list[FilingRef], bool]:
    """Return (refs, complete).

    `complete` is False when pagination stopped at `deadline` with pages still
    outstanding — the caller reports that as a partial run rather than treating
    a truncated list as the whole window. A non-200 raises `EdgarUnavailable`.
    """
    refs: list[FilingRef] = []
    offset = 0
    page_size = 100

    while True:
        if deadline is not None and time.time() >= deadline:
            return refs, False

        r = _get(EDGAR_SEARCH, params={
            "q": "",
            "forms": "4",
            "dateRange": "custom",
            "startdt": since,
            "enddt": today,
            "from": offset,
            "size": page_size,
        })
        if r.status_code != 200:
            raise EdgarUnavailable(
                f"EDGAR search returned HTTP {r.status_code} at offset {offset} "
                f"({len(refs)} filings collected before the failure)"
            )

        data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break

        for hit in hits:
            src = hit.get("_source", {})
            adsh = src.get("adsh", "")
            ciks = src.get("ciks", [])
            if not adsh or not ciks:
                continue

            raw_id = hit.get("_id", "")
            filename = raw_id.split(":", 1)[-1] if ":" in raw_id else ""
            if not filename.lower().endswith(".xml"):
                filename = ""

            # ciks[0] is the reporting owner (person); the archive is
            # accessible under their CIK directory.
            owner_cik = str(int(ciks[0])) if ciks[0].lstrip("0") else "0"

            refs.append(FilingRef(
                adsh=adsh,
                owner_cik=owner_cik,
                filename=filename,
                file_date=src.get("file_date", ""),
            ))

        total = data.get("hits", {}).get("total", {}).get("value", 0)
        offset += len(hits)
        if offset >= total:
            break

    return refs, True


# ---------------------------------------------------------------------------
# Fetch and parse a single Form 4 XML document
# ---------------------------------------------------------------------------

def _xml_text(root: ET.Element, path: str) -> str:
    el = root.find(path)
    return (el.text or "").strip() if el is not None else ""


def fetch_filing_xml(ref: FilingRef) -> str | None:
    """Return raw XML text for the filing, or None on failure."""
    accession_clean = ref.adsh.replace("-", "")

    if ref.filename:
        url = f"{EDGAR_ARCHIVE}/{ref.owner_cik}/{accession_clean}/{ref.filename}"
        r = _get(url)
        if r.status_code == 200:
            return r.text

    # Filename unknown or request failed — discover via index.json.
    index_url = f"{EDGAR_ARCHIVE}/{ref.owner_cik}/{accession_clean}/index.json"
    r = _get(index_url)
    if r.status_code != 200:
        return None

    items = r.json().get("directory", {}).get("item", [])
    xml_files = [i["name"] for i in items if i["name"].lower().endswith(".xml")]
    if not xml_files:
        return None

    xml_url = f"{EDGAR_ARCHIVE}/{ref.owner_cik}/{accession_clean}/{xml_files[0]}"
    r2 = _get(xml_url)
    return r2.text if r2.status_code == 200 else None


def parse_form4(xml_text: str) -> ParsedFiling | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    ticker = _xml_text(root, "issuer/issuerTradingSymbol")
    if not ticker:
        return None

    company = _xml_text(root, "issuer/issuerName")
    owner_name = _xml_text(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    owner_title = _xml_text(root, "reportingOwner/reportingOwnerRelationship/officerTitle")

    transactions: list[Transaction] = []
    for txn in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        code_el = txn.find("transactionCoding/transactionCode")
        if code_el is None:
            continue
        code = (code_el.text or "").strip().upper()
        if code not in ("P", "S"):
            continue

        shares_el = txn.find("transactionAmounts/transactionShares/value")
        price_el = txn.find("transactionAmounts/transactionPricePerShare/value")
        try:
            shares = float(shares_el.text or 0) if shares_el is not None else 0.0
            price = float(price_el.text or 0) if price_el is not None else 0.0
        except (ValueError, TypeError):
            continue

        if shares > 0 and price > 0:
            transactions.append(Transaction(code=code, shares=shares, price=price))

    return ParsedFiling(
        owner_name=owner_name,
        owner_title=owner_title,
        ticker=ticker,
        company=company,
        transactions=transactions,
    )


# ---------------------------------------------------------------------------
# Historical average — last N Form 4 filings for the same owner
# ---------------------------------------------------------------------------

def historical_avg(
    owner_cik: str,
    skip_adsh: str,
    value_cache: dict[str, float | None] | None = None,
) -> float | None:
    """
    Return the mean total P/S value across the last HISTORY_COUNT Form 4
    filings for this owner (excluding the current filing). None if no history.

    `value_cache` memoises adsh -> parsed P/S value for the life of one run. An
    owner who files twice inside the window shares most of their history, so
    this drops up to HISTORY_COUNT redundant fetches per repeat filer.
    """
    cik_padded = owner_cik.zfill(10)
    r = _get(f"{EDGAR_SUBMISSIONS}/CIK{cik_padded}.json")
    if r.status_code != 200:
        return None

    recent = r.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])

    skip_clean = skip_adsh.replace("-", "")
    history: list[str] = []
    for form, adsh in zip(forms, accessions):
        if form != "4":
            continue
        if adsh.replace("-", "") == skip_clean:
            continue
        history.append(adsh)
        if len(history) >= HISTORY_COUNT:
            break

    if not history:
        return None

    totals: list[float] = []
    for adsh in history:
        if value_cache is not None and adsh in value_cache:
            cached = value_cache[adsh]
            if cached:
                totals.append(cached)
            continue

        ref = FilingRef(adsh=adsh, owner_cik=owner_cik, filename="", file_date="")
        xml_text = fetch_filing_xml(ref)
        parsed = parse_form4(xml_text) if xml_text else None
        value = parsed.ps_value if parsed and parsed.ps_value > 0 else None
        if value_cache is not None:
            value_cache[adsh] = value
        if value:
            totals.append(value)

    return sum(totals) / len(totals) if totals else None


# ---------------------------------------------------------------------------
# Scan state — high-water mark and per-filing dedup
#
# Both live in the existing `filings` table under source='sec_form4', so this
# needs no schema change: `filings` is already a generic, source-tagged filing
# registry with a UNIQUE doc_id (accession numbers can't collide with House
# doc_ids). A processed filing is never re-fetched, which is what lets the scan
# window overlap safely.
# ---------------------------------------------------------------------------

def archive_dir_url(ref: FilingRef) -> str:
    return f"{EDGAR_ARCHIVE}/{ref.owner_cik}/{ref.adsh.replace('-', '')}"


def scan_watermark(conn) -> str | None:
    """Newest file_date this rule has already processed, or None on cold start."""
    row = conn.execute(
        "SELECT MAX(filing_date) FROM filings WHERE source = ?",
        (FILING_SOURCE,),
    ).fetchone()
    return row[0] if row and row[0] else None


def processed_adsh(conn, since: str) -> set[str]:
    """Accession numbers already processed on or after `since`."""
    return {
        row[0]
        for row in conn.execute(
            "SELECT doc_id FROM filings WHERE source = ? AND filing_date >= ?",
            (FILING_SOURCE, since),
        )
    }


def record_filing(conn, ref: FilingRef, status: str) -> None:
    """Mark a filing processed so later runs skip it.

    Only called once a filing has actually been fetched and parsed (or found
    deterministically unparseable). A fetch failure deliberately records
    nothing, so a transient 429 or 503 is retried on the next run instead of
    silently dropping the filing forever.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO filings
            (source, doc_id, member_id, filing_date, report_type,
             extraction_status, raw_url)
        VALUES (?, ?, NULL, ?, '4', ?, ?)
        """,
        (FILING_SOURCE, ref.adsh, ref.file_date, status, archive_dir_url(ref)),
    )


def _finish_filing(conn, ref: FilingRef, status: str) -> None:
    """Record one filing as processed and commit it.

    Committing per filing is what makes the run resumable: being killed costs
    at most the filing in flight, never the ones already done. (Alerts were
    already durable — `insert_alert` commits each row itself.)
    """
    if conn is None:
        return
    record_filing(conn, ref, status)
    conn.commit()


def resolve_since(conn, today: str) -> tuple[str, str]:
    """Return (since, how) for an incremental scan. `how` is for the log line."""
    floor = (date.fromisoformat(today) - timedelta(days=MAX_WINDOW_DAYS)).isoformat()

    mark = scan_watermark(conn)
    if not mark:
        return floor, f"cold start — full {MAX_WINDOW_DAYS}d window"

    incremental = (date.fromisoformat(mark) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    if incremental <= floor:
        return floor, f"watermark {mark} is older than the {MAX_WINDOW_DAYS}d cap"
    return incremental, f"incremental from watermark {mark} (-{LOOKBACK_DAYS}d lookback)"


# ---------------------------------------------------------------------------
# Alert dedup
# ---------------------------------------------------------------------------

def alert_exists(conn, ticker: str, headline: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE rule = ?
          AND ticker = ?
          AND headline = ?
          AND datetime(created_at) >= datetime('now', '-7 days')
        LIMIT 1
        """,
        (RULE, ticker, headline),
    ).fetchone()
    return row is not None


def insert_alert(conn, ticker: str, headline: str, severity: str, tags: str) -> None:
    conn.execute(
        "INSERT INTO alerts (rule, headline, severity, tags, ticker) VALUES (?,?,?,?,?)",
        (RULE, headline, severity, tags, ticker),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _fmt_dollars(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def _record_activity_checked(
    scanned: int, flagged: int, emitted: int, duration: float, notes: str
) -> bool:
    """Write the activity_log row and confirm it landed.

    `record_activity` swallows every exception (jpt_common.py:1049-1055), so a
    DB problem there leaves the run completely invisible — the failure mode this
    rule is being fixed for. Read the row back; if it isn't there, say so on
    stderr and let the caller exit non-zero so the scheduler's
    SCHEDULER_JOB_FAILURE net catches it.
    """
    from jpt_common import record_activity

    def _last_id() -> int | None:
        try:
            conn = db_connection()
            row = conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM activity_log WHERE source = ?",
                (RULE,),
            ).fetchone()
            conn.close()
            return int(row[0])
        except Exception:
            return None

    before = _last_id()
    record_activity(RULE, scanned=scanned, flagged=flagged, emitted=emitted,
                    duration_seconds=duration, notes=notes)
    after = _last_id()

    landed = before is not None and after is not None and after > before
    if not landed:
        print(
            f"CRITICAL: {RULE} completed but could not write its activity_log "
            f"row — this run would otherwise be invisible. notes={notes!r}",
            file=sys.stderr,
        )
    return landed


def run(
    since: str | None,
    today: str,
    emit_alerts: bool,
    time_budget: float = TIME_BUDGET_SECONDS,
) -> tuple[int, int, str]:
    """Return (significant_count, alerts_emitted, status).

    status is 'ok', 'partial' (stopped at the time budget — the rest resumes on
    the next run) or 'failed'. An activity_log row is written on every path,
    including exceptions, and progress is committed per filing so being killed
    mid-run costs at most the filing in flight.
    """
    _t0 = time.time()
    deadline = _t0 + time_budget

    scanned = significant = emitted = fetch_failed = 0
    in_window = already_done = 0
    status = "ok"
    reasons: list[str] = []   # appended, never overwritten — a run can be
                              # truncated for more than one reason at once
    since_how = "explicit --since"

    conn = db_connection() if emit_alerts else None

    try:
        if since is None:
            if conn is not None:
                since, since_how = resolve_since(conn, today)
            else:
                since = (date.fromisoformat(today)
                         - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat()
                since_how = f"dry run — fixed {DEFAULT_WINDOW_DAYS}d window"
        print(f"Scanning Form 4 filings from {since} to {today} ({since_how}) ...")

        refs, complete = search_form4_filings(since, today, deadline=deadline)
        if not complete:
            status = "partial"
            reasons.append("time budget reached while paginating the EDGAR search")

        in_window = len(refs)
        seen = processed_adsh(conn, since) if conn is not None else set()
        pending = [ref for ref in refs if ref.adsh not in seen]
        already_done = in_window - len(pending)

        # Oldest first, so the watermark advances monotonically and a partial
        # run leaves no gap behind it.
        pending.sort(key=lambda ref: (ref.file_date, ref.adsh))
        print(
            f"{in_window} filings in window, {already_done} already processed, "
            f"{len(pending)} to process"
        )

        value_cache: dict[str, float | None] = {}

        for ref in pending:
            if time.time() >= deadline:
                status = "partial"
                stopped = (
                    f"stopped at the {time_budget:.0f}s budget with "
                    f"{len(pending) - scanned} filings left; they resume next run"
                )
                reasons.append(stopped)
                print(f"  … {stopped}")
                break

            scanned += 1

            xml_text = fetch_filing_xml(ref)
            if not xml_text:
                # Not recorded — retried next run rather than silently dropped.
                fetch_failed += 1
                continue

            parsed = parse_form4(xml_text)
            if parsed is None:
                _finish_filing(conn, ref, "parse_failed")
                continue

            _finish_filing(conn, ref, "parsed_ok")

            if not parsed.transactions:
                continue

            current_value = parsed.ps_value
            if current_value < MIN_VALUE:
                continue

            avg = historical_avg(ref.owner_cik, ref.adsh, value_cache=value_cache)
            if avg is None or avg <= 0:
                continue

            multiple = current_value / avg
            if multiple < MIN_MULTIPLE:
                continue

            significant += 1

            action = parsed.majority_action
            title_str = f"{parsed.owner_title} of" if parsed.owner_title else "Insider at"
            headline = (
                f"{title_str} {parsed.ticker} {action} "
                f"{_fmt_dollars(current_value)} "
                f"({multiple:.1f}× their historical avg)"
            )

            severity = (
                "CRITICAL"
                if current_value > 1_000_000 and multiple >= 3.0
                else "HIGH"
            )

            action_tag = "sale" if action == "sold" else "purchase"
            tags = f"{parsed.owner_name},{action_tag},{multiple:.1f}x"

            print(
                f"  [{severity}] {parsed.ticker} | {parsed.owner_name} "
                f"({parsed.owner_title}) | {_fmt_dollars(current_value)} | "
                f"{multiple:.1f}× avg | {headline}"
            )

            if emit_alerts and conn is not None:
                if not alert_exists(conn, parsed.ticker, headline):
                    insert_alert(conn, parsed.ticker, headline, severity, tags)
                    emitted += 1

        # Every filing was offered but none could be fetched — that is EDGAR
        # being unreachable, not a quiet day.
        if status == "ok" and scanned > 0 and fetch_failed == scanned:
            status = "failed"
            reasons.append(f"all {scanned} filing fetches failed")

    except EdgarUnavailable as exc:
        status = "failed"
        reasons.append(str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)

    # BaseException, not Exception: a KeyboardInterrupt / SystemExit mid-run must
    # be recorded as failed rather than logged as a clean partial scan. Always
    # re-raised, so the traceback and the non-zero exit still reach the scheduler.
    except BaseException as exc:
        status = "failed"
        reasons.append(f"{type(exc).__name__}: {exc}")
        raise

    finally:
        if conn is not None:
            try:
                conn.commit()
            finally:
                conn.close()

        notes = "; ".join([
            f"status={status}",
            f"window={since}..{today} ({since_how})",
            f"in_window={in_window}",
            f"already_processed={already_done}",
            f"examined={scanned}",
            f"fetch_failed={fetch_failed}",
            *reasons,
        ])

        _logged = _record_activity_checked(
            scanned, significant, emitted, round(time.time() - _t0, 2), notes
        )
        if not _logged:
            status = "unlogged"

    return significant, emitted, status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect significant SEC Form 4 insider trades (RULE_06)."
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Start date YYYY-MM-DD. Default: incremental from the last "
             "processed filing (falls back to a 7-day window on a cold start).",
    )
    parser.add_argument(
        "--emit-alerts",
        action="store_true",
        help="Insert significant findings into the alerts table.",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=TIME_BUDGET_SECONDS,
        help=f"Stop and log a resumable partial run after this many seconds "
             f"(default {TIME_BUDGET_SECONDS:.0f}, inside the scheduler's 300s).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    today = date.today().isoformat()
    if args.since and args.since > today:
        print("ERROR: --since date is in the future.")
        raise SystemExit(1)

    significant, emitted, status = run(
        args.since, today, args.emit_alerts, time_budget=args.max_seconds
    )
    print(
        f"{significant} significant filings found, {emitted} alerts emitted "
        f"(status={status})"
    )

    # Exit non-zero so the scheduler's SCHEDULER_JOB_FAILURE net records it too.
    if status == "failed":
        raise SystemExit(1)
    if status == "unlogged":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
