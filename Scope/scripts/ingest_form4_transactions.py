#!/usr/bin/env python3
"""Per-transaction Form 4 store — INGESTION ONLY.

One row per (filing, insider, transaction). No clustering, no surface, no alerts. The
cluster rule is the NEXT session and is built on top of this; nothing here emits.

WHY THIS EXISTS. RULE_06 alerts only when a filing clears $50,000 AND 2x that insider's
own historical average (`rule_06_form4.py:37-38`), so its alert stream is a record of
ANOMALOUS transactions, not of insider activity. Three executives quietly buying
normal-sized positions — the exact pattern cluster discovery is for — produces zero
alerts. Measured over 33 days of real history: 237 alerts, of which 40 were purchases,
yielding TWO genuine "2+ insiders buying in 7d" candidates. Clustering needs every
transaction; this stores them.

WHAT RULE_06's PARSER DISCARDS, and why it could not simply be reused.
`parse_form4` (`rule_06_form4.py:220-262`) keeps ONLY codes P and S, only the
non-derivative table, and only rows with shares > 0 AND price > 0. A real Apple filing
(0001140361-26-025622, fetched and kept under tests/fixtures/) contains:

    nonDerivative  M  acquired=A  30104 shares  price EMPTY (footnoted)
    nonDerivative  F  disposed=D  16238 shares  price 296.42
    derivative     M  disposed=D  30104 shares  price EMPTY

RULE_06 stores NOTHING from that filing. It is an option exercise plus tax withholding —
not a signal, but it IS insider activity, and a store that silently drops it cannot
answer "who transacted in this name". Note also that an empty price is legitimate and
footnoted, so `price > 0` is a real filter, not a sanity check.

⚠️ RULE_06 IS NOT TOUCHED. Its alert emission, dedup and watermark are byte-unchanged.
This module has its own watermark under its own `filings` source, so the two cannot
interfere — and a failure here can never suppress a RULE_06 alert.

VERIFIED ELEMENT NAMES, read off real SEC XML rather than assumed:
    issuer/issuerCik | issuerName | issuerTradingSymbol
    reportingOwner/reportingOwnerId/rptOwnerCik | rptOwnerName
    reportingOwner/reportingOwnerRelationship/isDirector|isOfficer|isTenPercentOwner|officerTitle
    aff10b5One                                   <- DOCUMENT level, not per-transaction
    {nonDerivative,derivative}Transaction/
        securityTitle/value | transactionDate/value
        transactionCoding/transactionCode
        transactionAmounts/transactionShares/value
                          /transactionPricePerShare/value
                          /transactionAcquiredDisposedCode/value   <- PER TRANSACTION
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection, record_activity  # noqa: E402
from rule_06_form4 import (  # noqa: E402  — the FETCH is reused; the parse is not (see above)
    FilingRef, archive_dir_url, fetch_filing_xml, search_form4_filings,
)

RULE = "FORM4_TXN_INGEST"

# Its OWN watermark source. RULE_06 uses 'sec_form4'; sharing it would make either rule's
# progress silently skip filings for the other.
FILING_SOURCE = "sec_form4_txn"

DEFAULT_WINDOW_DAYS = 7
LOOKBACK_DAYS = 2            # EDGAR indexes Form 4s late; re-offer recent days
MAX_WINDOW_DAYS = 7
TIME_BUDGET_SECONDS = 240    # stop inside the scheduler's 300s and log a resumable partial


# ── insider identity ─────────────────────────────────────────────────────────
#
# The audit's false cluster was three Manulife subsidiaries reading as three separate
# insiders. Distinguishing a natural person from an entity is therefore not cosmetic —
# it is the difference between "four insiders bought" and "one asset manager bought".

_ENTITY_TOKENS = {
    "LTD", "LIMITED", "LLC", "LP", "LLP", "INC", "INCORPORATED", "CORP", "CORPORATION",
    "CO", "COMPANY", "PLC", "PTE", "PTY", "GMBH", "AG", "NV", "BV", "SA", "SAS", "SPA",
    "TRUST", "FUND", "FUNDS", "PARTNERS", "PARTNERSHIP", "CAPITAL", "MANAGEMENT",
    "ADVISORS", "ADVISERS", "HOLDINGS", "HOLDING", "GROUP", "BRANCH", "BANK",
    "INSURANCE", "ASSURANCE", "ASSOCIATES", "INTERNATIONAL", "INVESTMENTS",
    "INVESTMENT", "VENTURES", "EQUITY", "ASSET", "SECURITIES", "FINANCIAL", "GP",
    # Special-purpose vehicles. `SL SPV-2` is a live example from the real corpus: it
    # files on DELL alongside `Silver Lake Partners IV`, which IS caught — so without
    # this the same manager reads as "an institution and a person", which is the
    # Manulife failure recurring in the shape the suffix test misses.
    "SPV", "HOLDCO", "AGGREGATOR", "VEHICLE", "AFFILIATES", "MANAGERS", "ADVISORY",
}
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalize_insider(name: str) -> str:
    """Casing- and punctuation-stable identity.

    `DUGGAN ROBERT W` and `Duggan Robert W` are one person filing twice, and a cluster
    rule counting distinct strings would call them two. Deliberately NOT reordering
    surname/given-name: EDGAR is consistent within an owner CIK, and guessing name order
    across cultures is how you merge two real people into one.
    """
    if not name:
        return ""
    return _WS.sub(" ", _PUNCT.sub(" ", name.upper())).strip()


def classify_insider(name: str, is_director: bool, is_officer: bool,
                     is_ten_pct: bool) -> str:
    """'person' | 'institution'. Errs toward 'person' ONLY on positive evidence.

    OFFICER is decisive; DIRECTOR is not. An entity cannot hold an officer title, so
    `is_officer` settles it. `is_director` does NOT — entities routinely sit on boards by
    deputization, and an earlier version short-circuited on either flag, so
    `classify_insider("Silver Lake Partners IV", is_director=True, ...)` returned
    'person'. In a live 70-filing sample 24 of 70 owners carried the director flag, so
    that override was doing a great deal of work in exactly the wrong direction.

    Order now: officer -> person. Otherwise entity tokens -> institution. Otherwise
    person.

    ⚠️ KNOWN AND MEASURED RECALL LIMIT. Token matching cannot see a brand name with no
    legal suffix. Against a hand-labelled set of the 220 distinct insider names in the
    real corpus this misses 1 of 18 entities (5.6%) with 0 of 202 false positives; but
    against a constructed list of well-known managers (BlackRock, Vanguard, Renaissance
    Technologies, Citadel, Point72, Baupost...) it misses roughly 31 of 33. The real-corpus
    number is the lower one because EDGAR conformed names usually carry a legal suffix.
    The residue concentrates on SPV and holding-vehicle names — precisely the
    affiliate-stacking shape a cluster rule cares about.

    THE REAL FIX IS NOT MORE TOKENS. SEC `submissions/CIK*.json` exposes `entityType`,
    which answers this directly per owner CIK. That is a network lookup and belongs with
    the cluster session, which is where the cost is justified. Until then treat
    `institution` as a HARD signal and `person` as a DEFAULT, not as proof.
    """
    if is_officer:
        return "person"
    tokens = set(normalize_insider(name).split())
    if tokens & _ENTITY_TOKENS:
        return "institution"
    return "person"


@dataclass
class Txn:
    security_title: str
    txn_date: str
    code: str
    acquired_disposed: str
    shares: float
    price: float
    is_derivative: bool

    @property
    def value(self) -> float:
        return self.shares * self.price


@dataclass
class Owner:
    cik: str
    name: str
    is_director: bool
    is_officer: bool
    is_ten_pct: bool
    title: str


def _t(el, path: str) -> str:
    if el is None:
        return ""
    node = el.find(path)
    return (node.text or "").strip() if node is not None and node.text else ""


def _flag(el, path: str) -> bool:
    v = _t(el, path).strip().lower()
    return v in ("1", "true")


def _f(el, path: str) -> float:
    try:
        return float(_t(el, path) or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_form4_document(xml_text: str) -> bool:
    """Is this actually a Form 4, or did EDGAR hand back something else?

    The discriminator is the ROOT ELEMENT, not parseability. `<html>rate limited</html>`
    is perfectly well-formed XML, so "did it parse" would classify a rate-limit page as
    a genuine symbol-less filing and record it as permanently processed — losing the
    filing for good. A real Form 4 roots at `ownershipDocument`; nothing else does.
    """
    try:
        return ET.fromstring(xml_text).tag == "ownershipDocument"
    except ET.ParseError:
        return False


def parse_transactions(xml_text: str) -> dict | None:
    """Everything, not just P/S. Returns None only if the filing is unusable.

    A filing is unusable when it has no `issuerTradingSymbol` — the ticker is the filer's
    OWN declared symbol, and there is no fallback. Guessing from the issuer name is
    precisely the mis-attribution that scored Boeing from `BA Credit Card Trust`, so an
    absent symbol is DROPPED AND COUNTED, never inferred.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    ticker = _t(root, "issuer/issuerTradingSymbol")
    if not ticker:
        return None

    # Document-level, confirmed against real XML — it is NOT inside transactionCoding.
    # Older filings predate the element entirely, so absent means "not disclosed", which
    # is different from "not a plan". Stored as NULL, never as 0.
    aff = root.find("aff10b5One")
    is_10b5_1 = None
    if aff is not None and (aff.text or "").strip():
        is_10b5_1 = 1 if (aff.text or "").strip().lower() in ("1", "true") else 0

    owners = []
    for o in root.findall("reportingOwner"):
        rel = o.find("reportingOwnerRelationship")
        owners.append(Owner(
            cik=_t(o, "reportingOwnerId/rptOwnerCik"),
            name=_t(o, "reportingOwnerId/rptOwnerName"),
            is_director=_flag(rel, "isDirector"),
            is_officer=_flag(rel, "isOfficer"),
            is_ten_pct=_flag(rel, "isTenPercentOwner"),
            title=_t(rel, "officerTitle"),
        ))

    txns: list[Txn] = []
    dropped: list[str] = []
    for table, derivative in (("nonDerivativeTable/nonDerivativeTransaction", False),
                              ("derivativeTable/derivativeTransaction", True)):
        for x in root.findall(table):
            code = _t(x, "transactionCoding/transactionCode").upper()
            if not code:
                # COUNTED, not silent. A dropped transaction with no counter is
                # indistinguishable from a filing that simply had fewer rows.
                dropped.append(table)
                continue
            txns.append(Txn(
                security_title=_t(x, "securityTitle/value"),
                txn_date=_t(x, "transactionDate/value"),
                code=code,
                # PER TRANSACTION. RULE_06's `majority_action` collapses a whole filing
                # to one label, so a filing that is 60% sale / 40% purchase records as a
                # sale and its buys vanish — which for a cluster rule keyed on BUYING is
                # the difference between seeing an insider and not.
                acquired_disposed=_t(
                    x, "transactionAmounts/transactionAcquiredDisposedCode/value").upper(),
                # An empty price is legitimate and footnoted (option exercises, gifts).
                # Storing 0.0 keeps the transaction; filtering on price would drop it.
                shares=_f(x, "transactionAmounts/transactionShares/value"),
                price=_f(x, "transactionAmounts/transactionPricePerShare/value"),
                is_derivative=derivative,
            ))

    return {
        "ticker": ticker,
        "issuer_cik": _t(root, "issuer/issuerCik"),
        "issuer_name": _t(root, "issuer/issuerName"),
        "period": _t(root, "periodOfReport"),
        "is_10b5_1": is_10b5_1,
        "owners": owners,
        "transactions": txns,
        "dropped_no_code": len(dropped),
    }


