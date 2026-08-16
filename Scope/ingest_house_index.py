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
import re
import sqlite3
import sys
import unicodedata
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


class IngestError(RuntimeError):
    """A fetch/parse failure with a note ready for activity_log. Raised instead of
    sys.exit() so main()'s handler always logs an error row (a silent SystemExit
    would bypass logging and leave zero trace of a failed run)."""


def download_house_zip(year: int) -> bytes:
    url = HOUSE_ZIP_URL_TEMPLATE.format(year=year)
    print(f"Downloading House index ZIP: {url}")

    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        # Network-layer failure (timeout, connection reset, DNS): report the
        # exception type so a transient blip is distinguishable from a 4xx/5xx.
        raise IngestError(f"network {type(exc).__name__} fetching {url}: {exc}") from exc

    if response.status_code == 404:
        # Distinct, actionable case: the source URL likely moved.
        raise IngestError(f"HTTP 404 — House disclosure ZIP not found (source URL may "
                          f"have changed) for year {year}: {url}")
    if response.status_code != 200:
        raise IngestError(f"HTTP {response.status_code} fetching {url}")

    return response.content


def extract_xml_from_zip(zip_bytes: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xml_names = [
                name
                for name in zf.namelist()
                if name.lower().endswith(".xml")
            ]

            if not xml_names:
                raise IngestError("no XML file found inside the House ZIP")

            xml_name = xml_names[0]
            print(f"Extracting XML: {xml_name}")
            return zf.read(xml_name)

    except zipfile.BadZipFile as exc:
        raise IngestError("downloaded House file is not a valid ZIP "
                          "(truncated download or an error page served instead)") from exc


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


def fold_accents(value: str) -> str:
    """Fold diacritics to ASCII (Sánchez -> Sanchez) so House PTR filings, which
    spell names in plain ASCII, match the accented members.full_name spellings."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(ch)
    )


def normalize_name(value: str | None) -> str:
    if not value:
        return ""

    return " ".join(fold_accents(value).strip().lower().replace(",", " ").split())


# Credentials / generational suffixes that pollute House filer name fields
# (e.g. "Neal Patrick MD, Facs" | "Dunn"). Stripped before matching.
_CREDENTIAL_TOKENS = {
    "md", "do", "dds", "dmd", "phd", "jd", "esq", "cpa", "rn", "facs", "mba",
    "jr", "sr", "ii", "iii", "iv", "v",
}


def name_tokens(value: str | None) -> list[str]:
    """Lowercase name tokens with nicknames, parentheticals, punctuation and
    credential/suffix tokens removed. Deterministic — safe to call repeatedly."""
    if not value:
        return []
    v = fold_accents(value).lower()
    v = re.sub(r'"[^"]*"', " ", v)      # drop "Buddy" style nicknames
    v = re.sub(r"\([^)]*\)", " ", v)    # drop parentheticals
    v = re.sub(r"[.,;:]", " ", v)       # punctuation -> space
    return [t for t in v.split() if t and t not in _CREDENTIAL_TOKENS]


def split_member_name(full_name: str) -> tuple[list[str], list[str]]:
    """members.full_name is stored 'Last, First Middle'. Returns
    (last_tokens, first_tokens). Handles compound surnames (McClain Delaney)."""
    if "," in full_name:
        last_part, first_part = full_name.split(",", 1)
    else:
        parts = full_name.rsplit(" ", 1)
        first_part, last_part = (parts[0], parts[1]) if len(parts) == 2 else ("", full_name)
    return name_tokens(last_part), name_tokens(first_part)


def load_members(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT bioguide_id, full_name, party, state, chamber
        FROM members;
        """
    ).fetchall()

    # Service periods, so two people with the same name can be told apart by WHEN
    # they served. Loaded in one query and attached per member rather than queried
    # per candidate — `match_member_id` is called once per index entry (1,547 on a
    # normal run) and must not become 1,547 x N round trips.
    #
    # ⚠️ A MISSING `member_terms` TABLE IS NOT AN ERROR. It does not exist until
    # m017 has run, and it is EMPTY until `scripts/load_member_terms.py` has run.
    # Both cases leave `terms` empty for every member, and `_covers` fails OPEN, so
    # matching behaves exactly as it did before this change rather than rejecting
    # every filer. The fix is inert until its data is present, by design.
    terms: dict[str, list[tuple[str, str]]] = {}
    try:
        for t in conn.execute(
            "SELECT bioguide_id, term_start, term_end FROM member_terms"
        ):
            terms.setdefault(t["bioguide_id"], []).append(
                (t["term_start"], t["term_end"])
            )
    except sqlite3.OperationalError:
        pass

    members: list[dict[str, Any]] = []

    for row in rows:
        full_name = str(row["full_name"] or "").strip()
        if not full_name:
            continue

        last_tokens, first_tokens = split_member_name(full_name)
        members.append(
            {
                "bioguide_id": row["bioguide_id"],
                "full_name": full_name,
                "normalized_name": normalize_name(full_name),
                "last_tokens": last_tokens,
                "first_tokens": first_tokens,
                "state": row["state"],
                "chamber": row["chamber"],
                "terms": terms.get(row["bioguide_id"], []),
            }
        )

    return members


