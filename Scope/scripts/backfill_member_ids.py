#!/usr/bin/env python3
"""
backfill_member_ids.py — one-time backfill for House filer member matching.

The `filings` table does not persist the raw filer name (it's matched at ingest
time and discarded), so this backfill re-downloads the House FD.zip indexes,
recovers doc_id -> (first, last), re-runs the improved deterministic matcher
(ingest_house_index.match_member_id) against currently-unmatched house filings,
and syncs the result down to transactions.

It also repairs the legacy `member_id='None'` string bug (str(None) written by
an older parse path) by resyncing transactions.member_id from their filing.

Read-only-safe to dry-run: pass no --commit and it reports without writing.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import zipfile
import xml.etree.ElementTree as ET

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ingest_house_index as ih
from jpt_common import db_connection

UA = "Scope Congressional Trade Tracker/0.1"
ZIP_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"


def _strip(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def build_doc_name_map(years: list[int]) -> dict[str, tuple[str, str]]:
    """doc_id -> (first, last) for every PTR entry in the given years' indexes."""
    out: dict[str, tuple[str, str]] = {}
    for year in years:
        url = ZIP_URL.format(year=year)
        print(f"Downloading {url}")
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            xn = [n for n in zf.namelist() if n.lower().endswith(".xml")][0]
            root = ET.fromstring(zf.read(xn))
        for node in root.iter():
            if not list(node):
                continue
            d = {_strip(ch.tag): (ch.text or "").strip() for ch in node if (ch.text or "").strip()}
            doc = d.get("DocID") or d.get("DocumentID") or d.get("DocId")
            if doc and d.get("FilingType") == "P":
                out[str(doc).strip()] = (d.get("First", ""), d.get("Last", ""))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill House member_ids.")
    ap.add_argument("--commit", action="store_true", help="Write changes (else dry-run).")
    args = ap.parse_args()

    conn = db_connection()
    conn.row_factory = __import__("sqlite3").Row
    members = ih.load_members(conn)

    # Currently-unmatched house filings.
    null_filings = conn.execute(
        "SELECT id, doc_id FROM filings WHERE source='house' AND member_id IS NULL"
    ).fetchall()
    print(f"House filings with NULL member_id: {len(null_filings)}")

    # Recover names from the years those filings actually span.
    filing_years = conn.execute(
        "SELECT DISTINCT CAST(strftime('%Y', filing_date) AS INT) y "
        "FROM filings WHERE source='house' AND member_id IS NULL AND filing_date IS NOT NULL"
    ).fetchall()
    years = sorted({r["y"] for r in filing_years if r["y"]})
    print(f"Recovering names from index years: {years}")
    name_map = build_doc_name_map(years)

    matched, still_null, no_name = 0, [], []
    updates: list[tuple[str, int]] = []
    for r in null_filings:
        doc = str(r["doc_id"]).strip()
        nm = name_map.get(doc)
        if not nm:
            no_name.append(doc)
            continue
        first, last = nm
        mid = ih.match_member_id(first, last, members)
        if mid:
            matched += 1
            updates.append((mid, r["id"]))
        else:
            still_null.append((doc, first, last))

    print(f"\nMatched now: {matched}")
    print(f"Still unmatched (roster gap / ambiguous): {len(still_null)}")
    for doc, f, l in still_null:
        print(f"  STILL NULL: doc={doc}  {f} {l}")
    if no_name:
        print(f"No name recovered for {len(no_name)} doc_ids: {no_name}")

    # 'None' string diagnostic.
    none_str = conn.execute(
        "SELECT COUNT(*) n FROM transactions WHERE member_id='None'"
    ).fetchone()["n"]
    print(f"\ntransactions with literal 'None' string: {none_str}")

    if not args.commit:
        print("\n[dry-run] no changes written. Re-run with --commit to apply.")
        conn.close()
        return

    for mid, fid in updates:
        conn.execute("UPDATE filings SET member_id=? WHERE id=?", (mid, fid))
    # Resync transactions from their filing for ALL house filings — this both
    # applies the new matches and rewrites the 'None' string to real NULL/id.
    conn.execute(
        """
        UPDATE transactions
        SET member_id = (SELECT f.member_id FROM filings f WHERE f.id = transactions.filing_id)
        WHERE filing_id IN (SELECT id FROM filings WHERE source='house')
        """
    )
    conn.commit()

    post_null = conn.execute("SELECT COUNT(*) n FROM transactions WHERE member_id IS NULL").fetchone()["n"]
    post_none = conn.execute("SELECT COUNT(*) n FROM transactions WHERE member_id='None'").fetchone()["n"]
    print(f"\n[committed] filings updated: {len(updates)}")
    print(f"[committed] transactions now NULL: {post_null}   literal 'None': {post_none}")
    conn.close()


if __name__ == "__main__":
    main()