def ensure_tables(conn) -> None:
    """Additive only. Never drops, never rewrites."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS form4_transactions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            accession         TEXT NOT NULL,
            owner_seq         INTEGER NOT NULL,
            txn_index         INTEGER NOT NULL,
            ticker            TEXT NOT NULL,
            issuer_cik        TEXT,
            issuer_name       TEXT,
            insider_cik       TEXT,
            insider_name      TEXT,
            insider_name_norm TEXT,
            insider_kind      TEXT,      -- person | institution
            is_director       INTEGER DEFAULT 0,
            is_officer        INTEGER DEFAULT 0,
            is_ten_pct_owner  INTEGER DEFAULT 0,
            officer_title     TEXT,
            security_title    TEXT,
            is_derivative     INTEGER DEFAULT 0,
            txn_code          TEXT,      -- P S A D M F G ...
            acquired_disposed TEXT,      -- A | D, PER TRANSACTION
            shares            REAL,
            price             REAL,
            value             REAL,
            txn_date          TEXT,
            filing_date       TEXT,
            is_10b5_1         INTEGER,   -- NULL = not disclosed (pre-2022 filings)
            ingested_at       TEXT DEFAULT (datetime('now')),
            -- (accession, owner, index) is the natural key. Two identical transactions
            -- in one filing are legitimately distinct rows, so the index is required —
            -- deduping on the values would silently merge them.
            -- owner_seq, not insider_cik: two reportingOwners can share a CIK (or file
            -- with none at all), and keying on it made the second owner's rows collide
            -- with the first's and vanish under INSERT OR IGNORE.
            UNIQUE(accession, owner_seq, txn_index)
        )""")
    for idx, cols in (("idx_f4txn_ticker_date", "ticker, txn_date"),
                      ("idx_f4txn_insider", "insider_name_norm"),
                      ("idx_f4txn_code", "txn_code, acquired_disposed")):
        conn.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON form4_transactions({cols})")
    conn.commit()


