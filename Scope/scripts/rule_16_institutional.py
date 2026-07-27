#!/usr/bin/env python3
"""
RULE_16 — Institutional / whale accumulation (SEC Form 13F-HR)

Source: SEC EDGAR (free, no key; declarative User-Agent required, 10 req/s fair access)
CUSIP -> ticker: OpenFIGI v3 /mapping (free, NO API KEY, 25 requests/minute)

Detects a tracked institution DISCLOSING a new position or a significant increase,
and emits one single-symbol alert per (filer, ticker).

Feasibility was gated first — see 02_Sessions/SESSION-2026-07-27-rule-13f-institutional.md.
The four findings that shaped this file:

1. **CUSIP is a unique security identifier, so mapping is a LOOKUP, not a guess.**
   Measured on 140 CUSIPs stride-sampled from 3,612 real holdings: 93.6% resolve,
   84.3% to single-name listed equities, false-match ~0. Issuer-NAME fuzzy matching
   was measured too and REJECTED: 7-10% of its matches are wrong, and wrong in the
   dangerous way — DIME CMNTY BANCSHARES -> EQBK when the CUSIP says DCOM. That is
   the same failure that put Bank of America filings under Boeing in RULE_15 and
   Intellectual Ventures under Intel in RULE_14. **Never map by name here.**

2. **13F rows must be AGGREGATED BY CUSIP.** An information table is split per
   manager and per discretion: Berkshire's latest is 90 rows for 29 holdings.
   Summing without aggregating triple-counts a position.

3. **Quant funds would flood the gate.** Berkshire holds 29 names and Baupost 22;
   Bridgewater holds 993 and Renaissance 3,213. A 13F holding a third of the market
   is an index, not a conviction signal. MAX_HOLDINGS is a structural guard that
   works even if a quant filer is added to WHALES by mistake.

4. **Timing: the DISCLOSURE is the event.** A 13F is filed 45 days after quarter end,
   so the position is old — but the market learns of it on the filing date, which is
   a real dated event. (Contrast RULE_14, where a patent's grant date is examiner-queue
   noise.) We therefore emit only filings we have not seen before, keyed on
   `filingDate`, so `created_at` ~ filing + <1 day and no trailing window collapses
   into the cron instant. Headlines say "disclosed", never "is buying".

Run:  python scripts/rule_16_institutional.py --emit-alerts
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, insert_alert, normalize_ticker

RULE = "RULE_16"

SEC_UA = os.getenv("SEC_USER_AGENT",
                   "Scope Political Intelligence research@jointpredictive.com")
HEADERS = {"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
OPENFIGI = "https://api.openfigi.com/v3/mapping"

SEC_SLEEP = 0.35             # SEC fair access is 10 req/s; this is far inside it
FIGI_BATCH = 10              # OpenFIGI: max 10 jobs per request
FIGI_SLEEP = 2.6             # OpenFIGI: 25 requests/minute (verified from headers)
TIME_BUDGET_SECONDS = 240    # stop cleanly inside the scheduler's 300s kill

# --- tunable product choices (finding 3) -----------------------------------
# The human owns these three numbers. They are policy, not physics.
MAX_HOLDINGS = 150           # skip quant sweeps: Bridgewater 993, Renaissance 3,213
MIN_POSITION_USD = 5_000_000 # materiality floor; a rounding-error stake is not a leg
INCREASE_PCT = 50.0          # "significant increase" threshold vs the prior quarter
LOOKBACK_DAYS = 120          # only consider filings this recent (13F is quarterly)

# Concentrated, conviction-driven managers. TUNABLE PRODUCT CHOICE, not physics.
#
# Every CIK below was resolved from EDGAR's own company search filtered to 13F-HR
# filers, then confirmed by parsing that filer's latest information table and
# counting DISTINCT CUSIPs (never rows — Coatue files 198 rows for 62 holdings, so a
# row-based guard would wrongly drop it). Comment shows the confirmed count.
#
# Correctly excluded by MAX_HOLDINGS: Maverick Capital (239), Polen Capital (221),
# First Eagle (424) — index-like books, not conviction.
#
# FLAGGED FOR THE HUMAN, deliberately not guessed:
#   * Baupost — EDGAR search returns CIK 0001054420 ("BAUPOST GROUP LLC /ADV"), which
#     has no parseable 13F. 0001061768 is the entity that actually files and is used
#     here; confirm that is the intended one.
#   * Trian Fund Management (0001345472) — no parseable 13F found. OMITTED.
#   * Eminence Capital and Ruane Cunniff resolve to 13Fs with ONE holding, which is
#     almost certainly a partial or amended filing rather than the real book. OMITTED
#     rather than included on a number I do not believe.
#   * Abrams Capital and Jana Partners matched multiple CIKs; the largest-filer match
#     is used. Confirm.
WHALES: dict[str, str] = {
    "0001096343": "MARKEL GROUP INC.",                        # 129 holdings
    "0001103804": "VIKING GLOBAL INVESTORS LP",               #  77 holdings
    "0001536411": "Duquesne Family Office LLC",               #  68 holdings
    "0001135730": "COATUE MANAGEMENT LLC",                    #  62 holdings
    "0001167483": "TIGER GLOBAL MANAGEMENT LLC",              #  54 holdings
    "0000807985": "SOUTHEASTERN ASSET MANAGEMENT INC/TN/",    #  49 holdings
    "0001138995": "GLENVIEW CAPITAL MANAGEMENT, LLC",         #  43 holdings
    "0001079114": "GREENLIGHT CAPITAL INC",                   #  40 holdings
    "0001762304": "HHLR ADVISORS, LTD.",                      #  38 holdings
    "0001061165": "LONE PINE CAPITAL LLC",                    #  36 holdings
    "0001040273": "Third Point LLC",                          #  33 holdings
    "0001791786": "Elliott Investment Management L.P.",       #  32 holdings
    "0001656456": "Appaloosa LP",                             #  31 holdings
    "0001061768": "Baupost Group LLC",                        #  22 holdings
    "0001067983": "BERKSHIRE HATHAWAY INC",                   #  29 holdings
    "0001517857": "Soroban Capital Partners LP",              #  27 holdings
    "0001517137": "Starboard Value LP",                       #  25 holdings
    "0001112520": "AKRE CAPITAL MANAGEMENT LLC",              #  19 holdings
    "0001582090": "Sachem Head Capital Management LP",        #  19 holdings
    "0001418814": "ValueAct Holdings, L.P.",                  #  18 holdings
    "0001709323": "Himalaya Capital Management LLC",          #  14 holdings
    "0001541617": "Altimeter Capital Management, LP",         #  13 holdings
    "0000921669": "ICAHN CARL C",                             #  12 holdings
    "0001336528": "Pershing Square Capital Management, L.",   #  11 holdings
    "0001358706": "ABRAMS CAPITAL MANAGEMENT, L.P.",          #  11 holdings  # AMBIGUOUS: also matched 0001426355,0001165407
    "0001056831": "FAIRHOLME CAPITAL MANAGEMENT LLC",         #  10 holdings
    "0001159159": "JANA PARTNERS LLC",                        #   9 holdings  # AMBIGUOUS: also matched 0001998597
    "0001649339": "Scion Asset Management, LLC",              #   8 holdings
}

# Only these OpenFIGI security types become a leg. Funds/ETFs are excluded on
# purpose: a basket is not a conviction signal and drags the generic-ticker
# concentration problem into the gate.
ALLOWED_SECURITY_TYPES = {"Common Stock", "REIT"}

# --- the fenced CINS fallback -----------------------------------------------
# OpenFIGI resolves 0 of 316 CINS (letter-prefixed) CUSIPs — verified by census and
# again live on Chubb, Aon, Accenture, Willis Towers Watson. Those are top-ten
# positions in real conviction books, so the blind spot is material.
#
# The fallback is name matching, which is EXACTLY the technique that mapped Boeing's
# political score onto Bank of America filings in RULE_15 and Intellectual Ventures
# onto Intel in RULE_14. It is therefore FENCED on four sides:
#   1. CINS only        — never runs for a digit CUSIP, which OpenFIGI resolves at 98%
#   2. only on failure  — never runs when the CUSIP lookup succeeded
#   3. high cutoff 0.90 — well above RULE_09's 0.70, which produced real mis-maps
#   4. flagged forever  — the alert carries ticker_confidence="name-matched", and a
#                         test asserts a fallback ticker can never be presented as a lookup
# Below the cutoff it DROPS LOUDLY. A guessed ticker is never emitted.
CINS_PREFIX = re.compile(r"^[A-Z]")
CINS_FALLBACK_CUTOFF = 0.90
CONF_LOOKUP = "cusip-lookup"      # OpenFIGI, false-match ~0
CONF_NAME = "name-matched"        # CINS fallback, lower confidence, always flagged

DERIVATIVE_CLASS = re.compile(r"\b(PUT|CALL|WARRANT|NOTE|BOND|DBCV|CONV)\b", re.I)
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,5}$")


class SourceUnavailable(RuntimeError):
    """Upstream failed in a way that must be LOUD, never a silent zero."""


def _whales() -> dict[str, str]:
    return {k: v for k, v in WHALES.items() if v and k.isdigit()}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 45) -> bytes:
    """GET with a declarative UA. Uses `requests` like every other rule here.

    NOT urllib: SEC honours `Accept-Encoding: gzip` and urllib does not
    transparently decompress, so the first live run died on
    `UnicodeDecodeError: invalid start byte 0x8b` — a gzip magic number being
    read as text. `requests` handles content-encoding.
    """
    import requests
    time.sleep(SEC_SLEEP)
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        raise SourceUnavailable(f"{url} -> {type(e).__name__}") from e
    if r.status_code != 200:                 # loud, never a silent zero
        raise SourceUnavailable(f"{url} -> HTTP {r.status_code}")
    return r.content


# ---------------------------------------------------------------------------
# 13F retrieval + parsing
# ---------------------------------------------------------------------------

def recent_13f(cik: str, limit: int = 2) -> list[dict]:
    """The filer's most recent 13F-HR filings, newest first.

    Returns filingDate and reportDate for each: filingDate is when the market
    learned, reportDate is the quarter the holdings describe. We key emission on
    filingDate (finding 4) and record reportDate as provenance.
    """
    d = json.loads(_get(SUBMISSIONS.format(cik=cik.zfill(10))).decode())
    rec = d.get("filings", {}).get("recent", {})
    out = []
    for i, form in enumerate(rec.get("form", [])):
        if form != "13F-HR":
            continue
        out.append({
            "accession": rec["accessionNumber"][i],
            "filing_date": rec["filingDate"][i],
            "report_date": rec["reportDate"][i],
            "filer_name": d.get("name", ""),
        })
        if len(out) >= limit:
            break
    return out


def _first(elem, tag: str) -> str:
    for c in elem.iter():
        if c.tag.split("}")[-1] == tag:
            return (c.text or "").strip()
    return ""


def holdings(cik: str, accession: str) -> dict[str, dict]:
    """Parse a 13F information table, AGGREGATED BY CUSIP (finding 2).

    Returns {cusip: {issuer, class, value, shares}} with value/shares SUMMED across
    the manager-split rows. Without this, Berkshire's 90 rows would look like 90
    positions instead of 29.
    """
    accn = accession.replace("-", "")
    base = ARCHIVE.format(cik=int(cik), acc=accn)
    idx = json.loads(_get(f"{base}/index.json").decode())
    xmls = [it["name"] for it in idx.get("directory", {}).get("item", [])
            if it["name"].lower().endswith(".xml") and "primary_doc" not in it["name"].lower()]
    if not xmls:
        raise SourceUnavailable(f"no information table in {accession}")
    root = ET.fromstring(_get(f"{base}/{xmls[0]}").decode("utf-8", "replace"))

    agg: dict[str, dict] = {}
    for row in (e for e in root.iter() if e.tag.split("}")[-1] == "infoTable"):
        cusip = _first(row, "cusip").strip().upper()
        if not cusip:
            continue
        cls = _first(row, "titleOfClass")
        if DERIVATIVE_CLASS.search(cls or ""):
            continue                      # puts/calls/notes are not an equity position
        try:
            val = float(_first(row, "value") or 0)
            sh = float(_first(row, "sshPrnamt") or 0)
        except ValueError:
            continue
        a = agg.setdefault(cusip, {"issuer": _first(row, "nameOfIssuer"),
                                   "class": cls, "value": 0.0, "shares": 0.0})
        a["value"] += val
        a["shares"] += sh
    return agg


# ---------------------------------------------------------------------------
# CUSIP -> ticker (finding 1)
# ---------------------------------------------------------------------------

def map_cusips(cusips: list[str], budget_deadline: float | None = None) -> dict[str, dict]:
    """CUSIP -> {ticker, name, security_type} via OpenFIGI. Unresolved CUSIPs are
    simply ABSENT from the result — the caller drops them rather than guessing.

    A CUSIP identifies exactly one security, so this is a lookup. We never fall
    back to issuer-name matching: measured at 7-10% wrong, and wrong silently.
    """
    out: dict[str, dict] = {}
    uniq = sorted({c for c in cusips if c})
    for i in range(0, len(uniq), FIGI_BATCH):
        if budget_deadline and time.time() > budget_deadline:
            break
        batch = uniq[i:i + FIGI_BATCH]
        body = json.dumps([{"idType": "ID_CUSIP", "idValue": c} for c in batch]).encode()
        import requests
        try:
            r = requests.post(OPENFIGI, data=body, timeout=45,
                              headers={"Content-Type": "application/json"})
        except requests.RequestException as e:
            raise SourceUnavailable(f"OpenFIGI -> {type(e).__name__}") from e
        if r.status_code == 429:                  # respect the documented 25/min
            time.sleep(20)
            continue
        if r.status_code != 200:
            raise SourceUnavailable(f"OpenFIGI -> HTTP {r.status_code}")
        res = r.json()

        for cusip, entry in zip(batch, res):
            data = (entry or {}).get("data") or []
            prefer = [d for d in data if d.get("exchCode") == "US"] or data
            if not prefer:
                continue
            d0 = prefer[0]
            tk = (d0.get("ticker") or "").upper()
            st = d0.get("securityType2") or d0.get("securityType") or ""
            if not TICKER_RE.match(tk) or st not in ALLOWED_SECURITY_TYPES:
                continue
            out[cusip] = {"ticker": tk, "name": d0.get("name") or "", "security_type": st}
        time.sleep(FIGI_SLEEP)
    return out


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_TICKER_NAMES_CACHE: list[tuple[str, str]] | None = None


def _ticker_names(conn) -> list[tuple[str, str]]:
    """(symbol, UPPER company_name) from the local `tickers` table."""
    global _TICKER_NAMES_CACHE
    if _TICKER_NAMES_CACHE is None:
        _TICKER_NAMES_CACHE = [
            (r[0], (r[1] or "").upper())
            for r in conn.execute("SELECT symbol, company_name FROM tickers "
                                  "WHERE company_name IS NOT NULL AND company_name != ''")]
    return _TICKER_NAMES_CACHE


_NAME_NOISE = re.compile(
    # PUBLIC is here because "Public Limited Co" is the written-out form of "plc",
    # which is already stripped — without it "Willis Towers Watson Public Limited Co"
    # scores 0.851 against "Willis Towers Watson PLC" and is correctly DROPPED. The
    # fix is to normalise a known legal-form token, never to lower the 0.90 fence.
    r"\b(INC|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|PUBLIC|THE|CLASS|COM|SHS|ORD|"
    r"HLDGS|HOLDINGS|GROUP|SA|NV|AG|LP|TR|TRUST|IRELAND|NEW)\b", re.I)


def _norm(name: str) -> str:
    return " ".join(_NAME_NOISE.sub(" ", (name or "").upper()).replace(",", " ").split())


def cins_fallback(conn, cusip: str, issuer: str) -> dict | None:
    """Resolve a CINS issuer NAME to a ticker — fenced, high-cutoff, always flagged.

    Returns None (caller drops loudly) unless the match clears CINS_FALLBACK_CUTOFF.
    Never called for a digit CUSIP and never called when OpenFIGI succeeded.
    """
    import difflib
    if not CINS_PREFIX.match(cusip or ""):
        return None                                   # fence 1: CINS only
    target = _norm(issuer)
    if not target:
        return None
    # A very short normalised name ("AON") is the riskiest thing to fuzzy-match:
    # at 3 characters a 0.90 ratio is nearly meaningless. Rather than drop those
    # (which would lose Aon, a real top holding) or loosen the fence, SHORT NAMES
    # REQUIRE AN EXACT normalised match. Stricter than the cutoff, not looser.
    exact_only = len(target) < 5
    pairs = _ticker_names(conn)
    scored: list[tuple[float, str, str]] = []
    for sym, nm in pairs:
        score = difflib.SequenceMatcher(None, target, _norm(nm)).ratio()
        if score > 0:
            scored.append((score, sym, nm))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    best_score, sym0, nm0 = scored[0]
    best = (sym0, nm0)

    # FENCE 5 — ambiguity is a DROP, never a coin flip.
    #
    # Baupost holds G61188127 "LIBERTY GLOBAL LTD", class "COM CL C" = LBTYK. Three
    # share classes (LBTYA/LBTYB/LBTYK) share one issuer name, so all three score
    # 0.933 and the old `score > best_score` tie-break returned whichever row SQLite
    # yielded first — LBTYA. That is a WRONG TICKER above the cutoff: exactly what
    # the CUSIP disambiguates and the name cannot. 34 short normalised names in
    # `tickers` map to more than one symbol ("UBS" covers 20).
    #
    # A name that does not identify a unique security is not a match. Drop it.
    tied = {sym for sc, sym, _ in scored if abs(sc - best_score) < 1e-9}
    if len(tied) > 1:
        return None
    floor = 1.0 if exact_only else CINS_FALLBACK_CUTOFF
    if not best or best_score < floor:                 # fence 3: drop below cutoff
        return None
    if not TICKER_RE.match(best[0].upper()):
        return None
    return {"ticker": best[0].upper(), "name": best[1],
            "security_type": "CINS/name-matched", "confidence": CONF_NAME,
            "match_score": round(best_score, 3)}


def ensure_tables(conn) -> None:
    """Additive only — a new table, no change to any existing one."""
    conn.execute("""CREATE TABLE IF NOT EXISTS institutional_holdings (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        cik          TEXT NOT NULL,
        filer_name   TEXT,
        accession    TEXT NOT NULL,
        filing_date  TEXT NOT NULL,
        report_date  TEXT,
        cusip        TEXT NOT NULL,
        issuer       TEXT,
        ticker       TEXT,
        value_usd    REAL,
        shares       REAL,
        ingested_at  TEXT DEFAULT (datetime('now')),
        UNIQUE(accession, cusip)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inst_cik_report "
                 "ON institutional_holdings(cik, report_date)")
    conn.commit()


