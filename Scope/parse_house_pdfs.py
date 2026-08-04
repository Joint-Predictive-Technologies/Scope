#!/usr/bin/env python3
"""
parse_house_pdfs.py

Reads pending House PTR filings from SQLite, downloads PDFs, extracts transaction
text using pdfplumber first and pytesseract OCR as fallback, parses stock
transaction rows, and stores them in the transactions table.
"""

from __future__ import annotations

import io
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import pdfplumber
import pytesseract
import requests
from PIL import Image
from dotenv import load_dotenv

from jpt_common import db_connection


BATCH_SIZE = 20
DOWNLOAD_SLEEP_SECONDS = 1
REQUEST_TIMEOUT_SECONDS = 30

USER_AGENT = (
    "Scope Congressional Trade Tracker/0.1 "
    "(research project; contact: contact@example.com)"
)

AMOUNT_RE = re.compile(
    r"\$\s*[\d,]+(?:\.\d+)?\s*(?:-|–|—|to)\s*\$?\s*[\d,]+(?:\.\d+)?"
    r"|Over\s+\$\s*[\d,]+(?:\.\d+)?"
    r"|\$\s*[\d,]+(?:\.\d+)?\+?",
    re.IGNORECASE,
)

DATE_RE = re.compile(
    r"\b(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\b"
)

TICKER_RE = re.compile(r"\b[A-Z]{1,5}(?:\.[A-Z])?\b")

PARENTHESIZED_TICKER_RE = re.compile(r"\(([A-Z]{1,5}(?:\.[A-Z])?)\)")

TICKER_BLOCKLIST: frozenset[str] = frozenset({
    "US", "SP", "NA", "N/A", "ST", "CA", "GE",
})


def is_blocklisted(ticker: str) -> bool:
    return len(ticker) == 1 or ticker in TICKER_BLOCKLIST


# Matches lone-letter name abbreviations such as "U.S", "D.R", "W.R", "N.V" —
# these show up at the start of company/issuer names (e.g. "U.S. Treasury",
# "D.R. Horton") and get mistaken for tickers, but real ticker symbols never
# take the form of a single letter + period + single letter.
NAME_ABBREVIATION_RE = re.compile(r"^[A-Z]\.[A-Z]$")

# House PTR filings encode the transaction type as a single-letter code in the
# "Type" column: P (purchase), S (partial)/S (full) (sale), E (exchange).
# Lookarounds require whitespace on both sides (rather than \b) so this
# doesn't match letters embedded in abbreviations like "U.S." or "SP".
TRANSACTION_TYPE_RE = re.compile(
    r"(?<!\S)(?:S\s*\(\s*(?:partial|full)\s*\)|[PSE])(?!\S)"
)


@dataclass
class Filing:
    id: int
    member_id: str | None
    raw_url: str


@dataclass
class ParsedTransaction:
    raw_ticker_string: str | None
    raw_description: str | None
    transaction_type: str | None
    amount_band: str | None
    transaction_date: str | None


def ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_id          INTEGER,
            member_id          TEXT,
            raw_ticker_string  TEXT,
            raw_description    TEXT,
            transaction_type   TEXT,
            amount_band        TEXT,
            transaction_date   TEXT,
            ticker_id          INTEGER
        );
        """
    )

    conn.commit()


def fetch_pending_filings(conn) -> list[Filing]:
    rows = conn.execute(
        """
        SELECT id, member_id, raw_url
        FROM filings
        WHERE extraction_status = 'pending'
          AND source = 'house'
          AND raw_url IS NOT NULL
        ORDER BY id ASC
        LIMIT ?;
        """,
        (BATCH_SIZE,),
    ).fetchall()

    return [
        Filing(
            # Preserve SQL NULL — never coerce to the string "None", which used
            # to poison transactions.member_id for unmatched filers.
            id=int(row["id"]),
            member_id=row["member_id"],
            raw_url=str(row["raw_url"]),
        )
        for row in rows
    ]


def update_filing_status(conn, filing_id: int, status: str) -> None:
    conn.execute(
        """
        UPDATE filings
        SET extraction_status = ?
        WHERE id = ?;
        """,
        (status, filing_id),
    )
    conn.commit()


def normalize_house_url(url: str) -> str:
    url = url.strip()

    if url.startswith("http://") or url.startswith("https://"):
        return url

    if url.startswith("//"):
        return f"https:{url}"

    if url.startswith("/"):
        return f"https://disclosures-clerk.house.gov{url}"

    return f"https://disclosures-clerk.house.gov/{url}"


def download_pdf(url: str) -> bytes | None:
    normalized_url = normalize_house_url(url)

    try:
        response = requests.get(
            normalized_url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code == 404:
            print(f"WARNING: 404 PDF not found, skipping: {normalized_url}")
            return None

        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if "pdf" not in content_type and not response.content.startswith(b"%PDF"):
            print(f"WARNING: Response may not be a PDF: {normalized_url}")

        return response.content

    except requests.exceptions.RequestException as exc:
        print(f"ERROR: Failed to download PDF {normalized_url}: {exc}")
        return None


def extract_text_pdfplumber(pdf_bytes: bytes) -> str:
    chunks: list[str] = []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    chunks.append(text)

    except Exception as exc:
        print(f"WARNING: pdfplumber text extraction failed: {exc}")
        return ""

    return "\n".join(chunks).strip()


def extract_text_ocr(pdf_bytes: bytes) -> str:
    chunks: list[str] = []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                try:
                    page_image = page.to_image(resolution=300)
                    image: Image.Image = page_image.original
                    text = pytesseract.image_to_string(image)

                    if text.strip():
                        chunks.append(text)

                except Exception as exc:
                    print(f"WARNING: OCR failed on page {page_number}: {exc}")

    except Exception as exc:
        print(f"WARNING: Could not open PDF for OCR: {exc}")
        return ""

    return "\n".join(chunks).strip()


def normalize_date(value: str | None) -> str | None:
    if not value:
        return None

    value = value.strip()

    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    return value


def normalize_transaction_type(value: str | None) -> str | None:
    if not value:
        return None

    code = re.sub(r"\s+", " ", value.strip()).upper()

    if code == "P":
        return "purchase"

    if code == "S (PARTIAL)":
        return "sale_partial"

    if code == "S (FULL)":
        return "sale_full"

    if code == "S":
        return "sale"

    if code == "E":
        return "exchange"

    return value.strip().lower()


def clean_cell(value: str | None) -> str | None:
    if value is None:
        return None

    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


RANGE_TAIL_RE = re.compile(r"^\s*(?:-|–|—|to)\s*$", re.IGNORECASE)
OVER_TAIL_RE = re.compile(r"\bOver\s*$", re.IGNORECASE)
AMOUNT_VALUE_RE = re.compile(r"\$\s*[\d,]+(?:\.\d+)?")


def resolve_amount_band(after_type: str, next_line: str | None) -> str | None:
    """
    Amount band cells often wrap onto the next line of the PDF table, e.g.
    "...$15,001 -" / "[ST] $50,000" or "...Spouse/DC Over" / "[GS] $1,000,000".
    Stitch the wrapped value back together using the following line.
    """
    amount_match = AMOUNT_RE.search(after_type)
    next_line = next_line or ""

    if amount_match:
        amount_band = amount_match.group(0)
        tail = after_type[amount_match.end():]

        if RANGE_TAIL_RE.match(tail):
            continuation = AMOUNT_VALUE_RE.search(next_line)
            if continuation:
                amount_band = f"{amount_band} - {continuation.group(0)}"

        return clean_cell(amount_band)

    if OVER_TAIL_RE.search(after_type):
        continuation = AMOUNT_VALUE_RE.search(next_line)
        if continuation:
            return clean_cell(f"Over {continuation.group(0)}")

    return None


def parse_table_like_lines(text: str) -> list[ParsedTransaction]:
    parsed: list[ParsedTransaction] = []
    raw_lines = text.splitlines()

    for index, raw_line in enumerate(raw_lines):
        line = re.sub(r"\s+", " ", raw_line).strip()

        if not line:
            continue

        lower = line.lower()
        if all(word in lower for word in ("date", "owner", "ticker", "asset")):
            continue

        type_match = TRANSACTION_TYPE_RE.search(line)

        if not type_match:
            continue

        # The asset description (e.g. "Treasury Note 4% DUE 7/31/29") often
        # contains its own date/dollar-looking text, so only look for the
        # transaction date and amount in the columns that follow the type
        # code, matching the House PTR table layout.
        after_type = line[type_match.end():]

        date_match = DATE_RE.search(after_type)

        if not date_match:
            continue

        next_line = raw_lines[index + 1] if index + 1 < len(raw_lines) else None
        amount_band = resolve_amount_band(after_type, next_line)

        if not amount_band:
            continue

        transaction_date = normalize_date(date_match.group(0))
        transaction_type = normalize_transaction_type(type_match.group(0))

        before_type = line[: type_match.start()].strip()

        ignored_tickers = {
            "SP",
            "IRA",
            "ETF",
            "NYSE",
            "NASDAQ",
            "NMS",
            "NQ",
            "JT",
            "DC",
        }

        # House PTR descriptions list the ticker in parentheses, e.g.
        # "Schlumberger N.V. - Common Stock (SLB)". Prefer that over the
        # first all-caps-looking token, which is often a name abbreviation
        # like "N.V" or "D.R" rather than the actual ticker.
        ticker = None
        paren_match = PARENTHESIZED_TICKER_RE.search(before_type)

        if paren_match:
            candidate = paren_match.group(1).strip().upper()
            if candidate not in ignored_tickers:
                ticker = candidate

        # Long company names (e.g. "D.R. Horton, Inc. Common Stock") often wrap
        # so the parenthesized ticker lands on the following PDF line, e.g.
        # "(DHI) [ST]" or "Stock (WRB) [ST]". Only treat that line as a
        # continuation (not an unrelated new row) when it has no type code.
        if ticker is None and next_line is not None:
            continuation = re.sub(r"\s+", " ", next_line).strip()
            if continuation and not TRANSACTION_TYPE_RE.search(continuation):
                paren_match = PARENTHESIZED_TICKER_RE.search(continuation)
                if paren_match:
                    candidate = paren_match.group(1).strip().upper()
                    if candidate not in ignored_tickers:
                        ticker = candidate

        if ticker is None:
            for candidate in TICKER_RE.findall(before_type):
                candidate = candidate.strip().upper()
                # Single-letter name abbreviations like "U.S", "D.R", "W.R",
                # "N.V" are frequently mistaken for tickers (real tickers never
                # take the form of a lone letter + period + lone letter).
                if NAME_ABBREVIATION_RE.match(candidate):
                    continue
                if candidate not in ignored_tickers:
                    ticker = candidate
                    break

        # FIX A — a rejected ticker must never cost us the TRANSACTION.
        #
        # This was `continue`, which discarded the whole row: the member, the date,
        # the amount and the asset name, not just the unusable symbol. Because
        # `is_blocklisted` is True for ANY one-character ticker, every Ford (F),
        # Visa (V), AT&T (T), Citigroup (C) and General Electric line vanished from
        # `transactions` entirely — invisible to every rule, score and audit, and
        # leaving no trace to notice it by. 207 such rows exist from before the
        # blocklist landed (2026-07-05); none since, against 441 House rows.
        #
        # Dropping the SYMBOL is right — a bare "A" lifted out of a company name is
        # not Agilent. Dropping the HOLDING is not.
        #
        # `raw_ticker_string=None` is what makes the kept row safe: RULE_01B,
        # RULE_02 and RULE_CLUSTER all require a non-empty ticker (directly or via
        # COALESCE), so the row cannot become a corroboration key. The linker still
        # sees it (`WHERE ticker_id IS NULL`) and resolves it from the description —
        # which fix C below now leaves intact. That is where a genuine single-letter
        # ticker is recovered, not here.
        if ticker and is_blocklisted(ticker):
            ticker = None

        description = before_type

        if ticker:
            # FIX C — the `\b{ticker}\b` deletion that used to follow this line is
            # gone. It removed the matched token from the description, which is the
            # exact text `resolve_tickers.resolve_by_company_name` fuzzy-matches on:
            # "JP Morgan …" became "Morgan …", "MACOM Technology Solutions" became
            # "Technology Solutions", "Arlington, TX Municipal Bond" became
            # "Arlington, Municipal Bond". The parser was destroying the evidence the
            # linker needs and then the linker mis-matched the remainder.
            #
            # Stripping the redundant "(NVDA)" parenthetical stays: it is not part of
            # the company name, and on a fallback-lifted token it is a no-op.
            description = re.sub(rf"\(\s*{re.escape(ticker)}\s*\)", "", description, count=1).strip()

        if not ticker and not description:
            continue

        parsed.append(
            ParsedTransaction(
                raw_ticker_string=ticker,
                raw_description=clean_cell(description),
                transaction_type=transaction_type,
                amount_band=amount_band,
                transaction_date=transaction_date,
            )
        )

    return parsed


def parse_pipe_or_tab_rows(text: str) -> list[ParsedTransaction]:
    parsed: list[ParsedTransaction] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if "|" in line:
            cells = [clean_cell(cell) for cell in line.split("|")]
        elif "\t" in line:
            cells = [clean_cell(cell) for cell in line.split("\t")]
        else:
            continue

        cells = [cell for cell in cells if cell]

        if len(cells) < 5:
            continue

        lower = " ".join(cells).lower()
        if "ticker" in lower and "asset" in lower:
            continue

        date_cell = next((cell for cell in cells if DATE_RE.search(cell)), None)
        type_cell = next((cell for cell in cells if TRANSACTION_TYPE_RE.search(cell)), None)
        amount_cell = next((cell for cell in cells if AMOUNT_RE.search(cell)), None)

        if not date_cell or not type_cell or not amount_cell:
            continue

        ticker_cell = None
        for cell in cells:
            value = cell.strip().upper()
            if TICKER_RE.fullmatch(value):
                ticker_cell = value
                break

        # FIX A, second path. Same defect, same reasoning as parse_table_like_lines:
        # this was `continue` and discarded the whole row. Clearing the cell instead
        # keeps the transaction, and because `asset_candidates` below excludes
        # whatever `ticker_cell` holds, setting it to None here also returns the
        # rejected value to the description rather than losing it.
        if ticker_cell and is_blocklisted(ticker_cell):
            ticker_cell = None

        asset_candidates = [
            cell
            for cell in cells
            if cell not in {date_cell, type_cell, amount_cell, ticker_cell}
        ]

        parsed.append(
            ParsedTransaction(
                raw_ticker_string=ticker_cell,
                raw_description=clean_cell(" ".join(asset_candidates)),
                transaction_type=normalize_transaction_type(type_cell),
                amount_band=clean_cell(amount_cell),
                transaction_date=normalize_date(DATE_RE.search(date_cell).group(0)),
            )
        )

    return parsed


def dedupe_transactions(
    transactions: Iterable[ParsedTransaction],
) -> list[ParsedTransaction]:
    seen: set[tuple[str | None, str | None, str | None, str | None, str | None]] = set()
    result: list[ParsedTransaction] = []

    for tx in transactions:
        key = (
            tx.raw_ticker_string,
            tx.raw_description,
            tx.transaction_type,
            tx.amount_band,
            tx.transaction_date,
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(tx)

    return result


def parse_transactions(text: str) -> list[ParsedTransaction]:
    pipe_rows = parse_pipe_or_tab_rows(text)
    line_rows = parse_table_like_lines(text)

    return dedupe_transactions([*pipe_rows, *line_rows])


def store_transactions(
    conn,
    filing: Filing,
    transactions: list[ParsedTransaction],
) -> None:
    conn.execute(
        """
        DELETE FROM transactions
        WHERE filing_id = ?;
        """,
        (filing.id,),
    )

    rows = [
        {
            "filing_id": filing.id,
            "member_id": filing.member_id,
            "raw_ticker_string": tx.raw_ticker_string,
            "raw_description": tx.raw_description,
            "transaction_type": tx.transaction_type,
            "amount_band": tx.amount_band,
            "transaction_date": tx.transaction_date,
        }
        for tx in transactions
    ]

    if rows:
        conn.executemany(
            """
            INSERT INTO transactions (
                filing_id,
                member_id,
                raw_ticker_string,
                raw_description,
                transaction_type,
                amount_band,
                transaction_date
            )
            VALUES (
                :filing_id,
                :member_id,
                :raw_ticker_string,
                :raw_description,
                :transaction_type,
                :amount_band,
                :transaction_date
            );
            """,
            rows,
        )

    conn.commit()


def process_filing(conn, filing: Filing) -> tuple[bool, int, bool]:
    print(f"Processing House filing id={filing.id}, member_id={filing.member_id}")

    pdf_bytes = download_pdf(filing.raw_url)

    if pdf_bytes is None:
        update_filing_status(conn, filing.id, "parse_failed")
        return False, 0, False

    text = extract_text_pdfplumber(pdf_bytes)
    used_ocr = False

    if len(text.strip()) < 50:
        print(f"Low text yield for filing id={filing.id}; falling back to OCR.")
        text = extract_text_ocr(pdf_bytes)
        used_ocr = True

    if len(text.strip()) < 20:
        print(f"ERROR: No usable text extracted for filing id={filing.id}.")
        update_filing_status(conn, filing.id, "parse_failed")
        return True, 0, used_ocr

    transactions = parse_transactions(text)

    if not transactions:
        print(f"ERROR: No transactions parsed for filing id={filing.id}.")
        update_filing_status(conn, filing.id, "parse_failed")
        return True, 0, used_ocr

    store_transactions(conn, filing, transactions)

    if used_ocr:
        update_filing_status(conn, filing.id, "parsed_low_confidence")
    else:
        update_filing_status(conn, filing.id, "parsed_ok")

    print(f"Parsed {len(transactions)} transactions from filing id={filing.id}.")
    return True, len(transactions), used_ocr


def main() -> None:
    load_dotenv()
    import time as _time
    from jpt_common import record_activity
    _t0 = _time.time()

    downloaded_count = 0
    parsed_transaction_count = 0
    failed_count = 0
    pending_before = "?"

    try:
        with db_connection() as conn:
            ensure_tables(conn)
            pending_before = conn.execute(
                "SELECT COUNT(*) FROM filings WHERE extraction_status='pending' AND source='house'"
            ).fetchone()[0]
            filings = fetch_pending_filings(conn)

            if not filings:
                print("No pending House filings found.")
                record_activity("PARSE_HOUSE_PDFS", scanned=0, flagged=0, emitted=0,
                                duration_seconds=round(_time.time() - _t0, 2),
                                notes="pending_before=0, nothing to parse")
                return

            print(f"Found {len(filings)} pending House filings. Processing batch of {BATCH_SIZE}.")

            for index, filing in enumerate(filings, start=1):
                try:
                    downloaded, parsed_count, _used_ocr = process_filing(conn, filing)

                    if downloaded:
                        downloaded_count += 1

                    if parsed_count > 0:
                        parsed_transaction_count += parsed_count
                    else:
                        failed_count += 1

                except KeyboardInterrupt:
                    print("Interrupted by user.")
                    sys.exit(130)

                except Exception as exc:
                    print(f"ERROR: Unexpected failure for filing id={filing.id}: {exc}")
                    update_filing_status(conn, filing.id, "parse_failed")
                    failed_count += 1

                if index < len(filings):
                    time.sleep(DOWNLOAD_SLEEP_SECONDS)

        print(
            "Done. "
            f"{downloaded_count} PDFs downloaded, "
            f"{parsed_transaction_count} transactions parsed, "
            f"{failed_count} failed."
        )
        notes = (f"pending_before={pending_before}, processed={len(filings)}, "
                 f"transactions_parsed={parsed_transaction_count}, failures={failed_count}")
        record_activity("PARSE_HOUSE_PDFS", scanned=len(filings), flagged=downloaded_count,
                        emitted=parsed_transaction_count,
                        duration_seconds=round(_time.time() - _t0, 2), notes=notes)

    except SystemExit:
        raise  # deliberate exit (e.g. KeyboardInterrupt) — not a failure to log
    except Exception as exc:
        # Catastrophic failure in setup (db/ensure_tables/fetch) — never die silent.
        details = (f"CRITICAL: parse_house_pdfs failed before completing "
                   f"(pending_before={pending_before}) — {type(exc).__name__}: {exc}")
        try:
            _c = db_connection()
            prev = _c.execute(
                "SELECT notes FROM activity_log WHERE source='PARSE_HOUSE_PDFS' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if prev and (prev["notes"] or "").startswith(("ERROR", "CRITICAL")):
                details += " [consecutive failure — prior run also failed]"
            _c.close()
        except Exception:
            pass
        record_activity("PARSE_HOUSE_PDFS", scanned=0, flagged=0, emitted=0,
                        duration_seconds=round(_time.time() - _t0, 2), notes=details)
        print(details)
        sys.exit(1)


if __name__ == "__main__":
    main()