def store_filing(conn, ref: FilingRef, parsed: dict) -> int:
    """One row per (filing, insider, transaction). Returns rows written."""
    # ACTUAL writes, via total_changes. `written += 1` after INSERT OR IGNORE counted
    # ATTEMPTS, so a re-run logged rows it had not written and an owner-CIK collision
    # reported a swallowed row as stored.
    before = conn.total_changes
    for owner_seq, owner in enumerate(parsed["owners"]):
        kind = classify_insider(owner.name, owner.is_director, owner.is_officer,
                                owner.is_ten_pct)
        for i, t in enumerate(parsed["transactions"]):
            conn.execute(
                """INSERT OR IGNORE INTO form4_transactions
                   (accession, owner_seq, txn_index, ticker, issuer_cik, issuer_name,
                    insider_cik, insider_name, insider_name_norm, insider_kind,
                    is_director, is_officer, is_ten_pct_owner, officer_title,
                    security_title, is_derivative, txn_code, acquired_disposed,
                    shares, price, value, txn_date, filing_date, is_10b5_1)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ref.adsh, owner_seq, i, parsed["ticker"], parsed["issuer_cik"],
                 parsed["issuer_name"], owner.cik, owner.name,
                 normalize_insider(owner.name), kind,
                 int(owner.is_director), int(owner.is_officer), int(owner.is_ten_pct),
                 owner.title, t.security_title, int(t.is_derivative), t.code,
                 t.acquired_disposed, t.shares, t.price, t.value, t.txn_date,
                 ref.file_date, parsed["is_10b5_1"]))
    return conn.total_changes - before


def scan_watermark(conn) -> str | None:
    row = conn.execute("SELECT MAX(filing_date) FROM filings WHERE source = ?",
                       (FILING_SOURCE,)).fetchone()
    return row[0] if row and row[0] else None


def processed_adsh(conn, since: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT doc_id FROM filings WHERE source = ? AND filing_date >= ?",
        (FILING_SOURCE, since))}


def record_filing(conn, ref: FilingRef, status: str) -> None:
    """Mark processed so later runs skip it. Only after a real fetch+parse outcome — a
    transient 429 records nothing, so the filing is retried rather than lost."""
    conn.execute(
        """INSERT OR IGNORE INTO filings
             (source, doc_id, member_id, filing_date, report_type,
              extraction_status, raw_url)
           VALUES (?, ?, NULL, ?, '4', ?, ?)""",
        (FILING_SOURCE, ref.adsh, ref.file_date, status, archive_dir_url(ref)))
    conn.commit()


def run(dry_run: bool = False, window_days: int | None = None) -> dict:
    t0 = time.time()
    conn = db_connection()
    if not dry_run:
        ensure_tables(conn)

    watermark = scan_watermark(conn)
    if window_days is None:
        if watermark:
            from datetime import date, datetime as _dt, timedelta
            back = (date.today() - _dt.strptime(watermark, "%Y-%m-%d").date()).days
            window_days = max(1, min(back + LOOKBACK_DAYS, MAX_WINDOW_DAYS))
        else:
            window_days = DEFAULT_WINDOW_DAYS

    counts = {"offered": 0, "parsed": 0, "rows": 0, "no_ticker": 0,
              "fetch_failed": 0, "unparseable": 0, "partial": 0}
    status = "ok"
    from datetime import date, timedelta
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=window_days)).isoformat()
    try:
        refs, complete = search_form4_filings(since, today,
                                              deadline=t0 + TIME_BUDGET_SECONDS)
        if not complete:
            counts["partial"] = 1
            status = "partial"
    except Exception as exc:
        # LOUD. A search failure is not a quiet day and must never be logged as one —
        # but a DRY RUN still writes nothing, not even the failure row. A dry run that
        # mutates state is worse than none, because it is used when someone is careful.
        if not dry_run:
            record_activity(RULE, scanned=0, flagged=0, emitted=0,
                            duration_seconds=round(time.time() - t0, 2),
                            notes=f"EDGAR search FAILED: {type(exc).__name__}: "
                                  f"{str(exc)[:120]}")
        conn.close()
        raise

    # OLDEST FIRST, so the watermark advances monotonically and a partial run leaves no
    # gap behind it. Unsorted, the loop walked newest-first into the time-budget break:
    # the newest filing got recorded, the watermark jumped past it, the next window
    # shrank to ~2 days, and the older unprocessed filings fell out of the search range
    # FOREVER with nothing logged. RULE_06 sorts for exactly this reason
    # (rule_06_form4.py:527-529). Sorted BEFORE `seen` is computed, because that bound
    # reads refs[0].
    refs = sorted(refs, key=lambda r: (r.file_date, r.adsh))
    counts["offered"] = len(refs)

    # Bounded by the OLDER of (window start, oldest offered filing) — never by the
    # NEWEST ref, which is what it used to be. That bound dropped every filing already
    # processed on an earlier day of the window, so they were re-fetched every run; and
    # `since` alone would miss a filing EDGAR still offers from before the window.
    floor = min(since, refs[0].file_date) if refs else since
    seen = processed_adsh(conn, floor)

    for ref in refs:
        if ref.adsh in seen:
            continue
        if time.time() - t0 > TIME_BUDGET_SECONDS:
            counts["partial"] = 1
            status = "partial"
            break
        xml_text = fetch_filing_xml(ref)
        if not xml_text:
            counts["fetch_failed"] += 1
            continue                       # deliberately NOT recorded — retry next run
        parsed = parse_transactions(xml_text)
        if parsed is None:
            # WHY THESE ARE SPLIT. `fetch_filing_xml` returns r.text on ANY HTTP 200, so
            # a rate-limit page or a truncated body arrives as a 200. A missing trading
            # symbol on a REAL Form 4 is deterministic and is recorded so it is not
            # retried forever; anything that is not a Form 4 at all is treated as
            # transient and left to retry.
            if _is_form4_document(xml_text):
                counts["no_ticker"] += 1
                if not dry_run:
                    record_filing(conn, ref, "no_ticker")
            else:
                counts["unparseable"] += 1
            continue
        counts["parsed"] += 1
        if not dry_run:
            counts["rows"] += store_filing(conn, ref, parsed)
            record_filing(conn, ref, "ok")
        else:
            counts["rows"] += len(parsed["owners"]) * len(parsed["transactions"])

    if not dry_run:
        conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM form4_transactions").fetchone()[0] \
        if not dry_run else 0
    notes = (f"offered={counts['offered']}, parsed={counts['parsed']}, "
             f"rows={counts['rows']}, no_ticker={counts['no_ticker']}, "
             f"fetch_failed={counts['fetch_failed']}, "
             f"unparseable={counts['unparseable']}, window={window_days}d, "
             f"status={status}, store_total={total}, dry_run={dry_run}")
    if not dry_run:
        record_activity(RULE, scanned=counts["offered"], flagged=counts["parsed"],
                        emitted=counts["rows"],
                        duration_seconds=round(time.time() - t0, 2), notes=notes)
    print(f"[{RULE}] {notes}")
    conn.close()
    return {**counts, "status": status, "dry_run": dry_run}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # The scheduler passes --emit-alerts to every job. This one emits NO alerts — it is
    # ingestion — but the flag must parse or argparse exits 2, which is how the morning
    # brief failed 100% of its runs while looking scheduled.
    p.add_argument("--emit-alerts", action="store_true",
                   help="accepted for scheduler compatibility; this job emits no alerts")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be stored, writing nothing at all")
    p.add_argument("--days", type=int, default=None, help="override the scan window")
    return p


if __name__ == "__main__":
    a = build_parser().parse_args()
    run(dry_run=a.dry_run, window_days=a.days)