def seen_accession(conn, accession: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM institutional_holdings WHERE accession=? LIMIT 1",
        (accession,)).fetchone() is not None


def has_baseline(conn, cik: str, before_report: str) -> bool:
    """Have we ever stored an EARLIER quarter for this filer?

    If not, every holding trivially looks like a NEW position. The first live
    smoke emitted 27 of 27 Berkshire holdings as "NEW" — deploying that across the
    whale list would have flooded the gate with a few hundred spurious HIGH alerts
    on day one, all of them artefacts of an empty table rather than real
    disclosures. So the first filing seen for a filer is stored as a BASELINE and
    emits nothing; real position changes are detected from the next quarter on.
    """
    return conn.execute(
        "SELECT 1 FROM institutional_holdings WHERE cik=? AND report_date < ? LIMIT 1",
        (cik, before_report)).fetchone() is not None


def prior_position(conn, cik: str, cusip: str, before_report: str) -> float:
    row = conn.execute(
        """SELECT shares FROM institutional_holdings
           WHERE cik=? AND cusip=? AND report_date < ?
           ORDER BY report_date DESC LIMIT 1""",
        (cik, cusip, before_report)).fetchone()
    return float(row["shares"] or 0) if row else 0.0


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def classify(prior_shares: float, now_shares: float) -> tuple[str, str] | None:
    """(kind, severity) for a position change, or None if it isn't a signal."""
    if now_shares <= 0:
        return None
    if prior_shares <= 0:
        return ("NEW", "HIGH")
    pct = (now_shares - prior_shares) / prior_shares * 100
    if pct >= INCREASE_PCT:
        return ("ADD", "HIGH" if pct >= 100 else "MEDIUM")
    return None


