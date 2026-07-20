#!/usr/bin/env python3
"""
ingest_house_index.py

Downloads the House annual financial disclosure ZIP index, extracts the XML,
filters PTR filings, and registers them in SQLite for later PDF parsing.
"""

from __future__ import annotations

import argparse
import difflib
import io
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

from jpt_common import db_connection


HOUSE_ZIP_URL_TEMPLATE = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PTR_PDF_URL_TEMPLATE = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

REQUEST_TIMEOUT_SECONDS = 60
USER_AGENT = "Scope Congressional Trade Tracker/0.1"


@dataclass
class HouseFiling:
    doc_id: str
    first_name: str | None
    last_name: str | None
    filing_date: str | None
    filing_type: str
    member_id: str | None
    raw_url: str


def start_ingestion_run(conn, year: int) -> int:
    cursor = conn.execute(
        """
        INSERT INTO ingestion_runs (source, started_at, notes)
        VALUES ('house', datetime('now'), ?);
        """,
        (f"house_fd_index year={year}",),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_ingestion_run(conn, run_id: int, filings_seen: int, errors: int, notes: str) -> None:
    conn.execute(
        """
        UPDATE ingestion_runs
        SET finished_at = datetime('now'),
            filings_seen = ?,
            errors = ?,
            notes = ?
        WHERE id = ?;
        """,
        (filings_seen, errors, notes, run_id),
    )
    conn.commit()


def download_house_zip(year: int) -> bytes:
    url = HOUSE_ZIP_URL_TEMPLATE.format(year=year)
    print(f"Downloading House index ZIP: {url}")

    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code == 404:
            print(f"ERROR: House disclosure ZIP not found for year {year}: {url}")
            sys.exit(1)

        response.raise_for_status()
        return response.content

    except requests.exceptions.RequestException as exc:
        print(f"ERROR: Failed to download House disclosure ZIP: {exc}")
        sys.exit(1)


def extract_xml_from_zip(zip_bytes: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xml_names = [
                name
                for name in zf.namelist()
                if name.lower().endswith(".xml")
            ]

            if not xml_names:
                print("ERROR: No XML file found inside House ZIP.")
                sys.exit(1)

            xml_name = xml_names[0]
            print(f"Extracting XML: {xml_name}")
            return zf.read(xml_name)

    except zipfile.BadZipFile:
        print("ERROR: Downloaded House file is not a valid ZIP.")
        sys.exit(1)


def strip_namespace(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def node_to_dict(node: ET.Element) -> dict[str, str]:
    data: dict[str, str] = {}

    for child in list(node):
        key = strip_namespace(child.tag)
        value = (child.text or "").strip()
        if value:
            data[key] = value

    return data


def get_field(entry: dict[str, str], *names: str) -> str | None:
    normalized = {
        key.lower().replace("_", "").replace("-", ""): value
        for key, value in entry.items()
    }

    for name in names:
        key = name.lower().replace("_", "").replace("-", "")
        value = normalized.get(key)
        if value:
            return value.strip()

    return None


def parse_filing_date(value: str | None) -> str | None:
    if not value:
        return None

    value = value.strip()

    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    return value


def normalize_name(value: str | None) -> str:
    if not value:
        return ""

    return " ".join(value.strip().lower().replace(",", " ").split())


def load_members(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT bioguide_id, full_name, party, state, chamber
        FROM members;
        """
    ).fetchall()

    members: list[dict[str, Any]] = []

    for row in rows:
        full_name = str(row["full_name"] or "").strip()
        if not full_name:
            continue

        members.append(
            {
                "bioguide_id": row["bioguide_id"],
                "full_name": full_name,
                "normalized_name": normalize_name(full_name),
                "state": row["state"],
                "chamber": row["chamber"],
            }
        )

    return members


def match_member_id(
    first_name: str | None,
    last_name: str | None,
    members: list[dict[str, Any]],
) -> str | None:
    first = normalize_name(first_name)
    last = normalize_name(last_name)

    if not last:
        return None

    target_variants = []

    if first and last:
        target_variants.extend(
            [
                f"{first} {last}",
                f"{last} {first}",
            ]
        )

    target_variants.append(last)

    member_names = [member["normalized_name"] for member in members]

    best_member_id = None
    best_score = 0.0

    for target in target_variants:
        matches = difflib.get_close_matches(target, member_names, n=3, cutoff=0.72)

        for match in matches:
            score = difflib.SequenceMatcher(None, target, match).ratio()

            if last and last not in match:
                score -= 0.25

            if score > best_score:
                best_score = score
                best_member_id = next(
                    (
                        member["bioguide_id"]
                        for member in members
                        if member["normalized_name"] == match
                    ),
                    None,
                )

    if best_score < 0.72:
        return None

    return best_member_id


def iter_xml_entries(xml_bytes: bytes) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        print(f"ERROR: Could not parse House XML: {exc}")
        sys.exit(1)

    entries: list[dict[str, str]] = []

    for node in root.iter():
        children = list(node)
        if not children:
            continue

        data = node_to_dict(node)

        if not data:
            continue

        has_doc_id = get_field(data, "DocID", "DocumentID", "DocId")
        has_filing_type = get_field(data, "FilingType", "Filing Type")

        if has_doc_id and has_filing_type:
            entries.append(data)

    return entries


def parse_house_filings(
    entries: list[dict[str, str]],
    members: list[dict[str, Any]],
    year: int,
) -> list[HouseFiling]:
    filings: list[HouseFiling] = []

    for entry in entries:
        filing_type = get_field(entry, "FilingType", "Filing Type")

        # Only "P" (Periodic Transaction Report) entries have a PDF at the
        # ptr-pdfs URL. "X" (extension request) and other filing types 404.
        if filing_type != "P":
            continue

        doc_id = get_field(entry, "DocID", "DocumentID", "DocId")

        if not doc_id:
            print(f"WARNING: PTR entry missing DocID, skipping: {entry}")
            continue

        first_name = get_field(
            entry,
            "First",
            "FirstName",
            "FilerFirstName",
            "PrefixFirstName",
        )

        last_name = get_field(
            entry,
            "Last",
            "LastName",
            "FilerLastName",
            "PrefixLastName",
        )

        filing_date = parse_filing_date(
            get_field(entry, "FilingDate", "DateReceived", "Filing Date")
        )

        member_id = match_member_id(first_name, last_name, members)

        if member_id is None:
            print(
                "WARNING: Could not match House filer to members table: "
                f"{first_name or ''} {last_name or ''} doc_id={doc_id}"
            )

        raw_url = HOUSE_PTR_PDF_URL_TEMPLATE.format(
            year=year,
            doc_id=quote(str(doc_id).strip()),
        )

        filings.append(
            HouseFiling(
                doc_id=str(doc_id).strip(),
                first_name=first_name,
                last_name=last_name,
                filing_date=filing_date,
                filing_type=filing_type,
                member_id=member_id,
                raw_url=raw_url,
            )
        )

    return filings


def upsert_filing(conn, filing: HouseFiling) -> bool:
    """Insert or update a filing. Returns True if a NEW filing was inserted."""
    existing = conn.execute(
        """
        SELECT id
        FROM filings
        WHERE source = 'house'
          AND doc_id = ?;
        """,
        (filing.doc_id,),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE filings
            SET member_id = ?,
                filing_date = ?,
                report_type = 'PTR',
                raw_url = ?
            WHERE id = ?;
            """,
            (
                filing.member_id,
                filing.filing_date,
                filing.raw_url,
                existing["id"],
            ),
        )
        return False
    else:
        conn.execute(
            """
            INSERT INTO filings (
                source,
                doc_id,
                member_id,
                filing_date,
                report_type,
                extraction_status,
                raw_url
            )
            VALUES (
                'house',
                ?,
                ?,
                ?,
                'PTR',
                'pending',
                ?
            );
            """,
            (
                filing.doc_id,
                filing.member_id,
                filing.filing_date,
                filing.raw_url,
            ),
        )
        return True


def register_filings(conn, filings: list[HouseFiling]) -> tuple[int, int]:
    """Returns (total_registered, new_inserted)."""
    new_count = 0
    for filing in filings:
        if upsert_filing(conn, filing):
            new_count += 1

    conn.commit()
    return len(filings), new_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest House annual disclosure index and register PTR filings."
    )

    parser.add_argument(
        "--year",
        type=int,
        default=datetime.now().year,
        help="Disclosure year to ingest. Default: current year.",
    )
    # Accepted (and ignored) for scheduler-runner uniformity.
    parser.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)

    return parser


def main() -> None:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    year = args.year

    import time as _time
    from jpt_common import record_activity
    _t0 = _time.time()

    with db_connection() as conn:
        run_id = start_ingestion_run(conn, year)

        try:
            zip_bytes = download_house_zip(year)
            xml_bytes = extract_xml_from_zip(zip_bytes)
            entries = iter_xml_entries(xml_bytes)

            members = load_members(conn)
            filings = parse_house_filings(entries, members, year)
            registered_count, new_count = register_filings(conn, filings)
            unmatched = sum(1 for f in filings if f.member_id is None)

            details = (
                f"{len(entries)} entries scanned, {registered_count} PTRs found, "
                f"{new_count} new filings inserted, {unmatched} unmatched filers"
            )

            finish_ingestion_run(conn, run_id, len(entries), 0, details)
            print(details)

            # Activity log — so a future stall is visible here, not only via a
            # diagnostic sweep. scanned=index entries, flagged=PTRs, emitted=new.
            record_activity("INGEST_HOUSE_INDEX", scanned=len(entries),
                            flagged=registered_count, emitted=new_count,
                            duration_seconds=round(_time.time() - _t0, 2), notes=details)

        except Exception as exc:
            details = f"Failed ingesting House index for year {year}: {exc}"
            finish_ingestion_run(conn, run_id, 0, 1, details)
            record_activity("INGEST_HOUSE_INDEX", scanned=0, flagged=0, emitted=0,
                            duration_seconds=round(_time.time() - _t0, 2), notes=details)
            print(f"ERROR: {details}")
            sys.exit(1)


if __name__ == "__main__":
    main()