#!/usr/bin/env python3

from __future__ import annotations

import math
import os
import sqlite3
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


SCHEMA_PATH = Path(__file__).resolve().parent / "schema_sqlite.sql"


AMOUNT_BANDS: dict[str, str] = {
    "$1,001 - $15,000": "$1k–15k",
    "$15,001 - $50,000": "$15k–50k",
    "$50,001 - $100,000": "$50k–100k",
    "$100,001 - $250,000": "$100k–250k",
    "$250,001 - $500,000": "$250k–500k",
    "$500,001 - $1,000,000": "$500k–1M",
    "$1,000,001 - $5,000,000": "$1M–5M",
    "$5,000,001 - $25,000,000": "$5M–25M",
    "$25,000,001 - $50,000,000": "$25M–50M",
    "Over $50,000,000": "$50M+",
}


CRITICAL_TAGS = {"cluster", "cross_reference"}
HIGH_TAGS = {"amount_above_50k"}


def _initialize_schema(conn: sqlite3.Connection) -> None:
    if not SCHEMA_PATH.exists():
        return

    with SCHEMA_PATH.open("r", encoding="utf-8") as handle:
        conn.executescript(handle.read())

    conn.commit()
    _run_migrations(conn)


SECTOR_MAP: dict[str, list[str]] = {
    "Defense & Aerospace": ["LMT", "RTX", "NOC", "GD", "BA", "HII", "LHX", "KTOS", "LDOS", "SAIC", "CACI", "BAH", "LEIDOS", "L3H", "DRS", "CW", "HEICO"],
    "Technology":          ["NVDA", "AAPL", "MSFT", "AMD", "INTC", "TSM", "AVGO", "QCOM", "ARM", "MU", "AMAT", "LRCX", "KLAC", "SNPS", "CDNS", "AI", "PLTR"],
    "Finance & Banking":   ["GS", "JPM", "MS", "BAC", "C", "WFC", "BLK", "AXP", "SCHW", "COF", "USB", "PNC", "TFC", "BK"],
    "Energy":              ["XOM", "CVX", "COP", "USO", "XLE", "OXY", "SLB", "EOG", "VLO", "MPC", "PSX", "HAL", "DVN", "HES", "MRO"],
    "Healthcare & Pharma": ["JNJ", "PFE", "MRK", "ABBV", "UNH", "CVS", "LLY", "AMGN", "GILD", "REGN", "BIIB", "BMY", "VRTX", "ISRG"],
    "Crypto & Fintech":    ["COIN", "MSTR", "PYPL", "SQ", "MARA", "RIOT", "CLSK", "HUT", "HIVE"],
    "Telecom":             ["T", "VZ", "TMUS", "CMCSA", "CHTR", "DISH", "AMT", "CCI"],
    "Government Contractors": ["PLTR", "SAIC", "CACI", "BAH", "LDOS", "CSCO", "ORCL", "IBM", "MAXIMUS"],
}

SECTOR_KEYWORDS: dict[str, list[str]] = {
    "Defense & Aerospace": [
        "defense", "aerospace", "military", "lockheed", "raytheon", "northrop",
        "general dynamics", "boeing", "department of defense", "dod", "army",
        "navy", "air force", "pentagon", "contractor", "missile", "weapon",
        "fighter", "submarine", "satellite", "munition", "warfighter", "combat",
        "leidos", "l3harris", "saic", "booz allen", "bae systems",
    ],
    "Technology": [
        "semiconductor", "chip", "nvidia", "apple", "microsoft", "software",
        "artificial intelligence", " ai ", "cloud", "data center", "tech",
        "quantum", "cyber", "cybersecurity", "silicon", "processor", "gpu",
        "computing", "algorithm", "digital", "internet", "broadband",
    ],
    "Energy": [
        "oil", "gas", "energy", "petroleum", "pipeline", "refinery",
        "exxon", "chevron", "shell", "iran", "opec", "crude", "lng",
        "natural gas", "offshore", "drilling", "renewable", "solar", "wind",
        "nuclear", "coal", "carbon", "emissions", "climate",
    ],
    "Healthcare & Pharma": [
        "pharma", "drug", "fda", "medicare", "medicaid", "biotech",
        "clinical", "therapy", "hospital", "health", "pfizer", "merck",
        "vaccine", "treatment", "diagnostic", "genomic", "oncology",
        "insurance", "prescription", "formulary", "biosimilar",
    ],
    "Finance & Banking": [
        "bank", "financial", "goldman", "jpmorgan", "morgan stanley",
        "stablecoin", "crypto", "sec regulation", "fintech", "insurance",
        "interest rate", "federal reserve", "fed", "credit", "lending",
        "dodd-frank", "capital", "liquidity", "forex", "debit",
    ],
    "Government Contractors": [
        "palantir", "saic", "booz allen", "leidos", "caci", "federal contract",
        "government contract", "usaspending", "procurement", "gsa", "dod contract",
        "defense contract", "task order", "blanket purchase", "sole source",
    ],
}