def run(emit: bool = False, time_budget: float = TIME_BUDGET_SECONDS,
        whales: dict[str, str] | None = None) -> dict:
    t0 = time.time()
    deadline = t0 + time_budget
    conn = db_connection()
    ensure_tables(conn)

    scanned = stored = emitted = 0
    skipped_quant = dropped_unmapped = baselined = 0
    unmapped_detail: list[str] = []
    name_matched = 0
    partial = False        # ran out of time budget -> resumable
    crashed = False        # died on an exception -> distinct, and must read differently
    failures: list[str] = []
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    targets = whales if whales is not None else _whales()

    try:
        for cik, label in targets.items():
            if time.time() > deadline:
                partial = True
                break
            try:
                filings = recent_13f(cik, limit=1)
            except SourceUnavailable as e:
                failures.append(f"{label}: {e}")     # LOUD, not a silent zero
                continue
            if not filings:
                continue
            f = filings[0]

            # Timing (finding 4): only NEWLY-FILED 13Fs, keyed on filingDate, so
            # created_at ~ filing + <1 day. Never bulk-ingest a trailing window.
            if f["filing_date"] < cutoff or seen_accession(conn, f["accession"]):
                continue

            try:
                held = holdings(cik, f["accession"])
            except SourceUnavailable as e:
                failures.append(f"{label}: {e}")
                continue

            if len(held) > MAX_HOLDINGS:            # finding 3: quant sweep guard
                skipped_quant += 1
                print(f"[{RULE}] skip {label}: {len(held)} holdings > MAX_HOLDINGS")
                continue

            scanned += len(held)
            baseline = not has_baseline(conn, cik, f["report_date"])
            if baseline:
                baselined += 1
                print(f"[{RULE}] {label}: first filing seen — storing {len(held)} "
                      f"holdings as BASELINE, emitting nothing")
            material = {c: h for c, h in held.items() if h["value"] >= MIN_POSITION_USD}
            mapped = map_cusips(list(material), budget_deadline=deadline)

            # A dropped holding must be VISIBLE, not silent. OpenFIGI resolves
            # 0% of CINS (letter-prefixed) CUSIPs — verified across all 316 in a
            # 4-filer universe and again on 6 real Berkshire names. On a conviction
            # book that blind spot is Chubb, Aon, Accenture and Willis Towers
            # Watson, i.e. top-ten positions, not obscure micro-caps. Dropping is
            # still the right call (never guess a ticker), but dropping QUIETLY
            # would hide a known functional gap behind a healthy-looking run.
            # fence 2: the fallback runs ONLY where the CUSIP lookup failed
            for c, h in material.items():
                if c in mapped:
                    continue
                fb = cins_fallback(conn, c, h["issuer"])
                if fb:
                    mapped[c] = fb
                    name_matched += 1
                    print(f"[{RULE}] CINS fallback: {h['issuer'][:34]} {c} -> "
                          f"{fb['ticker']} (score {fb['match_score']}, FLAGGED low-confidence)")

            missing = [(c, h["issuer"], h["value"]) for c, h in material.items()
                       if c not in mapped]
            dropped_unmapped += len(missing)
            for c, iss, val in sorted(missing, key=lambda m: -m[2])[:10]:
                kind = "CINS/foreign" if c[:1].isalpha() else "unresolved"
                unmapped_detail.append(f"{iss[:28]}({c},{kind},${val/1e6:.0f}M)")
                print(f"[{RULE}] DROPPED {kind}: {iss[:40]} {c} ${val/1e6:,.0f}M "
                      f"— no ticker, not guessed")

            for cusip, h in material.items():
                m = mapped.get(cusip)
                if not m:
                    continue                         # drop, never guess (finding 1)
                ticker = normalize_ticker(m["ticker"])
                if not ticker or " " in ticker:      # WS3: single symbols only
                    continue

                prior = prior_position(conn, cik, cusip, f["report_date"])
                conn.execute(
                    """INSERT OR IGNORE INTO institutional_holdings
                       (cik, filer_name, accession, filing_date, report_date,
                        cusip, issuer, ticker, value_usd, shares)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (cik, f["filer_name"] or label, f["accession"], f["filing_date"],
                     f["report_date"], cusip, h["issuer"], ticker, h["value"], h["shares"]))
                stored += 1

                verdict = None if baseline else classify(prior, h["shares"])
                if not verdict or not emit:
                    continue
                kind, severity = verdict
                pct = ((h["shares"] - prior) / prior * 100) if prior > 0 else None
                what = ("a NEW position" if kind == "NEW"
                        else f"a {pct:.0f}% increase")
                low_conf = m.get("confidence", CONF_LOOKUP) != CONF_LOOKUP
                headline = (f"Whale disclosure — {f['filer_name'] or label} disclosed "
                            f"{what} in {ticker} (${h['value']/1e6:,.0f}M)"
                            + (" [ticker name-matched, unverified]" if low_conf else ""))
                detail = (
                    f"{f['filer_name'] or label} reported {h['shares']:,.0f} shares of "
                    f"{h['issuer']} (${h['value']:,.0f}) for the quarter ended "
                    f"{f['report_date']}, filed {f['filing_date']}. "
                    f"Prior quarter: {prior:,.0f} shares. "
                    f"This is a DISCLOSURE of a position held as of {f['report_date']} — "
                    f"not evidence of buying today."
                    + ("" if not low_conf else
                       f" ⚠ TICKER CONFIDENCE: name-matched, not a CUSIP lookup. "
                       f"CUSIP {cusip} is a CINS (foreign-domiciled) which OpenFIGI cannot "
                       f"resolve; {ticker} was matched from the issuer name at score "
                       f"{m.get('match_score')}. Treat the symbol as unverified."))
                insert_alert(
                    conn, RULE, ticker, severity, headline,
                    why_matters=("A concentrated institution disclosing a new or materially "
                                 "increased stake is an independent read on a ticker that "
                                 "other instruments may also be touching."),
                    tags={"filer": f["filer_name"] or label, "cik": cik, "cusip": cusip,
                          "kind": kind, "shares": h["shares"], "prior_shares": prior,
                          "value_usd": h["value"], "report_date": f["report_date"],
                          "filing_date": f["filing_date"], "security_type": m["security_type"],
                          "source": "SEC 13F-HR",
                          # A name-matched ticker must NEVER read as a lookup.
                          "ticker_confidence": m.get("confidence", CONF_LOOKUP),
                          "match_score": m.get("match_score"),
                          "mapping": ("OpenFIGI CUSIP lookup"
                                      if m.get("confidence", CONF_LOOKUP) == CONF_LOOKUP
                                      else "CINS issuer-name fallback (LOW CONFIDENCE)")},
                    source_url=f"https://www.sec.gov/Archives/edgar/data/"
                               f"{int(cik)}/{f['accession'].replace('-','')}/",
                    detail=detail,
                    event_date=f["filing_date"],     # provenance: the disclosure date
                )
                emitted += 1
                print(f"[{RULE}] EMIT {severity}: {headline}")
            conn.commit()
        conn.commit()
    except BaseException:
        crashed = True
        raise
    finally:
        notes = (f"filers={len(targets)} scanned={scanned} stored={stored} "
                 f"emitted={emitted} quant_skipped={skipped_quant} "
                 f"unmapped_dropped={dropped_unmapped} name_matched={name_matched} "
                 f"baselined={baselined}"
                 + (f" | DROPPED: {'; '.join(unmapped_detail[:6])}" if unmapped_detail else "")
                 + (" PARTIAL(time budget, resumable)" if partial else "")
                 + (" ABORTED(exception)" if crashed else ""))
        if failures or crashed:
            notes = "CRITICAL: " + ("; ".join(failures[:4]) or "run aborted") + " | " + notes
        try:
            from jpt_common import record_activity
            record_activity(RULE, scanned=scanned, flagged=stored, emitted=emitted,
                            duration_seconds=round(time.time() - t0, 2), notes=notes)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        print(f"[{RULE}] {notes}")

    return {"scanned": scanned, "stored": stored, "emitted": emitted,
            "partial": partial, "failures": failures, "baselined": baselined,
            "skipped_quant": skipped_quant, "dropped_unmapped": dropped_unmapped,
            "unmapped_detail": unmapped_detail, "name_matched": name_matched}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Detect tracked institutions disclosing new/increased positions (13F).")
    p.add_argument("--emit-alerts", action="store_true", help="Insert alerts.")
    p.add_argument("--time-budget", type=float, default=TIME_BUDGET_SECONDS,
                   help=f"Seconds before a resumable partial (default {TIME_BUDGET_SECONDS:.0f}).")
    return p


if __name__ == "__main__":
    a = build_parser().parse_args()
    run(emit=a.emit_alerts, time_budget=a.time_budget)
