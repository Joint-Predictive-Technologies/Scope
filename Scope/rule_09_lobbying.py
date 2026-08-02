#!/usr/bin/env python3
"""
rule_09_lobbying.py

Detects companies whose quarterly lobbying spend has spiked significantly
year-over-year using the Senate LDA (Lobbying Disclosure Act) API.
"""

from __future__ import annotations

import argparse
import re
import time
from collections import defaultdict
from datetime import date
from typing import NamedTuple

import requests

from jpt_common import CONTRACTOR_OVERRIDES, db_connection


RULE = "RULE_09"
LDA_API = "https://lda.senate.gov/api/v1/filings/"
HEADERS = {"User-Agent": "Scope/0.1 sloppysecondstbb@gmail.com"}
SLEEP = 0.3

QUARTERLY_TYPES = {"Q1", "Q2", "Q3", "Q4"}

# Historical quarter pairs to compare when current quarter has sparse data.
# Format: (current_filing_type, current_year, prior_filing_type, prior_year)
COMPARE_PERIODS = [
    ("Q2", 2026, "Q2", 2025),
    ("Q1", 2026, "Q1", 2025),
    ("Q4", 2025, "Q4", 2024),
    ("Q3", 2025, "Q3", 2024),
]

# Trigger thresholds
MIN_SPEND = 50_000.0
MIN_YOY_PCT = 50.0

# Severity thresholds
HIGH_PCT = 100.0
HIGH_SPEND = 500_000.0

# ⚠️ REMOVED: `TICKER_CUTOFF = 0.7`, the difflib similarity cutoff. It is gone rather than
# retuned on purpose — no threshold makes name similarity a sound basis for identity. See
# `match_ticker`.


def current_quarter() -> tuple[str, int]:
    """Return (filing_type, year) for the current calendar quarter."""
    today = date.today()
    q = (today.month - 1) // 3 + 1
    return f"Q{q}", today.year


# ---------------------------------------------------------------------------
# LDA API pagination
# ---------------------------------------------------------------------------

class FilingRecord(NamedTuple):
    client_name: str
    registrant_name: str
    spend: float
    issue_codes: list[str]
    issue_descs: list[str]
    filing_type: str
    filing_year: int


def _spend(filing: dict) -> float:
    for field in ("income", "expenses"):
        val = filing.get(field)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    return 0.0


def fetch_filings(filing_type: str, filing_year: int) -> list[FilingRecord]:
    records: list[FilingRecord] = []
    url: str | None = (
        f"{LDA_API}?filing_type={filing_type}&filing_year={filing_year}"
        "&format=json&page_size=100"
    )

    while url:
        time.sleep(SLEEP)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException:
            break

        if r.status_code == 429:
            time.sleep(10)
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
            except requests.RequestException:
                break

        if r.status_code != 200:
            break

        data = r.json()
        for filing in data.get("results", []):
            client = filing.get("client") or {}
            registrant = filing.get("registrant") or {}
            activities = filing.get("lobbying_activities") or []

            codes: list[str] = []
            descs: list[str] = []
            seen_codes: set[str] = set()
            for act in activities:
                code = act.get("general_issue_code_display") or ""
                desc = (act.get("description") or "").strip()
                if code and code not in seen_codes:
                    codes.append(code)
                    seen_codes.add(code)
                if desc:
                    descs.append(desc[:80])

            records.append(FilingRecord(
                client_name=(client.get("name") or "").strip(),
                registrant_name=(registrant.get("name") or "").strip(),
                spend=_spend(filing),
                issue_codes=codes,
                issue_descs=descs,
                filing_type=filing_type,
                filing_year=filing_year,
            ))

        url = data.get("next") or None

    return records


# ---------------------------------------------------------------------------
# Ticker fuzzy match
# ---------------------------------------------------------------------------

def load_ticker_names(conn) -> list[tuple[str, str]]:
    """Return [(symbol, company_name), ...] from the tickers table."""
    rows = conn.execute("SELECT symbol, company_name FROM tickers").fetchall()
    return [(row["symbol"], (row["company_name"] or "").upper()) for row in rows]


# Legal-form suffixes stripped before comparison, and SEC's `/DE/` state-of-incorporation
# marker. The marker matters on its own: `tickers.company_name` for HXL is
# "HEXCEL CORP /DE/", which is why an otherwise-exact "HEXCEL CORPORATION" did not match.
_LEGAL_SUFFIX = (
    r"(?:\s+(?:INC|INCORPORATED|CORP|CORPORATION|COMPANY|CO|LLC|LLP|LP|LTD|LIMITED"
    r"|PLC|PBC|SA|NV|AG|SE|AB|ASA|OYJ|HOLDINGS?|GROUP))+$"
)


