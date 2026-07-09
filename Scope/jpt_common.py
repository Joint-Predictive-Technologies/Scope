#!/usr/bin/env python3

from __future__ import annotations

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
}

COUNTRY_REGION_MAP: dict[str, str] = {
    "IR": "Middle East", "IQ": "Middle East", "SY": "Middle East",
    "YE": "Middle East", "IL": "Middle East", "SA": "Middle East",
    "AE": "Middle East", "QA": "Middle East", "KW": "Middle East",
    "UA": "Eastern Europe", "BY": "Eastern Europe",
    "RU": "Russia",
    "TW": "Taiwan Strait", "CN": "Taiwan Strait",
    "KN": "Korean Peninsula", "KS": "Korean Peninsula",
    "PK": "South Asia", "AF": "South Asia", "IN": "South Asia",
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

    # lifecycle_stage column on alerts
    if "lifecycle_stage" not in existing_alerts:
        conn.execute("ALTER TABLE alerts ADD COLUMN lifecycle_stage TEXT DEFAULT 'created'")
        conn.commit()

    # watchlist_rules table (idempotent — IF NOT EXISTS)
    conn.execute("""CREATE TABLE IF NOT EXISTS watchlist_rules (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        label            TEXT NOT NULL,
        condition_type   TEXT NOT NULL,
        condition_value  TEXT NOT NULL,
        created_at       TEXT DEFAULT (datetime('now'))
    )""")

    # region_summaries table for OSINT globe caching
    conn.execute("""CREATE TABLE IF NOT EXISTS region_summaries (
        region       TEXT PRIMARY KEY,
        summary      TEXT NOT NULL,
        generated_at TEXT NOT NULL
    )""")

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


def db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Return a SQLite connection for the project database.

    DB path priority:
    1. Explicit db_path argument
    2. DATABASE_PATH from .env
    3. Default: ./data/jpt.db

    Initializes all tables from schema_sqlite.sql before returning.
    """
    load_dotenv()

    default = Path(__file__).resolve().parent / "data" / "jpt.db"
    path = db_path or os.getenv("DATABASE_PATH") or str(default)
    db_file = Path(path)

    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    _initialize_schema(conn)

    return conn


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