# A PTR filed shortly AFTER a member leaves office is normal, not a mistake: the
# disclosure deadline runs 30-45 days from the transaction, so a departing member's
# final filings legitimately land after their term ends. Measured over the whole
# 866-filing corpus, exactly 6 filings fall outside their member's terms and they
# separate by a factor of 71:
#
#     3 / 24 / 108 days   -> Waltz, Manning x2   LEGITIMATE final PTRs
#     7,687 / 19,518 / 19,543 days  ->  the namesake errors (21 and 53 years)
#
# So this is not a tuned threshold sitting near the data; anything from ~120 days
# to ~20 years separates the two classes identically. 180 is chosen as a round
# number comfortably inside that gap. ⚠️ It is deliberately NOT applied before a
# term starts — there is no legitimate reason to file before taking office, and a
# symmetric window would re-admit a predecessor namesake.
TERM_GRACE_DAYS = 180


def _covers(member: dict[str, Any], filing_date: str | None) -> bool:
    """Did this member hold office on `filing_date` (plus the post-term grace)?

    ⚠️ FAILS OPEN, DELIBERATELY, IN EXACTLY TWO CASES: no filing date, or no term
    data for this member. Both mean "this test cannot be applied", and a test that
    cannot be applied must not be allowed to reject a filer — that would convert a
    missing-data problem into mass unmatching, which is a worse failure than the
    one being fixed. It fails CLOSED only when it has both facts and they disagree.

    Dates are ISO `YYYY-MM-DD` on both sides (the feed's format, and
    `parse_filing_date`'s output), so a lexicographic compare is a date compare.
    Verified across all 12,768 feed records: every term boundary matches
    ^\\d{4}-\\d{2}-\\d{2}$. The grace window needs real date arithmetic, so that
    one comparison parses.
    """
    if not filing_date:
        return True
    spans = member.get("terms") or []
    if not spans:
        return True
    day = str(filing_date)[:10]
    if any(start <= day <= end for start, end in spans):
        return True
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        # Unparseable date: the test cannot be applied, so it must not reject.
        return True
    for _start, end in spans:
        try:
            e = datetime.strptime(end[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if 0 < (d - e).days <= TERM_GRACE_DAYS:
            return True
    return False


def _difflib_match(
    first_name: str | None,
    last_name: str | None,
    members: list[dict[str, Any]],
    filing_date: str | None = None,
) -> str | None:
    """Original fuzzy fallback — preserved so previously-matching filers never
    regress when the deterministic anchor doesn't yield a unique candidate.

    ⚠️ THE CANDIDATE POOL IS DATE-FILTERED FIRST, and that is the point. This
    function resolves by string similarity, which systematically prefers the
    shorter, less specific name — i.e. the historical namesake over the serving
    member with a middle initial. Filtering the pool by service period before
    scoring removes the dead members from consideration entirely, rather than
    scoring them and hoping they lose.
    """
    first = normalize_name(first_name)
    last = normalize_name(last_name)

    if not last:
        return None

    eligible = [m for m in members if _covers(m, filing_date)]
    if not eligible:
        return None
    members = eligible

    target_variants = []

    if first and last:
        target_variants.extend([f"{first} {last}", f"{last} {first}"])

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


def match_member_id(
    first_name: str | None,
    last_name: str | None,
    members: list[dict[str, Any]],
    filing_date: str | None = None,
) -> str | None:
    """Deterministic anchor match with credential/compound-surname handling.

    A member is a candidate when the filer's surname token is a subset of the
    member's surname tokens (handles 'Delaney' ⊂ 'McClain Delaney') AND the
    filer's first given token equals the member's first given token. We accept
    ONLY when exactly one member qualifies — 0 candidates (roster gap, e.g.
    Linda T. Sanchez) or ≥2 fall through to the difflib fallback so nothing that
    matched before regresses.

    ── 🔴 WHY `filing_date` EXISTS, AND WHY THE FILTER IS *HERE* ───────────────
    The namesake defect was NOT that the anchor path picked the wrong member. It
    was that the anchor path found TWO candidates and gave up:

        ('Nicholas','Begich') -> B000315 "Begich, Nicholas"     (left office 1973)
                                 B001323 "Begich, Nicholas J."  (serving, actually filed)

    Two candidates means "ambiguous", which fell through to `_difflib_match`,
    which resolves by string similarity — and "Begich, Nicholas" is a closer
    string to "Nicholas Begich" than "Begich, Nicholas J." is. **The dead member
    won BECAUSE his name is less specific**, and serving members are the ones who
    carry a middle initial. Same mechanism produced ('Mark','Green') -> G000545
    (left 2007) over G000590. It is a systematic bias toward the historical
    namesake, not bad luck.

    So the date filter is applied to the CANDIDATE SET BEFORE the uniqueness
    test. That is what converts "2 candidates, guess by string" into "1 candidate,
    decided by fact". Bolting a date check onto the return value would not fix it:
    by then the wrong answer has already been chosen.

    ⚠️ AND THE DIFFLIB RESULT IS DATE-CHECKED TOO. Otherwise the fallback stays a
    hole straight back to the same bug for every filer the anchor path can't
    resolve — which is how ('Michael','Collins') is matched today.

    ⚠️ AMBIGUITY IS NOT RESOLVED, IT IS REPORTED. If two candidates both held
    office on the filing date, this returns None rather than picking one. None
    flows to `record_unmatched_filers`, so it surfaces to ROSTER_CHECK for a human
    instead of being silently decided by string distance — which is the entire
    failure mode above.

    Backwards compatible: `filing_date=None` (and an unloaded `member_terms`)
    reproduce the previous behaviour exactly.
    """
    filer_first = name_tokens(first_name)
    filer_last = name_tokens(last_name)

    if not filer_last:
        return _difflib_match(first_name, last_name, members, filing_date)

    filer_last_anchor = filer_last[-1]
    filer_first_anchor = filer_first[0] if filer_first else None

    candidates = []
    for member in members:
        if filer_last_anchor not in member["last_tokens"]:
            continue
        m_first = member["first_tokens"]
        if filer_first_anchor and m_first and filer_first_anchor != m_first[0]:
            continue
        candidates.append(member)

    if len(candidates) == 1:
        # Single anchor candidate: still date-checked. A lone candidate who was
        # out of office on the filing date is wrong even without a rival — that
        # is the Gallagher case (one 'James Gallagher' in the roster today, and
        # it is the wrong one, who resigned in 2024).
        return candidates[0]["bioguide_id"] if _covers(candidates[0], filing_date) else None

    if len(candidates) > 1:
        # ⚠️ THE DATE TEST MUST HAVE BEEN ABLE TO DISCRIMINATE BEFORE ITS RESULT
        # MEANS ANYTHING. Without this guard, an unloaded `member_terms` makes
        # every candidate "covered", len(dated) stays > 1, and the ambiguity rule
        # returns None — turning "this test could not be applied" into "this is
        # ambiguous" and unmatching every namesake pair in the corpus. That is a
        # worse failure than the bug being fixed, and it is precisely what the
        # fail-open contract in `_covers` exists to prevent; the contract has to
        # hold HERE too, not only inside `_covers`. Caught by
        # `test_fails_open_when_term_data_is_absent`.
        if filing_date and any(m.get("terms") for m in candidates):
            dated = [m for m in candidates if _covers(m, filing_date)]
            if len(dated) == 1:
                return dated[0]["bioguide_id"]
            if len(dated) > 1:
                return None      # genuine ambiguity — flag, never guess
            # len(dated) == 0: nobody with this name held office then. Fall through
            # to difflib, which is itself date-filtered, so it cannot resurrect them.

    return _difflib_match(first_name, last_name, members, filing_date)


def iter_xml_entries(xml_bytes: bytes) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise IngestError(f"could not parse House XML: {exc}") from exc

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

        member_id = match_member_id(first_name, last_name, members, filing_date)

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
        # 🔴 `COALESCE(?, member_id)` — A NULL MATCH MUST NEVER ERASE A GOOD ONE.
        # This UPDATE re-derives `member_id` on EVERY existing filing on EVERY run
        # (6-hourly), which is what silently reverted the 2026-08-15 identity
        # correction: the data was fixed, this line put the old answer back.
        #
        # Now that the matcher can legitimately return None (ambiguous, or nobody
        # of that name held office on the filing date), an unconditional write
        # would turn that into a NULL over a currently-correct `member_id` —
        # converting the fix into a new data-loss path of exactly the same shape.
        # A match is allowed to CORRECT an attribution, never to blank one.
        # Genuinely-unmatched filers are still surfaced, via
        # `record_unmatched_filers` + ROSTER_CHECK, which is the honest channel
        # for "we don't know" — silently nulling the column is not.
        conn.execute(
            """
            UPDATE filings
            SET member_id = COALESCE(?, member_id),
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


def ensure_unmatched_filers_table(conn) -> None:
    """Persist the raw names of filers we couldn't match (the raw name is not
    otherwise stored anywhere), so ROSTER_CHECK can spot a name recurring across
    ingestions — that's how we catch the next Sanchez without a manual sweep."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS unmatched_filers (
            norm_name   TEXT PRIMARY KEY,
            first_name  TEXT,
            last_name   TEXT,
            occurrences INTEGER DEFAULT 0,
            doc_ids     TEXT,
            first_seen  TEXT,
            last_seen   TEXT
        );
        """
    )
    conn.commit()


def record_unmatched_filers(conn, filings: list[HouseFiling]) -> None:
    """Upsert each still-unmatched filer name (deduped per doc_id)."""
    ensure_unmatched_filers_table(conn)
    for f in filings:
        if f.member_id is not None:
            continue
        norm = normalize_name(f"{f.first_name or ''} {f.last_name or ''}")
        if not norm:
            continue
        row = conn.execute(
            "SELECT doc_ids FROM unmatched_filers WHERE norm_name=?", (norm,)
        ).fetchone()
        if row:
            docs = set((row["doc_ids"] or "").split(",")) - {""}
            if f.doc_id in docs:
                continue  # already counted this filing
            docs.add(f.doc_id)
            conn.execute(
                "UPDATE unmatched_filers SET occurrences=?, doc_ids=?, last_seen=datetime('now') "
                "WHERE norm_name=?",
                (len(docs), ",".join(sorted(docs)), norm),
            )
        else:
            conn.execute(
                "INSERT INTO unmatched_filers (norm_name, first_name, last_name, "
                "occurrences, doc_ids, first_seen, last_seen) "
                "VALUES (?,?,?,1,?,datetime('now'),datetime('now'))",
                (norm, f.first_name, f.last_name, f.doc_id),
            )
    conn.commit()


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
            record_unmatched_filers(conn, filings)
            unmatched = sum(1 for f in filings if f.member_id is None)
            matched = registered_count - unmatched

            details = (
                f"{len(entries)} entries scanned, {registered_count} PTRs found, "
                f"{new_count} new filings inserted, matched={matched}, unmatched={unmatched}"
            )

            finish_ingestion_run(conn, run_id, len(entries), 0, details)
            print(details)

            # Activity log — so a future stall is visible here, not only via a
            # diagnostic sweep. scanned=index entries, flagged=PTRs, emitted=new.
            record_activity("INGEST_HOUSE_INDEX", scanned=len(entries),
                            flagged=registered_count, emitted=new_count,
                            duration_seconds=round(_time.time() - _t0, 2), notes=details)

        except Exception as exc:
            level = "ERROR" if isinstance(exc, IngestError) else "CRITICAL"
            details = f"{level}: House index ingest failed (year {year}) — {exc}"
            # Note when this failure follows a prior failure — a persistent break
            # reads differently from one flaky run.
            try:
                prev = conn.execute(
                    "SELECT notes FROM activity_log WHERE source='INGEST_HOUSE_INDEX' "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if prev and (prev["notes"] or "").startswith(("ERROR", "CRITICAL")):
                    details += " [consecutive failure — prior run also failed]"
            except Exception:
                pass
            finish_ingestion_run(conn, run_id, 0, 1, details)
            record_activity("INGEST_HOUSE_INDEX", scanned=0, flagged=0, emitted=0,
                            duration_seconds=round(_time.time() - _t0, 2), notes=details)
            print(details)
            sys.exit(1)


if __name__ == "__main__":
    main()