def normalize_company(name: str) -> str:
    """Canonical form for company-name equality. Deterministic; no similarity anywhere."""
    s = (name or "").upper()
    s = re.sub(r"/[A-Z]{2}/", " ", s)          # SEC state marker: "HEXCEL CORP /DE/"
    s = s.replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^THE\s+", "", s)
    previous = None
    while previous != s:                       # "FOO CORP INC" -> "FOO"
        previous = s
        s = re.sub(_LEGAL_SUFFIX, "", s).strip()
    return s


# Tokens that carry no identity: legal forms, corporate scaffolding, and the LDA's own
# renaming noise ("FKA" = formerly known as). A curated key is accepted only when everything
# ELSE in the client's name is one of these — see `_curated_symbol`.
_NOISE_TOKENS = frozenset({
    "INC", "INCORPORATED", "CORP", "CORPORATION", "COMPANY", "CO", "LLC", "LLP", "LP",
    "LTD", "LIMITED", "PLC", "PBC", "SA", "NV", "AG", "SE", "AB", "ASA", "OYJ",
    "THE", "AND", "OF", "AFFILIATES", "AFFILIATE", "SUBSIDIARIES", "FKA", "FNA",
    "FORMERLY", "KNOWN", "AS", "GROUP", "HOLDINGS", "HOLDING", "US", "USA",
})


def _is_token_run(haystack: list[str], needle: list[str]) -> bool:
    """Do `needle`'s tokens appear as a contiguous run in `haystack`? Token-level only.

    This is what stops `caci` matching CACIQUE and `boeing` matching BOEINGTON — a
    character-level `in` test matched both.
    """
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[i:i + len(needle)] == needle
               for i in range(len(haystack) - len(needle) + 1))


def _curated_symbol(normalized_name: str) -> str | None:
    """Curated-table lookup. Longest key wins; ANY leftover identity token refuses.

    ⚠️ THE LEFTOVER RULE IS THE WHOLE POINT, AND THE FIRST VERSION OF THIS FIX LACKED IT.
    A plain containment test resolved `BOEING EMPLOYEES' CREDIT UNION` to $BA. BECU is a
    member-owned credit union, not Boeing and not listed — and it is a REAL client in this
    corpus with real filings (2024 Q1 $20K -> 2025 Q1 $40K, +100% YoY). It clears
    `MIN_YOY_PCT` and is held back from emitting only by `MIN_SPEND`, i.e. by a spend floor
    rather than by anything about attribution. Found by a verification pass.

    So a curated key is accepted only when it accounts for every identity-bearing token in
    the name. "RTX CORPORATION AND AFFILIATES" resolves (leftovers are all scaffolding);
    "BOEING EMPLOYEES CREDIT UNION" does not (EMPLOYEES / CREDIT / UNION are identity).

    This is set containment over a 45-key CURATED table with an enumerated stopword list —
    deterministic, no score, no threshold. It is deliberately NOT the shape of
    `resolve_contractor`'s tier 2, which runs the same idea against all 10,619 rows of
    `tickers`, where any row can collide by accident.
    """
    tokens = normalized_name.split()
    if not tokens:
        return None
    best_length = 0
    best_symbol: str | None = None
    for key, value in CONTRACTOR_OVERRIDES.items():
        key_tokens = normalize_company(key).split()
        if not _is_token_run(tokens, key_tokens):
            continue
        leftover = [t for t in tokens
                    if t not in key_tokens and t not in _NOISE_TOKENS]
        if leftover:
            continue
        if len(key_tokens) > best_length:
            best_length, best_symbol = len(key_tokens), value[0]
    return best_symbol


