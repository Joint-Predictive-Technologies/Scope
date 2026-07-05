#!/usr/bin/env python3
"""
rule_06_form4.py

Detects significant executive insider trades (SEC Form 4) that deviate
from the executive's historical pattern and emits RULE_06 alerts.
"""

from __future__ import annotations

import argparse
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
SLEEP = 0.5
HISTORY_COUNT = 5
MIN_VALUE = 50_000.0
MIN_MULTIPLE = 2.0


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

def search_form4_filings(since: str, today: str) -> list[FilingRef]:
    refs: list[FilingRef] = []
    offset = 0
    page_size = 100

    while True:
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
            break

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

    return refs


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

def historical_avg(owner_cik: str, skip_adsh: str) -> float | None:
    """
    Return the mean total P/S value across the last HISTORY_COUNT Form 4
    filings for this owner (excluding the current filing). None if no history.
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
        ref = FilingRef(adsh=adsh, owner_cik=owner_cik, filename="", file_date="")
        xml_text = fetch_filing_xml(ref)
        if not xml_text:
            continue
        parsed = parse_form4(xml_text)
        if parsed and parsed.ps_value > 0:
            totals.append(parsed.ps_value)

    return sum(totals) / len(totals) if totals else None


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


def run(since: str, today: str, emit_alerts: bool) -> tuple[int, int]:
    """Return (significant_count, alerts_emitted)."""
    refs = search_form4_filings(since, today)

    significant = 0
    emitted = 0

    conn = db_connection() if emit_alerts else None

    for ref in refs:
        xml_text = fetch_filing_xml(ref)
        if not xml_text:
            continue

        parsed = parse_form4(xml_text)
        if not parsed or not parsed.transactions:
            continue

        current_value = parsed.ps_value
        if current_value < MIN_VALUE:
            continue

        avg = historical_avg(ref.owner_cik, ref.adsh)
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

    if conn is not None:
        conn.close()

    return significant, emitted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect significant SEC Form 4 insider trades (RULE_06)."
    )
    parser.add_argument(
        "--since",
        default=(date.today() - timedelta(days=7)).isoformat(),
        help="Start date YYYY-MM-DD. Default: 7 days ago.",
    )
    parser.add_argument(
        "--emit-alerts",
        action="store_true",
        help="Insert significant findings into the alerts table.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    today = date.today().isoformat()
    if args.since > today:
        print("ERROR: --since date is in the future.")
        raise SystemExit(1)

    print(f"Scanning Form 4 filings from {args.since} to {today} ...")
    significant, emitted = run(args.since, today, args.emit_alerts)
    print(f"{significant} significant filings found, {emitted} alerts emitted")


if __name__ == "__main__":
    main()