WHY_MATTERS: dict[str, str] = {
    "RULE_01B":     "A first-ever disclosed position signals high conviction — members rarely open brand-new names without strong thesis.",
    "RULE_02":      "Cluster trading by multiple members on the same ticker within a week suggests coordinated awareness of non-public information.",
    "RULE_06":      "Executive selling at multiples of their historical average indicates unusual distribution behavior — insiders rarely sell this aggressively without reason.",
    "RULE_07":      "Prediction market moves often precede hard news — sophisticated traders are repositioning based on private information.",
    "RULE_08":      "Proposed regulations directly reshape sector economics, competitive dynamics, and compliance costs — positions ahead of finalization capture the largest moves.",
    "RULE_09":      "Lobbying spend spikes indicate companies anticipating specific regulatory action affecting their business model or revenue.",
    "RULE_10":      "Multiple independent data sources converging on a single ticker within 48 hours is the strongest pattern in Scope — historically the most reliable precursor to a sustained move.",
    "RULE_11":      "Government contracts provide multi-year revenue visibility and validate a company's federal relationships, often preceding additional awards.",
    "RULE_12":      "Foreign lobbying spend surges indicate governments anticipating policy decisions that affect their strategic interests — historically precedes regulatory action in mapped sectors.",
    "RULE_13":      "Large PAC contributions from industry-specific groups often precede favorable committee votes or policy positions benefiting those sectors within 90 days.",
    "RULE_14":      "Patent filing clusters signal concentrated R&D investment that often precedes product launches, regulatory approvals, or government contract awards in that vertical.",
    "RULE_15":      "Surging political keyword density in earnings calls often precedes regulatory action, government contract activity, or policy-driven sector moves — management is signaling awareness of imminent change.",
    "RULE_OSINT":   "Geopolitical events in this region historically move correlated sectors within 1–30 days, typically ahead of mainstream coverage.",
    "RULE_REDDIT":  "Retail attention combined with political keywords can amplify institutional moves and create self-fulfilling momentum.",
    "RULE_ANOMALY": "Unusual signal concentration on a ticker often precedes a catalyst event — the system is detecting something the market hasn't priced yet.",
}

REGION_TICKERS: dict[str, list[str]] = {
    "Middle East":       ["USO", "XLE", "XOM", "CVX", "LMT", "RTX"],
    "Taiwan Strait":     ["TSM", "NVDA", "AMD", "INTC", "AVGO"],
    "Eastern Europe":    ["LMT", "RTX", "NOC", "GD"],
    "Korean Peninsula":  ["TSM", "LMT", "NOC"],
    "South China Sea":   ["TSM", "NVDA", "LMT", "RTX"],
    "Russia":            ["LMT", "RTX", "NOC", "USO", "XLE"],
    "South Asia":        ["LMT", "RTX", "NOC"],
    "West Africa":       ["XOM", "CVX", "USO"],
    "East Africa":       ["USO", "XLE"],
    "North Africa":      ["USO", "XLE", "LMT"],
    "Southeast Asia":    ["TSM", "NVDA", "XOM"],
    "Latin America":     ["XOM", "CVX", "USO"],
}