def match_ticker(client_name: str, ticker_names: list[tuple[str, str]]) -> str | None:
    """Resolve a lobbying client to a ticker AUTHORITATIVELY, or return None.

    ⚠️ THIS USED TO BE `difflib.get_close_matches(..., cutoff=0.7)` AND IT WAS WRONG ON
    ROUGHLY A THIRD TO A HALF OF THE ROWS IT ATTRIBUTED. Every reasonable ground truth lands
    in a band — 31.0% to 45.8% of the 216 stored alerts, depending on how generic a token has
    to be to count as identity — and the true rate is higher still, because sharing a token
    does not make an attribution right (`COHERUS BIOSCIENCES` -> `$RCUS` is *Arcus*
    Biosciences). Authority can confirm only 35.6%. Measured examples, all live in the DB:

        IBM CORPORATION        -> $VIRC   (VIRCO MFG CORPORATION)
        HEXCEL CORPORATION     -> $HBIA   (HILLS BANCORPORATION)
        RELX INC               -> $ARDX   (ARDELYX, INC.)
        WIKIMEDIA FOUNDATION   -> $IVDA   (Iveda Solutions, Inc.)
        SAMSUNG SDI AMERICA    -> $LMFA   (LM FUNDING AMERICA, INC.)

    RULE_09 is corroboration-eligible, so a fabricated ticker is not cosmetic — it can build a
    FALSE convergence on the wrong company. This is the same defect class RULE_11 had
    (`resolve_contractor` + migrations m003/m004); RULE_09 had never been repaired.

    ⛔ THE BAR IS AUTHORITATIVE, AND THERE IS DELIBERATELY NO FALLBACK. A name that does not
    resolve returns None, and the caller already renders that honestly — `ticker_label` falls
    back to the client's own name. A fabricated-but-plausible ticker is worse than a blank.

    ⛔ AND THE TOKEN-CONTAINMENT TIER OF `resolve_contractor` IS DELIBERATELY NOT REUSED HERE.
    That tier resolves on PARTIAL token containment at a hardcoded conf=90, so a name that
    merely contains a company's distinctive tokens is attributed to it — and the confidence
    number cannot tell that apart from a curated match. Swapping one similarity heuristic for
    another would not be a fix. Only the CURATED tier is used.

    Two tiers, both exact:
      1. `jpt_common.CONTRACTOR_OVERRIDES` — curated; longest normalized key wins.
      2. Unique normalized-name equality against `tickers.company_name`.
    Ambiguity refuses: 1541 normalized names map to 2+ symbols in the live tickers table
    (GOOG/GOOGL, BRK-A/BRK-B, ASML/ASMLF), and picking one would be a guess.
    """
    normalized = normalize_company(client_name)
    if not normalized:
        return None

    curated = _curated_symbol(normalized)
    if curated:
        return curated

    # 2. Exact normalized equality, and only when it is UNIQUE.
    candidates = {symbol for symbol, company in ticker_names
                  if normalize_company(company) == normalized}
    if len(candidates) == 1:
        return next(iter(candidates))

    # 3. There is no step 3. An unresolved client gets NO ticker.
    return None


# ---------------------------------------------------------------------------
# Group + compare
# ---------------------------------------------------------------------------

def _group_key(rec: FilingRecord) -> tuple[str, str]:
    return (rec.client_name, rec.registrant_name)


def find_spikes(
    current: list[FilingRecord],
    prior: list[FilingRecord],
) -> list[dict]:
    prior_idx: dict[tuple[str, str], float] = defaultdict(float)
    for rec in prior:
        prior_idx[_group_key(rec)] += rec.spend

    current_agg: dict[tuple[str, str], dict] = {}
    for rec in current:
        key = _group_key(rec)
        if key not in current_agg:
            current_agg[key] = {
                "client_name": rec.client_name,
                "registrant_name": rec.registrant_name,
                "spend": 0.0,
                "issue_codes": [],
                "issue_descs": [],
                "filing_type": rec.filing_type,
                "filing_year": rec.filing_year,
            }
        entry = current_agg[key]
        entry["spend"] += rec.spend
        for code in rec.issue_codes:
            if code not in entry["issue_codes"]:
                entry["issue_codes"].append(code)
        entry["issue_descs"].extend(rec.issue_descs)

    spikes: list[dict] = []
    for key, entry in current_agg.items():
        prior_spend = prior_idx.get(key, 0.0)
        curr_spend = entry["spend"]

        if curr_spend < MIN_SPEND:
            continue
        if prior_spend <= 0:
            continue

        pct = (curr_spend - prior_spend) / prior_spend * 100.0
        if pct < MIN_YOY_PCT:
            continue

        spikes.append({**entry, "prior_spend": prior_spend, "pct_change": pct})

    spikes.sort(key=lambda x: x["pct_change"], reverse=True)
    return spikes


# ---------------------------------------------------------------------------
# Alert helpers
# ---------------------------------------------------------------------------