# GDELT uses FIPS-10-4 country codes (2-letter), NOT ISO-3166.
# Key differences: Russia=RS, China=CH, Ukraine=UP, Israel=IS, Iraq=IZ,
# Yemen=YM, Kuwait=KU, Vietnam=VM, Philippines=RP, Myanmar=BM, Turkey=TU
COUNTRY_REGION_MAP: dict[str, str] = {
    # Middle East (FIPS codes)
    "IR": "Middle East", "IZ": "Middle East", "SY": "Middle East",
    "YM": "Middle East", "IS": "Middle East", "SA": "Middle East",
    "AE": "Middle East", "QA": "Middle East", "KU": "Middle East",
    "LE": "Middle East", "JO": "Middle East", "TU": "Middle East",
    # Eastern Europe
    "UP": "Eastern Europe", "BO": "Eastern Europe", "HU": "Eastern Europe",
    # Russia
    "RS": "Russia",
    # Taiwan Strait
    "TW": "Taiwan Strait", "CH": "Taiwan Strait",
    # Korean Peninsula
    "KN": "Korean Peninsula", "KS": "Korean Peninsula",
    # South China Sea
    "VM": "South China Sea", "RP": "South China Sea",
    "MY": "South China Sea", "ID": "South China Sea",
    # South Asia
    "PK": "South Asia", "AF": "South Asia", "IN": "South Asia", "CE": "South Asia",
    # West Africa
    "NI": "West Africa", "GH": "West Africa", "ML": "West Africa",
    "UV": "West Africa", "NG": "West Africa",
    # East Africa
    "ET": "East Africa", "KE": "East Africa", "SO": "East Africa",
    "UG": "East Africa", "SU": "East Africa",
    # North Africa
    "EG": "North Africa", "LY": "North Africa", "TS": "North Africa",
    "AG": "North Africa", "MO": "North Africa",
    # Southeast Asia
    "BM": "Southeast Asia", "TH": "Southeast Asia",
    "CB": "Southeast Asia", "LA": "Southeast Asia",
    # Latin America
    "VE": "Latin America", "CO": "Latin America", "BR": "Latin America",
    "MX": "Latin America",
}

HIGH_SIGNAL_CAMEO: dict[str, str] = {
    "13":  "Threaten",
    "14":  "Protest",
    "17":  "Coerce",
    "18":  "Assault",
    "19":  "Fight",
    "20":  "Use unconventional mass violence",
    "112": "Criticize or denounce",
    "172": "Impose administrative sanctions",
    "173": "Impose embargo or boycott",
    "175": "Expel or deport",
    "191": "Use conventional military force",
    "193": "Conduct strike or raid",
    "194": "Conduct siege",
    "195": "Employ aerial weapons",
    "196": "Violate ceasefire",
}


def _run_migrations(conn: sqlite3.Connection) -> None:
    existing_contracts = {r[1] for r in conn.execute("PRAGMA table_info(contracts)").fetchall()}
    if "award_id" not in existing_contracts:
        conn.execute("ALTER TABLE contracts ADD COLUMN award_id TEXT")
        conn.commit()

    existing_bt = {r[1] for r in conn.execute("PRAGMA table_info(backtest_results)").fetchall()}
    if "return_7d" not in existing_bt and existing_bt:
        conn.execute("ALTER TABLE backtest_results ADD COLUMN return_7d REAL")
        conn.commit()

    existing_alerts = {r[1] for r in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    if "why_matters" not in existing_alerts:
        conn.execute("ALTER TABLE alerts ADD COLUMN why_matters TEXT")
        conn.commit()

    if "lifecycle_stage" not in existing_alerts:
        conn.execute("ALTER TABLE alerts ADD COLUMN lifecycle_stage TEXT DEFAULT 'created'")
        conn.commit()

    if "source_url" not in existing_alerts:
        conn.execute("ALTER TABLE alerts ADD COLUMN source_url TEXT")
        conn.commit()

    if "confidence_score" not in existing_alerts:
        conn.execute("ALTER TABLE alerts ADD COLUMN confidence_score REAL")
        conn.commit()

    if "conflict_score" not in existing_alerts:
        conn.execute("ALTER TABLE alerts ADD COLUMN conflict_score REAL")
        conn.commit()

    if "conflict_explanation" not in existing_alerts:
        conn.execute("ALTER TABLE alerts ADD COLUMN conflict_explanation TEXT")
        conn.commit()

    # Phase 2 tables (all idempotent — IF NOT EXISTS)
    conn.execute("""CREATE TABLE IF NOT EXISTS watchlist_rules (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        label            TEXT NOT NULL,
        condition_type   TEXT NOT NULL,
        condition_value  TEXT NOT NULL,
        created_at       TEXT DEFAULT (datetime('now'))
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS region_summaries (
        region       TEXT PRIMARY KEY,
        summary      TEXT NOT NULL,
        generated_at TEXT NOT NULL
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS fara_filings (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        reg_number        TEXT,
        registrant        TEXT,
        foreign_principal TEXT,
        country           TEXT,
        period_start      TEXT,
        period_end        TEXT,
        total_receipts    REAL,
        issues_lobbied    TEXT,
        ingested_at       TEXT DEFAULT (datetime('now')),
        UNIQUE(reg_number, period_start)
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS member_funding (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        bioguide_id           TEXT UNIQUE,
        candidate_id          TEXT,
        total_raised          REAL DEFAULT 0,
        top_industries        TEXT,
        pac_summary           TEXT,
        defense_pct           REAL DEFAULT 0,
        energy_pct            REAL DEFAULT 0,
        tech_pct              REAL DEFAULT 0,
        finance_pct           REAL DEFAULT 0,
        pharma_pct            REAL DEFAULT 0,
        foreign_connected_pct REAL DEFAULT 0,
        last_updated          TEXT DEFAULT (datetime('now'))
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS patent_filings (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        patent_number  TEXT UNIQUE,
        patent_title   TEXT,
        patent_date    TEXT,
        assignee       TEXT,
        ticker         TEXT,
        category       TEXT,
        keywords_hit   TEXT,
        ingested_at    TEXT DEFAULT (datetime('now'))
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS earnings_sentiment (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT NOT NULL,
        filing_date     TEXT NOT NULL,
        accession       TEXT UNIQUE,
        political_score REAL,
        keyword_counts  TEXT,
        ingested_at     TEXT DEFAULT (datetime('now'))
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS lobbying_filings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        filing_uuid     TEXT UNIQUE,
        client_name     TEXT,
        registrant_name TEXT,
        amount          REAL DEFAULT 0,
        filing_year     INTEGER,
        period          TEXT,
        issues          TEXT,
        category        TEXT,
        ticker          TEXT,
        is_foreign      INTEGER DEFAULT 0,
        document_url    TEXT,
        ingested_at     TEXT DEFAULT (datetime('now'))
    )""")

    # Migration tracking — idempotent, runs once per named migration
    conn.execute("""CREATE TABLE IF NOT EXISTS scope_migrations (
        name       TEXT PRIMARY KEY,
        applied_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()

    # m001: downgrade severity inflation from pre-threshold-tightening alerts
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m001_severity_downgrade'"
    ).fetchone():
        conn.execute(
            "UPDATE alerts SET severity='MEDIUM' WHERE rule='RULE_07' AND severity='HIGH'"
        )
        conn.execute(
            "UPDATE alerts SET severity='HIGH' WHERE rule='RULE_OSINT' AND severity='CRITICAL'"
        )
        conn.execute(
            "DELETE FROM alerts WHERE rule='RULE_10' AND tags LIKE '%GDELT%'"
        )
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m001_severity_downgrade')")
        conn.commit()

    # m002: purge RULE_10 records that don't satisfy the 4+ eligible-rule rule.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m002_purge_invalid_rule10'"
    ).fetchone():
        bad_ids = []
        for r in conn.execute("SELECT id, tags FROM alerts WHERE rule='RULE_10'").fetchall():
            if not rule10_is_valid(rule10_rules_from_tags(r[1])):
                bad_ids.append(r[0])
        for i in range(0, len(bad_ids), 400):
            chunk = bad_ids[i:i + 400]
            conn.execute(
                f"DELETE FROM alerts WHERE id IN ({','.join('?' * len(chunk))})", chunk
            )
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m002_purge_invalid_rule10')")
        conn.commit()

    # m003: re-resolve RULE_11 contract tickers with the strict matcher; null out
    # unverified mappings so no false company→ticker pairing is ever displayed.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m003_remap_contract_tickers'"
    ).fetchone():
        try:
            tmap = [(row[0], row[1] or "")
                    for row in conn.execute("SELECT symbol, company_name FROM tickers").fetchall()]
        except Exception:
            tmap = []
        for a in conn.execute(
            "SELECT id, ticker, tags FROM alerts WHERE rule='RULE_11'"
        ).fetchall():
            recipient = (a[2] or "").split("|")[0].strip()
            if not recipient:
                continue
            tkr, _parent, conf = resolve_contractor(recipient, tmap)
            new_ticker = tkr if (tkr and conf >= CONTRACTOR_MIN_CONFIDENCE) else None
            if new_ticker != a[1]:
                conn.execute("UPDATE alerts SET ticker=? WHERE id=?", (new_ticker, a[0]))
        # Also fix the contracts table where present.
        try:
            for c in conn.execute("SELECT id, recipient_name FROM contracts").fetchall():
                tkr, _p, conf = resolve_contractor(c[1] or "", tmap)
                nt = tkr if (tkr and conf >= CONTRACTOR_MIN_CONFIDENCE) else None
                conn.execute("UPDATE contracts SET ticker=? WHERE id=?", (nt, c[0]))
        except Exception:
            pass
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m003_remap_contract_tickers')")
        conn.commit()

    # m004: re-run the contract remap with the tightened matcher (2+ token rule
    # + additional overrides) to clear single-token false positives.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m004_remap_contract_tickers_v2'"
    ).fetchone():
        try:
            tmap = [(row[0], row[1] or "")
                    for row in conn.execute("SELECT symbol, company_name FROM tickers").fetchall()]
        except Exception:
            tmap = []
        for a in conn.execute(
            "SELECT id, ticker, tags FROM alerts WHERE rule='RULE_11'"
        ).fetchall():
            recipient = (a[2] or "").split("|")[0].strip()
            if not recipient:
                continue
            tkr, _p, conf = resolve_contractor(recipient, tmap)
            new_ticker = tkr if (tkr and conf >= CONTRACTOR_MIN_CONFIDENCE) else None
            if new_ticker != a[1]:
                conn.execute("UPDATE alerts SET ticker=? WHERE id=?", (new_ticker, a[0]))
        try:
            for c in conn.execute("SELECT id, recipient_name FROM contracts").fetchall():
                tkr, _p, conf = resolve_contractor(c[1] or "", tmap)
                nt = tkr if (tkr and conf >= CONTRACTOR_MIN_CONFIDENCE) else None
                conn.execute("UPDATE contracts SET ticker=? WHERE id=?", (nt, c[0]))
        except Exception:
            pass
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m004_remap_contract_tickers_v2')")
        conn.commit()

    # m005: evidence linkage columns on daily_briefs (Section 1C).
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m005_brief_evidence'"
    ).fetchone():
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_briefs)").fetchall()}
            if "alert_ids" not in cols:
                conn.execute("ALTER TABLE daily_briefs ADD COLUMN alert_ids TEXT")
            if "evidence_json" not in cols:
                conn.execute("ALTER TABLE daily_briefs ADD COLUMN evidence_json TEXT")
        except Exception:
            pass
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m005_brief_evidence')")
        conn.commit()

    # m006: invalidate daily briefs generated under the old RULE_10 logic (which
    # cited OSINT/Polymarket as corroboration). They regenerate on next request
    # from valid records only.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m006_invalidate_stale_briefs'"
    ).fetchone():
        try:
            conn.execute("DELETE FROM daily_briefs")
        except Exception:
            pass
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m006_invalidate_stale_briefs')")
        conn.commit()

    conn.commit()


def classify_sector(ticker: str, text: str = "") -> str:
    """Classify ticker+text into a sector. Checks SECTOR_MAP first, then SECTOR_KEYWORDS."""
    t = (ticker or "").upper().replace("$", "").split()[0] if ticker else ""
    for sector, tickers in SECTOR_MAP.items():
        if t in tickers:
            return sector
    low = text.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return sector
    return "Other"


# ── RULE_10 corroboration — single authoritative definition ──────────────────
# Fires when 4+ DISTINCT eligible rule families hit the same ticker within 24h.
# Noise/synthesis rules are never eligible corroboration inputs.
RULE_10_EXCLUDED: set[str] = {"RULE_07", "RULE_OSINT", "RULE_ANOMALY", "RULE_REDDIT", "RULE_10"}
RULE_10_MIN_ELIGIBLE = 4


def rule10_eligible_rules(rules) -> list[str]:
    """Distinct, sorted eligible rule families from an arbitrary rule iterable."""
    return sorted({(r or "").strip() for r in (rules or []) if (r or "").strip()
                   and (r or "").strip() not in RULE_10_EXCLUDED})


def rule10_is_valid(rules) -> bool:
    """True iff 4+ distinct eligible rules are present."""
    return len(rule10_eligible_rules(rules)) >= RULE_10_MIN_ELIGIBLE


def rule10_rules_from_tags(tags: str) -> list[str]:
    """Extract the rule list a RULE_10 alert recorded in its JSON tags."""
    import json as _json
    try:
        t = _json.loads(tags or "{}")
    except Exception:
        return []
    if isinstance(t, dict):
        if isinstance(t.get("rules"), list):
            return [str(x) for x in t["rules"]]
        if t.get("rules_fired"):
            return [s for s in str(t["rules_fired"]).split(",") if s]
    return []


# ── Federal contractor → public equity resolution ───────────────────────────
# Government contractors are a known, finite set, so an explicit table beats
# fuzzy matching — which produced false positives like
# "RAYTHEON COMPANY" → HNST (Honest Company) because both contain "company".
# value = (ticker or None, parent_label, confidence 0-100)
CONTRACTOR_OVERRIDES: dict[str, tuple] = {
    "lockheed martin":                    ("LMT", "Lockheed Martin Corporation", 99),
    "raytheon":                           ("RTX", "RTX Corporation (Raytheon)", 98),
    "rtx corporation":                    ("RTX", "RTX Corporation", 99),
    "pratt & whitney":                    ("RTX", "RTX Corporation (Pratt & Whitney)", 95),
    "collins aerospace":                  ("RTX", "RTX Corporation (Collins Aerospace)", 95),
    "boeing":                             ("BA",  "The Boeing Company", 99),
    "northrop grumman":                   ("NOC", "Northrop Grumman Corporation", 99),
    "general dynamics":                   ("GD",  "General Dynamics Corporation", 99),
    "l3harris":                           ("LHX", "L3Harris Technologies", 99),
    "l-3 communications":                 ("LHX", "L3Harris Technologies", 90),
    "huntington ingalls":                 ("HII", "Huntington Ingalls Industries", 99),
    "leidos":                             ("LDOS", "Leidos Holdings", 99),
    "science applications international":  ("SAIC", "SAIC", 98),
    "booz allen":                         ("BAH", "Booz Allen Hamilton", 99),
    "caci":                               ("CACI", "CACI International", 97),
    "palantir":                           ("PLTR", "Palantir Technologies", 98),
    "honeywell":                          ("HON", "Honeywell International", 96),
    "general electric":                   ("GE",  "GE Aerospace", 90),
    "ge aerospace":                       ("GE",  "GE Aerospace", 95),
    "textron":                            ("TXT", "Textron Inc.", 96),
    "amentum":                            ("AMTM", "Amentum Holdings", 92),
    "jacobs":                             ("J",   "Jacobs Solutions", 88),
    "kbr":                                ("KBR", "KBR Inc.", 95),
    "parsons":                            ("PSN", "Parsons Corporation", 93),
    "v2x":                                ("VVX", "V2X Inc.", 92),
    "curtiss-wright":                     ("CW",  "Curtiss-Wright Corporation", 95),
    "oshkosh":                            ("OSK", "Oshkosh Corporation", 95),
    "aerovironment":                      ("AVAV", "AeroVironment", 95),
    "kratos":                             ("KTOS", "Kratos Defense", 95),
    "raytheon technologies":              ("RTX", "RTX Corporation", 99),
    # Known private / non-US-listed contractors — surface parent, no ticker.
    "bechtel":                            (None, "Bechtel Corporation (privately held)", 96),
    "sierra nevada":                      (None, "Sierra Nevada Corporation (private)", 94),
    "bae systems":                        (None, "BAE Systems plc (UK-listed, no US ticker)", 90),
    "mission support and test services":  (None, "MSTS — Honeywell/Jacobs/Stoller JV", 80),
    "mission support & test services":    (None, "MSTS — Honeywell/Jacobs/Stoller JV", 80),
    "battelle":                           (None, "Battelle Memorial Institute (nonprofit)", 90),
    "mitre":                              (None, "MITRE Corporation (nonprofit FFRDC)", 92),
    "aerospace corporation":              (None, "The Aerospace Corporation (nonprofit FFRDC)", 88),
    "johns hopkins":                      (None, "Johns Hopkins APL (university-affiliated)", 88),
    "humana government business":         ("HUM", "Humana Inc. (TRICARE)", 92),
    "consolidated nuclear security":      (None, "Consolidated Nuclear Security LLC (Bechtel/Leidos JV, private)", 90),
    "national technology and engineering solutions": (None, "Sandia NTESS (Honeywell-managed FFRDC)", 88),
    "national technology & engineering solutions":   (None, "Sandia NTESS (Honeywell-managed FFRDC)", 88),
    "los alamos national security":       (None, "Los Alamos National Security (FFRDC)", 88),
    "triad national security":            (None, "Triad National Security (FFRDC)", 88),
}

# Assign a ticker only at/above this confidence; otherwise show it as unverified.
CONTRACTOR_MIN_CONFIDENCE = 85

_GENERIC_TOKENS = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "llc", "llp",
    "lp", "ltd", "limited", "the", "group", "holdings", "holding", "systems",
    "technologies", "technology", "solutions", "services", "service", "international",
    "industries", "enterprises", "associates", "partners", "and", "of", "us", "usa",
    "national", "america", "american", "global", "federal", "defense",
}


def _norm_company(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9& ]", " ", (name or "").lower()).strip()


def _distinctive_tokens(name: str) -> set[str]:
    return {t for t in _norm_company(name).replace("&", " ").split()
            if t and t not in _GENERIC_TOKENS and len(t) >= 3}


def resolve_contractor(name: str, ticker_map: "list[tuple[str, str]] | None" = None) -> tuple:
    """
    Resolve a federal-contract recipient to a public equity.

    Returns (ticker | None, parent_label | None, confidence 0-100). A ticker is
    only returned when confidence >= CONTRACTOR_MIN_CONFIDENCE; otherwise the
    caller should show "No verified public ticker mapping".
    """
    low = _norm_company(name)
    if not low:
        return (None, None, 0)

    # 1. Override table — longest matching key wins (most specific).
    best_key = None
    for key in CONTRACTOR_OVERRIDES:
        if key in low and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key:
        return CONTRACTOR_OVERRIDES[best_key]

    # 2. Strict token containment against the tickers table (public issuers).
    #    Require ALL distinctive tokens of a company name to appear in the
    #    recipient's distinctive tokens — no partial/generic-word matches.
    if ticker_map:
        recip = _distinctive_tokens(name)
        if recip:
            best = (None, None, 0)
            for symbol, company in ticker_map:
                comp = _distinctive_tokens(company)
                # Require 2+ distinctive tokens to fully match — single-token
                # containment ("security", "nuclear") produced false positives.
                if len(comp) < 2:
                    continue
                if comp <= recip:  # every distinctive company token present
                    conf = 90
                    if conf > best[2]:
                        best = (symbol, (company or symbol), conf)
            if best[2] >= CONTRACTOR_MIN_CONFIDENCE:
                return best

    return (None, None, 0)


_SECTOR_FUNDING_KEYS: dict[str, str] = {
    "Defense & Aerospace": "defense_pct",
    "Energy":              "energy_pct",
    "Technology":          "tech_pct",
    "Finance & Banking":   "finance_pct",
    "Healthcare & Pharma": "pharma_pct",
}


def calculate_conflict_score(
    bioguide_id: str,
    ticker: str,
    transaction_type: str,
    member_name: str,
    conn: "sqlite3.Connection",
) -> "tuple[float, str]":
    """
    Return (score, explanation) where score is -1.0 to +1.0.
    Positive = trade cuts against member's funding profile (stronger signal).
    Negative = trade aligns with member's funding profile (weaker signal).
    """
    row = conn.execute(
        "SELECT defense_pct, energy_pct, tech_pct, finance_pct, pharma_pct "
        "FROM member_funding WHERE bioguide_id = ?",
        (bioguide_id,),
    ).fetchone()

    if not row:
        return 0.0, "No funding data"

    sector = classify_sector(ticker)
    key    = _SECTOR_FUNDING_KEYS.get(sector)
    sector_funding = row[key] if key and row[key] is not None else 0.0

    is_buy = transaction_type.lower() in ("purchase", "p", "buy")
    if is_buy:
        conflict_score = (50.0 - sector_funding) / 50.0
    else:
        conflict_score = (sector_funding - 50.0) / 50.0

    conflict_score = max(-1.0, min(1.0, conflict_score))

    if conflict_score > 0.3:
        explanation = (
            f"CUTS AGAINST funding profile — {member_name} receives only "
            f"{sector_funding:.0f}% from {sector}, making this "
            f"{'purchase' if is_buy else 'sale'} more significant"
        )
    elif conflict_score < -0.3:
        explanation = (
            f"ALIGNS WITH funding profile — {member_name} receives "
            f"{sector_funding:.0f}% from {sector}, reducing signal weight"
        )
    else:
        explanation = "Neutral — no clear funding alignment"

    return conflict_score, explanation


import shutil
from datetime import datetime as _dt


_RAILWAY_VOLUME = Path("/app/data")
_BACKUP_MARKER  = None  # module-level sentinel; replaced with Path after first call


def _get_db_path(explicit: Optional[str]) -> Path:
    """
    Resolve the database file path.

    Priority:
    1. Explicit argument
    2. DATABASE_PATH env var
    3. Railway persistent volume (/app/data) if the directory exists
    4. Local ./data/jpt.db fallback
    """
    load_dotenv()

    if explicit:
        return Path(explicit)

    env_path = os.getenv("DATABASE_PATH", "").strip()
    if env_path:
        return Path(env_path)

    if _RAILWAY_VOLUME.is_dir():
        return _RAILWAY_VOLUME / "jpt.db"

    return Path(__file__).resolve().parent / "data" / "jpt.db"


def _backup_db(db_file: Path) -> None:
    """Copy the DB to a timestamped backup at most once per hour."""
    if not db_file.exists():
        return

    backup_dir = db_file.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    marker = backup_dir / "last_backup.txt"
    if marker.exists():
        try:
            last = float(marker.read_text())
            if (_dt.now().timestamp() - last) < 3600:
                return
        except Exception:
            pass

    timestamp   = _dt.now().strftime("%Y%m%d_%H%M")
    backup_path = backup_dir / f"jpt_{timestamp}.db"
    shutil.copy2(db_file, backup_path)

    # Keep the 24 most recent backups
    backups = sorted(backup_dir.glob("jpt_*.db"))
    for old in backups[:-24]:
        try:
            old.unlink()
        except Exception:
            pass

    marker.write_text(str(_dt.now().timestamp()))
    print(f"[backup] {backup_path.name} ({backup_path.stat().st_size // 1024} KB)", flush=True)


def db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Return a SQLite connection for the project database.

    On Railway the persistent volume at /app/data is used automatically.
    Locally falls back to DATABASE_PATH env var or ./data/jpt.db.
    Runs a non-blocking hourly backup before returning the connection.
    """
    db_file = _get_db_path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    _backup_db(db_file)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    _initialize_schema(conn)

    return conn


_HEAT_SEVERITY_WEIGHTS: dict[str, float] = {
    "CRITICAL": 3.0,
    "HIGH":     1.5,
    "MEDIUM":   0.5,
}

_HEAT_RULE_MULTIPLIERS: dict[str, float] = {
    "RULE_10": 2.0,
    "RULE_06": 1.5,
    "RULE_09": 1.3,
    "RULE_12": 1.4,
    "RULE_13": 1.4,
    "RULE_14": 1.2,
    "RULE_15": 1.2,
}


def calculate_heat_index(
    sector: str,
    conn: sqlite3.Connection,
    days: int = 30,
) -> dict:
    """
    Return a heat score for a sector based on alert severity × rule weight × recency decay.
    Score is normalised by days so windows are comparable.
    Returns dict: score, trend, dominant_rule, alert_count.
    """
    tickers = SECTOR_MAP.get(sector, [])
    if not tickers:
        return {"score": 0.0, "trend": "flat", "dominant_rule": None, "alert_count": 0}

    # Exclude high-volume noisy rules — they skew scores without adding signal
    _HEAT_EXCLUDED = ("'RULE_07'", "'RULE_OSINT'", "'RULE_REDDIT'", "'RULE_ANOMALY'")
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT severity, rule,
               (julianday('now') - julianday(created_at)) AS age_days
        FROM alerts
        WHERE ticker IN ({placeholders})
          AND rule NOT IN ({','.join(_HEAT_EXCLUDED)})
          AND created_at >= datetime('now', '-{int(days)} days')
        """,
        tickers,
    ).fetchall()

    if not rows:
        return {"score": 0.0, "trend": "flat", "dominant_rule": None, "alert_count": 0}

    rule_scores: dict[str, float] = {}
    current_half = 0.0
    prev_half = 0.0
    half = days / 2.0

    for row in rows:
        sev_w   = _HEAT_SEVERITY_WEIGHTS.get(row[0] or "MEDIUM", 0.5)
        rule    = (row[1] or "").upper()
        rule_w  = _HEAT_RULE_MULTIPLIERS.get(rule, 1.0)
        age     = max(0.0, float(row[2] or 0))  # age is in DAYS from julianday diff
        decay   = 0.5 ** (age / 2.0)            # 2-day (48-hour) half-life
        contrib = sev_w * rule_w * decay
        rule_scores[rule] = rule_scores.get(rule, 0.0) + contrib
        if age <= half:
            current_half += contrib
        else:
            prev_half += contrib

    raw_score = sum(rule_scores.values()) / max(days, 1)
    score     = round(min(math.log1p(raw_score * 3) * 25, 100.0), 1)

    if prev_half > 0:
        ratio = current_half / prev_half
        trend = "up" if ratio > 1.2 else ("down" if ratio < 0.8 else "flat")
    else:
        trend = "up" if current_half > 0 else "flat"

    dominant_rule = max(rule_scores, key=lambda k: rule_scores[k]) if rule_scores else None

    return {
        "score":        score,
        "trend":        trend,
        "dominant_rule": dominant_rule,
        "alert_count":  len(rows),
    }


def severity_score(tags: list[str]) -> str:
    """
    CRITICAL if a cluster or cross-reference tag is present, HIGH if the
    transaction amount exceeds $50k (tagged "amount_above_50k"), MEDIUM otherwise.
    """
    tag_set = {str(tag).strip().casefold() for tag in tags if tag}

    if tag_set & CRITICAL_TAGS:
        return "CRITICAL"

    if tag_set & HIGH_TAGS:
        return "HIGH"

    return "MEDIUM"