def alert_exists(conn, headline: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE rule = ?
          AND headline = ?
          AND datetime(created_at) >= datetime('now', '-90 days')
        LIMIT 1
        """,
        (RULE, headline),
    ).fetchone()
    return row is not None


def insert_alert(conn, headline: str, ticker: str | None, severity: str, tags: str) -> None:
    conn.execute(
        "INSERT INTO alerts (rule, headline, severity, tags, ticker) VALUES (?,?,?,?,?)",
        (RULE, headline, severity, tags, ticker),
    )
    conn.commit()


def _fmt_spend(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    return f"${v / 1_000:.0f}K"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    filing_type: str,
    filing_year: int,
    emit_alerts: bool,
    prior_type: str | None = None,
    prior_year: int | None = None,
) -> tuple[int, int]:
    if prior_type is None:
        prior_type = filing_type
    if prior_year is None:
        prior_year = filing_year - 1

    print(f"Fetching {filing_type} {filing_year} …")
    current = fetch_filings(filing_type, filing_year)
    print(f"  {len(current)} current filings")

    print(f"Fetching {prior_type} {prior_year} …")
    prior = fetch_filings(prior_type, prior_year)
    print(f"  {len(prior)} prior-year filings")

    spikes = find_spikes(current, prior)
    print(f"  {len(spikes)} spend spike(s) found")

    if not spikes:
        return 0, 0

    conn = db_connection()
    ticker_names = load_ticker_names(conn)

    triggered = len(spikes)
    emitted = 0

    for spike in spikes:
        client = spike["client_name"]
        registrant = spike["registrant_name"]
        curr_spend = spike["spend"]
        prior_spend = spike["prior_spend"]
        pct = spike["pct_change"]
        codes = spike["issue_codes"]

        ticker = match_ticker(client, ticker_names)
        ticker_label = f"${ticker}" if ticker else client[:30]

        issues_str = ", ".join(codes[:4]) if codes else "General"
        headline = (
            f"Lobbying spike: {ticker_label} spend up {pct:.0f}% YoY "
            f"({_fmt_spend(prior_spend)} → {_fmt_spend(curr_spend)}) "
            f"— issues: {issues_str}"
        )

        severity = (
            "HIGH"
            if pct >= HIGH_PCT or curr_spend >= HIGH_SPEND
            else "MEDIUM"
        )

        tags = ", ".join(filter(None, [
            client,
            registrant,
            issues_str,
            f"{_fmt_spend(prior_spend)}→{_fmt_spend(curr_spend)}",
        ]))

        print(
            f"  [{severity}] {pct:.0f}% YoY  {_fmt_spend(prior_spend)} → {_fmt_spend(curr_spend)}"
            f"  {client[:50]}"
            + (f"  → {ticker}" if ticker else "")
        )

        if emit_alerts:
            if not alert_exists(conn, headline):
                insert_alert(conn, headline, ticker, severity, tags)
                emitted += 1

    conn.close()
    return triggered, emitted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    default_type, default_year = current_quarter()
    parser = argparse.ArgumentParser(
        description="Detect lobbying spend spikes via Senate LDA API (RULE_09)."
    )
    parser.add_argument(
        "--filing-type",
        default=default_type,
        choices=list(QUARTERLY_TYPES),
        help=f"Quarterly filing type. Default: {default_type} (current quarter).",
    )
    parser.add_argument(
        "--filing-year",
        type=int,
        default=default_year,
        help=f"Filing year. Default: {default_year}.",
    )
    parser.add_argument(
        "--emit-alerts",
        action="store_true",
        help="Insert triggered filings into the alerts table.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    _t0 = time.time()
    if args.filing_type == current_quarter()[0] and args.filing_year == current_quarter()[1]:
        # Default run: sweep all COMPARE_PERIODS so historical data always shows
        total_triggered = total_emitted = 0
        for cur_type, cur_year, prior_type, prior_year in COMPARE_PERIODS:
            print(f"\n── {cur_type} {cur_year} vs {prior_type} {prior_year} ──")
            t, e = run(cur_type, cur_year, args.emit_alerts, prior_type, prior_year)
            total_triggered += t
            total_emitted += e
        print(f"\n{total_triggered} spike(s) detected, {total_emitted} alert(s) emitted")
    else:
        total_triggered, total_emitted = run(args.filing_type, args.filing_year, args.emit_alerts)
        print(f"\n{total_triggered} spike(s) detected, {total_emitted} alert(s) emitted")

    from jpt_common import record_activity
    record_activity("RULE_09", scanned=total_triggered, flagged=total_triggered,
                    emitted=total_emitted, duration_seconds=round(time.time() - _t0, 2))


if __name__ == "__main__":
    main()
