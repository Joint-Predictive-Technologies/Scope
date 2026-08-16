#!/usr/bin/env python3

from __future__ import annotations

import math
import os
import sqlite3
import sys
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
    "RULE_16":      "A concentrated institution disclosing a new or materially increased stake is an independent read on a ticker other instruments may also be touching — note this is a DISCLOSURE of a position held as of quarter-end, not evidence of buying today.",
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

    # m002: purge RULE_10 records that don't satisfy the gate (now 3+ INSTRUMENTS in 14d; rule10_is_valid is the authority).
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

    # m007: event_date column + backfill, so the heat map can distinguish real
    # signal/event timing from bulk-ingestion (created_at) timing.
    if "event_date" not in existing_alerts:
        try:
            conn.execute("ALTER TABLE alerts ADD COLUMN event_date TEXT")
            conn.commit()
        except Exception:
            pass
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m007_backfill_event_date'"
    ).fetchone():
        import re as _re
        _date_re = _re.compile(r"\d{4}-\d{2}-\d{2}")

        def _extract(rule: str, tags: str) -> str | None:
            if not tags:
                return None
            if rule == "RULE_01B":
                parts = tags.split("|")
                if len(parts) >= 4 and _date_re.match(parts[3].strip()):
                    return parts[3].strip()[:10]
            if rule == "RULE_11":
                seg = tags.split("|")[1] if "|" in tags else (
                    tags.split(",")[1] if "," in tags else "")
                m = _date_re.match((seg or "").strip())
                if m:
                    return m.group(0)
            return None

        for a in conn.execute(
            "SELECT id, rule, tags FROM alerts WHERE rule IN ('RULE_01B','RULE_11') "
            "AND event_date IS NULL"
        ).fetchall():
            ev = _extract(a[1], a[2] or "")
            if ev:
                conn.execute("UPDATE alerts SET event_date=? WHERE id=?", (ev, a[0]))
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m007_backfill_event_date')")
        conn.commit()

    # m008: Phase-2 intelligence data model — themes, theme_signals, activity_log,
    # thesis_outcomes + scoring columns on alerts. Additive only.
    conn.execute("""CREATE TABLE IF NOT EXISTS themes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, region TEXT, sector TEXT,
        status TEXT DEFAULT 'Emerging',
        first_signal_at TEXT, last_updated TEXT,
        signal_count INTEGER DEFAULT 0,
        novelty_score REAL DEFAULT 1.0,
        absorption_pct REAL DEFAULT 0.0,
        evidence_confidence REAL DEFAULT 0.0,
        opportunity_score REAL DEFAULT 0.0,
        time_horizon TEXT DEFAULT 'SHORT',
        supporting_rules TEXT, conflicting_evidence TEXT,
        conflict_status TEXT DEFAULT 'Insufficient Evidence',
        invalidation_conditions TEXT, watch_triggers TEXT,
        historical_analogue TEXT, what_changed TEXT,
        primary_ticker TEXT, affected_tickers TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS theme_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        theme_id INTEGER, alert_id INTEGER,
        added_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        events_scanned INTEGER DEFAULT 0,
        events_flagged INTEGER DEFAULT 0,
        alerts_emitted INTEGER DEFAULT 0,
        run_at TEXT DEFAULT (datetime('now')),
        duration_seconds REAL, notes TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS thesis_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        theme_id INTEGER, ticker TEXT,
        price_at_thesis_start REAL, price_at_resolution REAL,
        move_pct REAL, direction TEXT,
        resolved_at TEXT, notes TEXT
    )""")
    conn.commit()

    _alerts_cols = {r[1] for r in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    _new_cols = [
        ("novelty_score", "REAL DEFAULT 1.0"),
        ("absorption_pct", "REAL DEFAULT 0.0"),
        ("time_horizon", "TEXT DEFAULT 'SHORT'"),
        ("evidence_confidence", "REAL DEFAULT 0.0"),
        ("opportunity_score", "REAL DEFAULT 0.0"),
        ("source_quality", "TEXT DEFAULT 'Secondary'"),
        ("verify_url", "TEXT"),
        ("theme_id", "INTEGER"),
        ("price_at_detection", "REAL"),
        ("price_move_since_detection", "REAL"),
    ]
    for col, decl in _new_cols:
        if col not in _alerts_cols:
            try:
                conn.execute(f"ALTER TABLE alerts ADD COLUMN {col} {decl}")
            except Exception:
                pass
    conn.commit()

    # m008b: backfill deterministic time_horizon + source_quality by rule.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m008_intel_backfill'"
    ).fetchone():
        for rule, horizon in RULE_TIME_HORIZONS.items():
            conn.execute(
                "UPDATE alerts SET time_horizon=? WHERE rule=? AND (time_horizon IS NULL OR time_horizon='SHORT')",
                (horizon, rule),
            )
        for rule, quality in RULE_SOURCE_QUALITY.items():
            conn.execute(
                "UPDATE alerts SET source_quality=? WHERE rule=?",
                (quality, rule),
            )
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m008_intel_backfill')")
        conn.commit()

    # m009: annotations — alert_votes gains a useful/not_useful dimension.
    _votes_cols = {r[1] for r in conn.execute("PRAGMA table_info(alert_votes)").fetchall()}
    if _votes_cols and "vote" not in _votes_cols:
        try:
            conn.execute("ALTER TABLE alert_votes ADD COLUMN vote TEXT DEFAULT 'useful'")
            conn.commit()
        except Exception:
            pass

    # m010: alert_annotations — thumbs up/down, the training signal for future
    # source-quality weighting. One annotation per (alert_id, user_id); updating
    # replaces the prior one. user_id nullable now (single-user) — hooked in so a
    # multi-user migration isn't needed later. note nullable for a future "why?".
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m010_alert_annotations'"
    ).fetchone():
        conn.execute(
            """CREATE TABLE IF NOT EXISTS alert_annotations (
                   id           INTEGER PRIMARY KEY AUTOINCREMENT,
                   alert_id     INTEGER NOT NULL,
                   annotation   TEXT NOT NULL,          -- 'up' | 'down'
                   user_id      TEXT,                   -- nullable (single-user for now)
                   note         TEXT,                   -- nullable (future 'why?' comment)
                   annotated_at TEXT DEFAULT (datetime('now'))
               )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_annotations_alert "
                     "ON alert_annotations(alert_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_annotations_pair "
                     "ON alert_annotations(alert_id, user_id)")
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m010_alert_annotations')")
        conn.commit()

    # m011: war_rooms — per-entity (thesis/cluster) free-text note + entity-level
    # thumbs annotation. entity_type ∈ {'theme','cluster'}, entity_id = theme id
    # (str) or cluster fingerprint. Single row per entity (single-user for now).
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m011_war_rooms'"
    ).fetchone():
        conn.execute(
            """CREATE TABLE IF NOT EXISTS war_rooms (
                   entity_type  TEXT NOT NULL,          -- 'theme' | 'cluster'
                   entity_id    TEXT NOT NULL,
                   note         TEXT,
                   annotation   TEXT,                   -- 'up' | 'down' | NULL
                   annotated_at TEXT,
                   updated_at   TEXT DEFAULT (datetime('now')),
                   PRIMARY KEY (entity_type, entity_id)
               )"""
        )
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m011_war_rooms')")
        conn.commit()

    # m012: briefs — cached deterministic morning brief (one row per date), so a
    # subscriber loads instantly and repeat views don't re-query.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m012_briefs'"
    ).fetchone():
        conn.execute(
            """CREATE TABLE IF NOT EXISTS briefs (
                   date         TEXT PRIMARY KEY,
                   html         TEXT,
                   text         TEXT,
                   meta_json    TEXT,
                   generated_at TEXT
               )"""
        )
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m012_briefs')")
        conn.commit()

    # m013: issuer_cap — market cap keyed on the SEC CIK rather than a typed symbol.
    # The cluster surface's cap gate is its terminal filter and it fails closed, so a
    # symbol that will not resolve silently deletes a cluster; `ticker_meta` cannot serve
    # it because its PRIMARY KEY is the symbol. Created here rather than by a
    # `CREATE TABLE IF NOT EXISTS` on every cap lookup, which is a DDL per read.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m013_issuer_cap'"
    ).fetchone():
        conn.execute(
            """CREATE TABLE IF NOT EXISTS issuer_cap (
                   cik          TEXT PRIMARY KEY,
                   market_cap   INTEGER,
                   cap_updated  TEXT
               )"""
        )
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m013_issuer_cap')")
        conn.commit()

    # m014: per-ALERT corroboration verdict. Until now the gate asked only whether a rule
    # NAME was eligible, so an insider SELL corroborated a bullish theme exactly as well as
    # a buy — which is how the RTX false convergence fired.
    #
    # ⚠️ COLUMNS, NOT `tags`, AND THAT IS THE POINT. RULE_06's `tags` is a bare positional
    # comma string (`f"{owner},{action},{multiple}x"`), so an owner name containing a comma
    # shifts the direction out of index 1 — a verdict the gate must trust cannot live behind
    # that. Typed columns also let `_candidate_alerts` widen its SELECT instead of parsing
    # text, and generalise to the rules that get signed later.
    #
    # `corroborates` is deliberately NULLABLE and NULL means UNKNOWN, not False. Only rules
    # in SIGNED_RULES are read this way, and for them NULL fails closed; for every other
    # rule the column is ignored entirely, which is what keeps the untouched instruments
    # behaving identically. Forward-only: historical alerts keep NULL and are not backfilled
    # — a verdict cannot be invented for a filing that was never re-read.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m014_alert_corroborates'"
    ).fetchone():
        cols = {r[1] for r in conn.execute("PRAGMA table_info(alerts)")}
        if "corroborates" not in cols:
            conn.execute("ALTER TABLE alerts ADD COLUMN corroborates INTEGER")
        if "corroboration_note" not in cols:
            conn.execute("ALTER TABLE alerts ADD COLUMN corroboration_note TEXT")
        # ⚠️ `award_key` MOVES HERE, and that is a correctness fix, not tidying.
        # `scripts/rule_11_contracts.py:344` adds it with a lazy ALTER on its own first
        # run, so on a DB where RULE_11 has never run the column simply does not exist —
        # and the gate now SELECTs it to reach the award amount for the contracts weight.
        # A gate that raises "no such column" until an unrelated rule has run once is not
        # acceptable, so the ordered migration path owns it. RULE_11's add is guarded by
        # the same `PRAGMA table_info` check, so the two are idempotent in either order.
        # The partial index comes along because RULE_11 creates it INSIDE its
        # column-missing branch — without it here, that branch stops firing and RULE_11
        # silently loses its dedup index.
        if "award_key" not in cols:
            conn.execute("ALTER TABLE alerts ADD COLUMN award_key TEXT")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_alerts_award_key
                        ON alerts(award_key) WHERE award_key IS NOT NULL""")
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m014_alert_corroborates')")
        conn.commit()

    # m015: position_sizing_cache — the DISPLAY-ONLY materiality cache behind the ticker
    # page's position-sizing panel. Market cap, shares outstanding, public float, TTM
    # revenue, cash and operating cash flow, each stored WITH the date of the fact it came
    # from.
    #
    # ⚠️ PHYSICALLY SEPARATE FROM `ticker_meta` AND `issuer_cap` ON PURPOSE, even though it
    # holds a `market_cap` column too. Those two are read by the GATE —
    # `contract_leg_weight` requires a live `ticker_meta` row before it will weight a
    # contracts leg — so a table this one wrote into would be a display feature reaching
    # into the scoring path. Nothing in `rule_*`, `RULE_CLUSTER`, `insert_alert`,
    # `enrich_scores` or the corroboration gate reads this table, and nothing may be added
    # that does. The panel is context for a human sizing a position; it is not evidence.
    #
    # ⚠️ EVERY VALUE CARRIES ITS OWN `*_as_of`, and that is the honesty property. A share
    # count, a public float and a revenue figure are all point-in-time facts that go stale
    # at completely different rates — Boeing's cover-page share count is days old while its
    # `dei:EntityPublicFloat` is the prior June 30th by construction. A panel that printed
    # them side by side undated would be inviting a reader to combine facts a year apart.
    # There is deliberately NO dilution column: warrants/ATM/convertibles are not derivable
    # from anything Scope ingests (see `scripts/position_sizing.py`), and a NULL column is
    # an invitation to fill it in with an estimate later.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m015_position_sizing_cache'"
    ).fetchone():
        conn.execute(
            """CREATE TABLE IF NOT EXISTS position_sizing_cache (
                   symbol               TEXT PRIMARY KEY,
                   cik                  TEXT,
                   market_cap           INTEGER,
                   cap_updated          TEXT,
                   shares_outstanding   REAL,
                   shares_as_of         TEXT,
                   last_close           REAL,
                   public_float_usd     REAL,
                   public_float_as_of   TEXT,
                   ttm_revenue          REAL,
                   ttm_revenue_as_of    TEXT,
                   ttm_revenue_basis    TEXT,
                   cash_usd             REAL,
                   cash_as_of           TEXT,
                   operating_cash_flow  REAL,
                   ocf_period_start     TEXT,
                   ocf_period_end       TEXT,
                   fetched_at           TEXT
               )"""
        )
        conn.execute(
            "INSERT INTO scope_migrations(name) VALUES('m015_position_sizing_cache')")
        conn.commit()

    # m016: average daily volume, on the SAME row as the rest of the position-sizing facts.
    #
    # ⚠️ COLUMNS ON `position_sizing_cache`, NOT A SECOND TABLE, AND THE REASON IS THE FETCH.
    # ADV and `last_close` come out of ONE Yahoo chart response — the same response the
    # panel already reads its close from. A separate `liquidity_cache` would have to either
    # fire a second HTTP request for data already in hand, or write two tables from one
    # response and then keep their TTLs, their degraded-retention rules and their
    # `fetched_at` stamps in agreement forever. Those are the same fact about the same
    # company at the same instant; splitting them would invent a synchronisation problem
    # that does not otherwise exist.
    #
    # Still display-only and still outside the moat: `position_sizing_cache` is read by
    # `scripts/position_sizing.py` and the ticker API and by nothing else. Adding columns
    # does not widen that, and `test_no_rule_or_gate_module_reads_the_panel_cache` continues
    # to hold the line.
    #
    # `adv_shares` is a 20-TRADING-DAY mean and `adv_window_days` records the count that
    # actually went into it, because a holiday-shortened window is a different number and
    # the panel must be able to say which one it got.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m016_position_sizing_adv'"
    ).fetchone():
        cols = {r[1] for r in conn.execute("PRAGMA table_info(position_sizing_cache)")}
        for col, decl in (("adv_shares", "REAL"), ("adv_window_days", "INTEGER"),
                          ("adv_period_start", "TEXT"), ("adv_period_end", "TEXT")):
            if col not in cols:
                conn.execute(
                    f"ALTER TABLE position_sizing_cache ADD COLUMN {col} {decl}")
        conn.execute(
            "INSERT INTO scope_migrations(name) VALUES('m016_position_sizing_adv')")
        conn.commit()

    # m017: member_terms — the service periods `match_member_id` needs to tell two
    # people with the same name apart. Until now it could not: a 2026 PTR matched a
    # member who left in 1973, because the only tiebreak was string similarity.
    #
    # ⚠️ A TABLE, NOT `term_start`/`term_end` COLUMNS ON `members`, AND THAT IS LOAD-BEARING.
    # Members hold up to 30 terms, and 135 of the 2,692 in the roster have a genuine gap
    # of more than a year between terms (the largest is 32 years). A single min..max span
    # would assert service across a gap the member did not serve — the same
    # plausible-looking-wrong shape this project has caught repeatedly. Measured: min..max
    # and exact intervals disagree for ZERO members at the 2025-2026 filing dates in the
    # corpus today, so the shortcut would look correct right now and break silently later.
    #
    # ⚠️ NOT populated here. This creates the table only; `scripts/load_member_terms.py`
    # fills it from unitedstates/congress-legislators. An empty table is the honest
    # default: `covers()` fails OPEN (see ingest_house_index) so an unloaded table leaves
    # matching exactly as it is today rather than rejecting every filer.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m017_member_terms'"
    ).fetchone():
        conn.execute(
            """CREATE TABLE IF NOT EXISTS member_terms (
                   bioguide_id  TEXT NOT NULL,
                   term_start   TEXT NOT NULL,
                   term_end     TEXT NOT NULL,
                   chamber      TEXT,
                   state        TEXT,
                   district     TEXT,
                   source       TEXT,
                   updated_at   TEXT,
                   PRIMARY KEY (bioguide_id, term_start, term_end)
               )"""
        )
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_member_terms_bioguide
                        ON member_terms(bioguide_id)""")
        conn.execute("INSERT INTO scope_migrations(name) VALUES('m017_member_terms')")
        conn.commit()

    # m018: edgar_filings + edgar_cik_watch — EDGAR filing METADATA per CIK (form type,
    # filing date, accession), broadly, rather than only the narrow slices individual
    # rules already consume.
    #
    # ⚠️ WHY THIS EXISTS AT ALL. Filing velocity was refused at its go/no-go gate because
    # the only form-type column in the database is `filings.report_type`, which holds
    # exactly two values ('PTR' — congressional, and '4' — SEC Form 4). There is no 8-K,
    # no S-1, no S-3 and no amendment marker anywhere. The blocker was never the display
    # layer; the data did not exist. This creates it.
    #
    # ⚠️ OUTSIDE THE MOAT, AND STRUCTURALLY SO. Nothing in `rule_*`, `RULE_CLUSTER`,
    # `insert_alert`, `enrich_scores` or the corroboration gate reads these tables, and
    # nothing may be added that does — `test_no_rule_or_gate_module_reads_edgar_filings`
    # holds that line the same way the position-sizing cache's test does. These are
    # DELIBERATELY separate from `filings`: that table is the congressional-PTR corpus
    # keyed on `member_id` and IS read by the moat, and widening it with SEC form metadata
    # would put a new ingestion surface inside the scoring path on day one.
    #
    # ⚠️ `is_backfill` IS THE POINT OF THE TABLE, not a bookkeeping column. A rate-of-change
    # metric computed over rows that arrived in a catch-up run measures the INGESTER'S
    # start date, not the filer's behaviour — the exact defect that killed the previous
    # attempt, where 71% of `earnings_sentiment` landed in a single run on 2026-07-11 and
    # would have shown a four-month "burst" that was purely an ingestion event. A consumer
    # of this table can and must exclude `is_backfill = 1` from any baseline.
    #
    # ⚠️ HOW BACKFILL IS DECIDED — by OBSERVATION, never by inference from the row alone.
    # `edgar_cik_watch.monitoring_since` records the UTC instant Scope first pulled a given
    # CIK. A row is backfill if EITHER:
    #   (a) it was written by that CIK's first run — we did not watch the filing arrive, we
    #       discovered it retroactively, and that includes anything filed the same day; or
    #   (b) its `filing_date` predates `monitoring_since` — a later poll surfacing an older
    #       document (EDGAR does add amendments and late documents after the fact) is still
    #       a discovery, not new activity inside our observation window.
    # Only a filing FIRST SEEN by a later poll AND dated on/after `monitoring_since` is
    # organic. That is conservative on purpose: mislabelling a discovery as organic
    # silently deforms a baseline, while mislabelling organic as backfill only costs
    # history, and history is recoverable by waiting.
    #
    # ⚠️ `first_seen_run_id` IS KEPT EVEN THOUGH `is_backfill` IS DERIVED FROM IT. The flag
    # is what consumers filter on; the run id is what makes the flag auditable after the
    # fact. Without it, "why is this row marked backfill" is unanswerable a month later.
    #
    # 🔴 THE PRIMARY KEY IS (cik, accession_number), NOT THE ACCESSION ALONE, AND THAT IS A
    # CORRECTNESS FIX RATHER THAN A PREFERENCE. An accession is unique per DOCUMENT, not per
    # filer, and EDGAR lists one document under every CIK it concerns — Berkshire's 13G on
    # Apple appears in both Apple's and Berkshire's submission index under the same
    # accession `0001193125-24-036431`. Keyed on the accession alone, whichever CIK is
    # ingested second silently loses that filing to `ON CONFLICT DO NOTHING`, and a per-CIK
    # velocity metric then under-counts a filer for reasons invisible in its own row.
    # Measured on the first 150-ticker pass: 96 of 204,053 filings were dropped exactly this
    # way before the key was widened. Per-CIK rows keep re-polling idempotent while letting
    # a shared document count for each filer it belongs to.
    if not conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m018_edgar_filing_metadata'"
    ).fetchone():
        conn.execute(
            """CREATE TABLE IF NOT EXISTS edgar_filings (
                   accession_number  TEXT NOT NULL,
                   cik               TEXT NOT NULL,
                   form_type         TEXT NOT NULL,
                   filing_date       TEXT NOT NULL,
                   report_date       TEXT,
                   primary_document  TEXT,
                   is_amendment      INTEGER NOT NULL DEFAULT 0,
                   is_backfill       INTEGER NOT NULL,
                   first_seen_run_id TEXT NOT NULL,
                   ingested_at       TEXT NOT NULL,
                   PRIMARY KEY (cik, accession_number)
               )"""
        )
        # The accession on its own is still worth an index: "who else filed this
        # document" is the natural question once one document can span filers.
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_edgar_filings_accession
                        ON edgar_filings(accession_number)""")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_edgar_filings_cik_date
                        ON edgar_filings(cik, filing_date)""")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_edgar_filings_form
                        ON edgar_filings(form_type, filing_date)""")
        # Partial index: every velocity-shaped query filters backfill OUT, so the organic
        # rows are the hot set and are worth their own index rather than a scan.
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_edgar_filings_organic
                        ON edgar_filings(cik, filing_date) WHERE is_backfill = 0""")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS edgar_cik_watch (
                   cik               TEXT PRIMARY KEY,
                   ticker            TEXT,
                   monitoring_since  TEXT NOT NULL,
                   first_run_id      TEXT NOT NULL,
                   last_polled_at    TEXT,
                   last_run_id       TEXT,
                   poll_count        INTEGER NOT NULL DEFAULT 0
               )"""
        )
        conn.execute(
            "INSERT INTO scope_migrations(name) VALUES('m018_edgar_filing_metadata')")
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
# Fires when 3+ DISTINCT INSTRUMENTS hit the same ticker inside the convergence
# window. Noise/synthesis rules are never eligible corroboration inputs.
#
# D1 — count instruments, not rules. The North Star specifies "distinct
# mechanisms"; the old implementation counted rule NAMES, so three views of the
# congressional feed (RULE_01B, RULE_02, RULE_CLUSTER — all reading the same
# `transactions` table) could satisfy a gate meant to require three independent
# sources. Rules that read the same underlying source now collapse to one
# instrument. See 05_Decisions/2026-07-25-gate-redesign.md.
# RULE_12/13/14 are RETIRED, not noisy. Each is EXCLUDED rather than merely deleted from
# RULE_10_INSTRUMENTS, because rule10_instruments() resolves
# `RULE_10_INSTRUMENTS.get(rule, rule)` — an eligible-but-unmapped rule falls back to its
# own NAME and becomes its own PHANTOM instrument, the exact opposite of retiring it.
#   RULE_12 — claimed DOJ FARA, actually read RULE_09's own Senate LDA endpoint; never emitted
#   RULE_13 — 100% of its FEC requests 422; cannot finish inside the 300s timeout
#   RULE_14 — search.patentsview.org is authoritative NXDOMAIN (moved to USPTO ODP)
RULE_10_EXCLUDED: set[str] = {"RULE_07", "RULE_OSINT", "RULE_ANOMALY", "RULE_REDDIT",
                              "RULE_10", "RULE_12", "RULE_13", "RULE_14",
                              # RULE_COLLECTOR gathers COVERAGE — ticker names for the
                              # real instruments to cross-reference against. A collected
                              # name is not "watch this", it is "this name exists", so it
                              # must never be able to open a corroboration. RULE_DISCOVERY
                              # is its retired predecessor, kept excluded rather than
                              # deleted for the same reason RULE_12/13/14 are.
                              # Excluded rather than merely unmapped, because an
                              # eligible-but-unmapped rule becomes its OWN instrument
                              # (rule10_instruments does .get(rule, rule)) — the phantom
                              # trap that made RULE_12/13/14 count as three legs after
                              # they were "retired". Discovery emits no alerts today, so
                              # this changes nothing now; it is here so the trap cannot
                              # arm itself the day someone gives it one.
                              "RULE_DISCOVERY", "RULE_COLLECTOR",
                              # RULE_ADSB draws its tickers from `REGION_TICKERS` — the
                              # SAME hardcoded region->basket table that made RULE_OSINT
                              # publish 8 distinct tickers across 387 alerts, LMT among
                              # them because it is the first element of six regions. A
                              # ticker chosen by a lookup table is not evidence that a
                              # company is involved in anything, so it must not be able
                              # to complete a convergence.
                              #
                              # ⚠️ AND THIS ONE WAS NOT ALREADY CONTAINED. OSINT was
                              # excluded from the start; ADSB was mapped to the `flight`
                              # instrument, so `rule10_instruments(['RULE_ADSB'])`
                              # returned `['flight']` and a basket rule COULD be a gate
                              # leg — on a 5-minute cadence. Latent when this was written
                              # (0 alerts, 0 logged runs locally; UNVERIFIED in prod),
                              # which is why it is a one-line defang now rather than a
                              # rewrite. Its full fate is a separate session.
                              "RULE_ADSB",
                              # RULE_TELEGRAM_OSINT — the SAME table, and the one that was
                              # actually LIVE at the gate. `rule_telegram_osint.py:109`
                              # reads `REGION_TICKERS[region]` and `:141` writes
                              # `ticker = tickers[0]`, hourly. Measured before this line
                              # existed:
                              #
                              #   ['RULE_01B','RULE_06']                       -> 2 instruments, gate FALSE
                              #   ['RULE_01B','RULE_06','RULE_TELEGRAM_OSINT'] -> 3 instruments, gate TRUE
                              #
                              # A basket ticker completed a convergence. Found by the
                              # verification pass on the commit that defanged ADSB, whose
                              # own test asserted a CLASS ("a basket-keyed rule cannot
                              # contribute an instrument") while checking one member of it.
                              "RULE_TELEGRAM_OSINT",
                              # RULE_08 — the same disease, in the one member of the basket
                              # class that was LIVE, SCHEDULED (240 min) and COUNTED at the
                              # gate. `rule_08_federal_register.py:26` fans a KEYWORD match
                              # in a Federal Register title/abstract out into a hardcoded
                              # `SECTOR_MAP` basket ("bank" -> JPM/BAC/WFC/GS), and a prior
                              # session split the composite ticker so each basket element
                              # becomes its OWN alert — which is exactly what made
                              # `fed-register` a real, matchable instrument. Measured before
                              # this line existed:
                              #
                              #   ['RULE_01B','RULE_06']                 -> 2 instruments, FALSE
                              #   ['RULE_01B','RULE_06','RULE_08']       -> 3 instruments, TRUE
                              #
                              # The word "bank" appearing in a proposed rule is not evidence
                              # that JPMorgan is involved in anything, so it must not be able
                              # to complete a convergence.
                              #
                              # ⚠️ THIS EXCLUSION HAS A REAL COST AND IT IS INTENTIONAL: it
                              # removes a currently-counted leg, so convergences that would
                              # have fired on RULE_08 no longer do. Human-gated decision
                              # (CLAUDE.md: gate/scoring changes are human-gated). Forward-
                              # only — themes RULE_08 already helped complete are NOT
                              # retracted; the remediation query to find them by hand is in
                              # 02_Sessions/SESSION-2026-07-29-rule08-exclude.md.
                              #
                              # `fed-register` earns its way back by REAL ISSUER ATTRIBUTION
                              # (the entities actually named in the document), not by a
                              # keyword->basket fan-out. Backlogged beside RULE_09/01B/02,
                              # which have the same attribution-from-a-projection disease.
                              "RULE_08",
                              # RULE_09 — A FOURTH CATEGORY: CONTEXT. Not noisy, not retired,
                              # not a coverage collector, and NOT basket-keyed. Lobbying spend
                              # measures influence on GOVERNMENT; it is not a claim about a
                              # company's securities, so it is context around a thesis rather
                              # than an independent instrument confirming one. Human decision
                              # (Q20). Measured before this line existed:
                              #
                              #   ['RULE_01B','RULE_06']            -> 2 instruments, FALSE
                              #   ['RULE_01B','RULE_06','RULE_09']  -> 3 instruments, TRUE
                              #
                              # So this removes a leg that really did complete convergences.
                              #
                              # ⚠️ `senate-lda` NOW HAS NO ELIGIBLE MEMBER. RULE_12 (retired)
                              # was the only other rule mapped to it, so the instrument becomes
                              # unreachable. The mapping is KEPT rather than deleted: an
                              # eligible-but-unmapped rule falls back to its own name and
                              # becomes its own phantom instrument (`rule10_instruments` does
                              # `.get(rule, rule)`), which is the trap that made RULE_12/13/14
                              # count as three legs after they were "retired".
                              #
                              # Forward-only: themes RULE_09 already helped complete are NOT
                              # retracted. Verified on prod 2026-08-02 — the single existing
                              # corroboration (RTX, legs RULE_06/RULE_11/RULE_15) has no
                              # RULE_09 leg, so nothing is owed. RULE_09 still runs and still
                              # emits; resurfacing it AS context is a separate follow-up.
                              "RULE_09"}

# Rule -> instrument. Every mapping below was derived by reading the rule's own
# source, not from the design note; the citation is the source it actually reads.
RULE_10_INSTRUMENTS: dict[str, str] = {
    # congressional disclosures — all four are the same underlying feed
    "RULE_01":             "congressional",   # ingest_senate.py:278 (Senate PTR ingest)
    "RULE_01B":            "congressional",   # scripts/rule_01b_first_touch.py:42
    "RULE_02":             "congressional",   # rule_02_cluster.py:31
    "RULE_CLUSTER":        "congressional",   # scripts/rule_cluster.py:115
    # SEC EDGAR full-text search, but genuinely different documents:
    "RULE_06":             "insider",         # rule_06_form4.py:136  forms=4
    "RULE_15":             "earnings",        # scripts/rule_15_earnings_nlp.py:63  forms=8-K
    # Senate LDA filings — RULE_12 reads the SAME endpoint as RULE_09 and merely
    # filters for the foreign_entities field, so they are one instrument. NOTE:
    # rule_12_fara.py's docstring claims "DOJ FARA — fara.justice.gov", but the
    # code uses LDA_API_URL = lda.senate.gov/api/v1/filings/ (:27), same as
    # rule_09_lobbying.py:24. The code wins. This DEVIATES from the design note,
    # which lists lobbying and foreign-agents as separate instruments — flagged
    # for review; collapsing is the conservative reading of D1.
    "RULE_09":             "senate-lda",      # rule_09_lobbying.py:24
    "RULE_12":             "senate-lda",      # scripts/rule_12_fara.py:27
    # 13F institutional holdings — genuinely independent of `insider`: Form 4 is an
    # officer's own trade in their own company filed within 2 business days; a 13F is
    # an EXTERNAL manager's quarterly position report filed 45 days after quarter end.
    # Different filer, different document, different phenomenon — so it must NOT
    # collapse into `insider` even though both are EDGAR-hosted (the "same document
    # population" test, same reasoning that keeps RULE_06 and RULE_15 separate).
    "RULE_16":             "institutional",   # scripts/rule_16_institutional.py  13F-HR
    "RULE_08":             "fed-register",    # rule_08_federal_register.py:22
    "RULE_11":             "contracts",       # scripts/rule_11_contracts.py:24  usaspending
    "RULE_13":             "fec",             # scripts/rule_13_fec.py  api.open.fec.gov
    "RULE_14":             "patents",         # scripts/rule_14_patents.py  patentsview
    "RULE_ADSB":           "flight",          # scripts/rule_adsb.py  opensky-network
    "RULE_TELEGRAM_OSINT": "telegram",        # scripts/rule_telegram_osint.py  rsshub
    # RULE_OPTIONS enriches existing alerts and emits none, so it has no instrument.
}

# D2 — fire at 3 distinct instruments (was 4 distinct rules). A later
# 3=candidate / 4=strong tier is a surfacing concern, not a second gate; the
# instrument count is recorded on every corroboration so that tier is free.
RULE_10_MIN_INSTRUMENTS = 3


# ── SIGNED SIGNALS — per-ALERT eligibility, not just per-rule ─────────────────
#
# Until now the gate asked one question: is this rule NAME eligible. That made every
# leg equally and directionlessly corroborating, and it shipped a false convergence:
# RTX fired at exactly 3 instruments where the insider leg was an EXERCISE-AND-SELL —
# an officer REDUCING exposure, counted as bullish confirmation. `_candidate_alerts`
# selected ticker/rule/severity/window and never loaded `tags`; RULE_06 computed the
# direction, persisted it, and the gate discarded it.
#
# The idea is SURPRISE RELATIVE TO THE ENTITY'S OWN BASELINE: the same event means
# opposite things depending on who it happens to. An insider selling into an option
# exercise is compensation mechanics. A $50M award is a Tuesday for Lockheed and
# transformative for a defence micro-cap.
#
# ⚠️ SIGNED_RULES IS THE BLAST RADIUS, AND IT IS DELIBERATELY TINY. Per-alert
# eligibility applies ONLY to these rules. For every other rule an absent verdict
# means "corroborates", exactly as before — which is what makes "the untouched
# instruments behave identically" provable rather than hoped for.
#
# It holds only the instruments whose direction is unambiguous AND whose attribution
# is trusted. Earnings (RULE_15) and RULE_01B are deliberately ABSENT: RULE_15
# misattributed *rituximab* to RTX, and ~46% of RULE_01B's sales are mislabelled as
# opens. Signing a leg whose attribution is known-broken puts a CONFIDENT SIGN ON
# DATA KNOWN TO BE WRONG, which makes a future false convergence look more credible,
# not less. They join once their attribution repairs land. Lobbying/13F are parked:
# "which lobby implies which ticker" is a thematic association, the basket disease in
# disguise.
SIGNED_RULES: frozenset[str] = frozenset({"RULE_06"})

# A present-but-meaningless symbol; mirrors `_SENTINEL_TICKERS` in
# scripts/insider_clusters.py, whose equivalence to this module is asserted by
# tests/test_signed_insider_leg.py::test_the_two_buy_definitions_are_EQUIVALENT.
FORM4_SENTINEL_TICKERS: tuple[str, ...] = ("NA", "N/A", "NONE", "NULL", "-")


def is_genuine_open_market_buy(txn_code, acquired_disposed, is_derivative,
                              is_10b5_1, ticker) -> bool:
    """Is this Form 4 transaction an insider BUYING, on the open market, by choice?

    ⚠️ THIS DEFINITION IS NOT NEW AND MUST NOT BE RE-DERIVED. It is the Python twin of
    `scripts/insider_clusters.py::_buy_predicate`, which is already certified by
    `test_insider_clusters.py::test_what_is_NOT_a_qualifying_buy` (8 rejections,
    including `M/A` "an option exercise" and `P/D` "code P but DISPOSED"). A twin only
    exists because that one is SQL over `form4_transactions` — a table that exists
    neither locally nor in prod and that RULE_06 does not write — so the gate cannot
    join to it. Equivalence is PROVEN over an exhaustive matrix, not asserted.

    The four parts, and why each is load-bearing:
      txn_code == 'P'          an open-market purchase. Note this ALSO excludes an
                               option exercise structurally: `M` is not `P`. There is
                               deliberately no separate exercise detector — the
                               whitelist-of-one IS the discipline. `A` (grant),
                               `G` (gift), `F` (tax withholding) fall out the same way.
      acquired_disposed == 'A' BOTH halves are required. `P`/`D` — code P but disposed —
                               is a real shape and is not a buy.
      is_derivative == 0       a derivative right is not the security.
      is_10b5_1 != 1           a pre-scheduled plan trade carries no timing conviction.
                               ⚠️ TRI-STATE: NULL means NOT DISCLOSED (pre-2022 filings)
                               and is KEPT, never coerced to "planned". Undisclosed is
                               surfaced separately; it is not evidence of a plan.

    ⚠️ MIRRORS SQL'S NULL SEMANTICS EXACTLY, which is stricter than Python's instinct.
    In SQLite `NULL = 'P'` is NULL, i.e. NOT TRUE, so the row is dropped. Hence a None
    code, direction, derivative flag or ticker returns False here — fail closed. Only
    `is_10b5_1` is COALESCEd, because that column's NULL carries meaning.

    ⚠️ CASE IS NOT NORMALISED, deliberately. The SQL twin compares with SQLite's binary
    collation, so a lower-case 'p' does not match there either. `parse_transactions`
    upper-cases both fields at parse time, so the canonical domain is upper-case and
    anything else fails closed. Normalising here would be a silent divergence from the
    predicate this claims to mirror.

    ⚠️ EQUIVALENCE IS PROVEN OVER THE CANONICAL DOMAIN, WHICH IS NOT THE SAME AS "ALWAYS".
    A verification pass swept 390,390 rows including type and whitespace variants and found
    two places this is *stricter* than the SQL: SQLite's `TRIM` strips only spaces, so a
    sentinel ticker padded with `\\t` or `\\n` survives there and is rejected here; and
    `int()` on a non-numeric `is_10b5_1` used to RAISE where SQL's `COALESCE(…) != 1`
    returned true. The raise is fixed below — failing closed beats crashing — and both
    divergences are in the safe direction. Neither is reachable from RULE_06 today
    (`is_10b5_1 ∈ {None,0,1}`, ticker already `.strip()`ped by `_xml_text`), but the
    docstring should not claim more than the proof covers.
    """
    if txn_code is None or acquired_disposed is None or is_derivative is None:
        return False
    if ticker is None:                      # UPPER(TRIM(NULL)) NOT IN (...) is NULL
        return False
    if txn_code != "P" or acquired_disposed != "A":
        return False
    try:
        if int(is_derivative) != 0:
            return False
    except (TypeError, ValueError):
        return False
    if ticker.strip().upper() in FORM4_SENTINEL_TICKERS:
        return False
    # Guarded for the same reason `is_derivative` is: a non-numeric value must FAIL CLOSED,
    # not raise. An uncaught ValueError here would propagate out of the gate's counting loop
    # and abort the whole run — turning one malformed row into zero convergences.
    try:
        return (0 if is_10b5_1 is None else int(is_10b5_1)) != 1
    except (TypeError, ValueError):
        return False


def rule10_eligible_rules(rules) -> list[str]:
    """Distinct, sorted eligible rule families from an arbitrary rule iterable.

    Names are upper-cased before matching. Previously they were only stripped, so
    a non-canonical casing evaded BOTH the exclusion set and the instrument map:
    `["RULE_01B", "rule_01b", "Rule_01b"]` counted as three instruments, and
    three lower-cased *excluded* noise rules cleared the gate outright. Every
    emitter writes an upper-case module constant and the DB holds no
    non-canonical name today, so this changes nothing in practice — it just stops
    the moat's core property depending on that convention holding forever.
    """
    return sorted({(r or "").strip().upper() for r in (rules or [])
                   if (r or "").strip()
                   and (r or "").strip().upper() not in RULE_10_EXCLUDED})


def rule10_instruments(rules) -> list[str]:
    """Distinct, sorted INSTRUMENTS represented by an arbitrary rule iterable.

    Same-source rules collapse to one entry, which is the whole point of D1. An
    eligible rule with no mapping falls back to its own name, so a newly added
    rule counts as its own instrument rather than silently vanishing from the
    count — add it to RULE_10_INSTRUMENTS if it shares a source with an existing
    one.
    """
    return sorted({RULE_10_INSTRUMENTS.get(rule, rule)
                   for rule in rule10_eligible_rules(rules)})


def rule10_is_valid(rules) -> bool:
    """True iff 3+ distinct INSTRUMENTS are present (not merely 3+ rule names)."""
    return len(rule10_instruments(rules)) >= RULE_10_MIN_INSTRUMENTS


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


def _leg_weights_from_tags(tags: str) -> dict[str, float]:
    """Per-leg weights a RULE_10 alert recorded at detection time. Missing => no opinion.

    Returns `{}` for every alert emitted before leg weights existed, and for anything
    unparseable — so an absent or malformed weight can only ever mean 1.0. A weight is
    never invented, and it is never re-derived from today's market cap: detection-time
    scores are immutable in this project, and recomputing a ratio against a later price
    would silently rewrite history.
    """
    import json as _json
    try:
        t = _json.loads(tags or "{}")
    except Exception:
        return {}
    raw = t.get("leg_weights") if isinstance(t, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            w = float(v)
        except (TypeError, ValueError):
            continue
        # ⚠️ NON-FINITE VALUES FAIL TO NEUTRAL, NOT TO THE CEILING. Python's `json` accepts
        # bare `NaN` / `Infinity`, and clamping those with `min(CEILING, inf)` handed a
        # hand-edited tag the MAXIMUM boost — the exact opposite of "malformed means no
        # opinion". `nan` is worse: every comparison with it is False, so it slipped through
        # clamps silently. Found by a verification pass.
        if w != w or w in (float("inf"), float("-inf")):
            continue
        # Hard-clamped to the declared ceiling at the point of USE, not merely where the
        # weight is computed. A hand-edited or corrupted tag cannot lift a leg past
        # `CONTRACT_WEIGHT_MATERIAL`, so the bound on how much any single leg can move a
        # score does not depend on the writer having behaved.
        out[str(k).strip().upper()] = max(0.0, min(CONTRACT_WEIGHT_MATERIAL, w))
    return out


# ── Influence-organization entity resolution (lobbying / AIPAC etc.) ─────────
# Canonical org records + aliases so a search like "AIPAC" resolves to the full
# registered name used in Senate LDA filings. Kept small and evidence-backed —
# every entry corresponds to a real client name in the lobbying dataset.
ORG_ENTITIES: dict[str, dict] = {
    "american israel public affairs committee": {
        "canonical": "American Israel Public Affairs Committee",
        "aliases":   ["aipac", "american israel public affairs", "israel public affairs"],
        "sectors":   ["Defense & Aerospace"],
        "kind":      "Advocacy organization",
    },
    "j street": {
        "canonical": "J Street", "aliases": ["jstreet"],
        "sectors": ["Defense & Aerospace"], "kind": "Advocacy organization",
    },
    "national rifle association": {
        "canonical": "National Rifle Association of America",
        "aliases": ["nra", "national rifle association"],
        "sectors": [], "kind": "Advocacy organization",
    },
    "us chamber of commerce": {
        "canonical": "US Chamber of Commerce",
        "aliases": ["chamber of commerce", "u.s. chamber", "uschamber"],
        "sectors": [], "kind": "Business federation",
    },
    "pharmaceutical research and manufacturers": {
        "canonical": "Pharmaceutical Research and Manufacturers of America",
        "aliases": ["phrma", "pharmaceutical research"],
        "sectors": ["Healthcare & Pharma"], "kind": "Trade association",
    },
}


def resolve_org(query: str) -> tuple:
    """
    Resolve a free-text org query to a canonical entity.

    Returns (record | None, confidence 0-100, matched_by). Never fabricates — a
    non-match returns (None, 0, None) and callers fall back to raw client search.
    """
    q = (query or "").strip().lower()
    if not q:
        return (None, 0, None)
    for key, rec in ORG_ENTITIES.items():
        if q == key or q == rec["canonical"].lower():
            return (rec, 100, "canonical")
        if q in rec["aliases"]:
            return (rec, 100, "alias")
    # partial containment against canonical/aliases
    for key, rec in ORG_ENTITIES.items():
        hay = [key, rec["canonical"].lower(), *rec["aliases"]]
        if any(q in h or h in q for h in hay):
            return (rec, 85, "partial")
    return (None, 0, None)


# ── Phase-2 intelligence scoring (Evidence Confidence / Opportunity / Novelty) ──
# Rule → default time horizon and source quality (spec §7, §11).
RULE_TIME_HORIZONS: dict[str, str] = {
    "RULE_01B": "SHORT", "RULE_02": "SHORT", "RULE_03": "MEDIUM", "RULE_04": "SHORT",
    "RULE_05": "IMMEDIATE", "RULE_06": "MEDIUM", "RULE_07": "IMMEDIATE",
    "RULE_08": "MEDIUM", "RULE_09": "MEDIUM", "RULE_10": "SHORT", "RULE_11": "SHORT",
    "RULE_12": "LONG", "RULE_13": "SHORT", "RULE_14": "LONG", "RULE_15": "SHORT",
    "RULE_16": "MEDIUM",
    "RULE_OSINT": "IMMEDIATE", "RULE_REDDIT": "IMMEDIATE", "RULE_ANOMALY": "SHORT",
    "RULE_CLUSTER": "IMMEDIATE", "RULE_ADSB": "IMMEDIATE",
}
# Primary = direct from an authoritative filing; Derived = synthesized/social.
RULE_SOURCE_QUALITY: dict[str, str] = {
    "RULE_01B": "Primary", "RULE_02": "Primary", "RULE_06": "Primary",
    "RULE_08": "Primary", "RULE_09": "Primary", "RULE_11": "Primary",
    "RULE_12": "Primary", "RULE_13": "Primary", "RULE_14": "Primary",
    "RULE_CLUSTER": "Primary", "RULE_16": "Primary",
    "RULE_15": "Secondary", "RULE_07": "Secondary", "RULE_OSINT": "Secondary",
    "RULE_REDDIT": "Derived", "RULE_ANOMALY": "Derived", "RULE_10": "Derived",
}
_SOURCE_QUALITY_WEIGHT = {"Primary": 1.0, "Secondary": 0.6, "Derived": 0.3}


def calculate_evidence_confidence(distinct_rule_count, source_quality_scores,
                                  has_conflicting_evidence=False) -> float:
    """How strongly is the thesis supported? (Not whether opportunity remains.)

    THE TIERS ARE IN THE GATE'S UNITS: distinct INSTRUMENTS, matching
    RULE_10_MIN_INSTRUMENTS. The first tier MUST be the gate's firing threshold.

    They used to step at 4/5/6, from when the gate's threshold was expressed in rule
    NAMES and sat one tier higher. When D1 moved it to 3 INSTRUMENTS (several rules
    can read one source, so the congressional trio is one leg, not three), the
    tiers were left in the old units — so every minimum-strength corroboration fell
    BELOW the first tier, scored base=0, and persisted 6.0 against a lone RULE_06's
    20.0. A corroboration ranked at one third of its own constituent signals, and
    `mode=overwatch` orders on this column. Rescaled 2026-07-27, human-signed-off.

    If RULE_10_MIN_INSTRUMENTS ever moves, MOVE THE FIRST TIER WITH IT — that
    coupling is asserted in tests/test_evidence_confidence_instruments.py.
    """
    base = 0.0
    if distinct_rule_count >= 3:
        base = 40.0
    if distinct_rule_count >= 4:
        base = 60.0
    if distinct_rule_count >= 5:
        base = 75.0
    avg_quality = (sum(source_quality_scores) / len(source_quality_scores)
                   if source_quality_scores else 0.5)
    base += avg_quality * 20.0
    if has_conflicting_evidence:
        base *= 0.7
    return min(round(base, 1), 100.0)


def calculate_opportunity_score(novelty_score, absorption_pct, time_horizon,
                                liquidity_score=1.0, historical_win_rate=0.5) -> float:
    """Is there still likely actionable opportunity? Separate from evidence.

    Four additive terms, then a liquidity multiplier, clamped 0-100:
        novelty*40  -  (absorption/100)*30  +  horizon*20  +  win_rate*10
    The win_rate term is a real term, currently a fixed 0.5 placeholder (=> +5 on
    every alert) — a reserved input for per-rule *realized* win rate once the
    `alert_outcomes` calibration has enough data. `liquidity_score` defaults 1.0
    (no-op) until per-ticker liquidity is wired in. Keep opportunity_score_breakdown
    in sync with this formula."""
    horizon_scores = {"IMMEDIATE": 1.0, "SHORT": 0.85, "MEDIUM": 0.65, "LONG": 0.45}
    raw = (novelty_score * 40.0
           - (absorption_pct / 100.0) * 30.0
           + horizon_scores.get(time_horizon, 0.7) * 20.0
           + historical_win_rate * 10.0)
    return min(max(round(raw * liquidity_score, 1), 0.0), 100.0)


def opportunity_score_breakdown(novelty_score, absorption_pct, time_horizon,
                                historical_win_rate=0.5) -> dict:
    """Transparent decomposition of calculate_opportunity_score — the exact
    additive components, so the UI can show the reasoning, not just the number.
    Mirrors the formula in calculate_opportunity_score (keep in sync).

    The fourth component is LABELLED as an uncalibrated placeholder on purpose.
    No call site passes `historical_win_rate` — the three callers are
    `score_alert_fields`, `enrich_alert_scores` and api/routers/warroom.py's
    `cluster_detail`, and all take the default — so its value is the hard constant
    +5.0 on every alert and carries no information about the signal. It was
    previously labelled "base win-rate 0.5", which read as a measured 50% hit rate
    and collided with the genuinely measured win rate in
    `main.member_signal_integrity` (rendered as "Win Rate" on the member page).
    Labels here are display strings computed at call time and are never persisted,
    so relabelling is retroactive-safe and changes no score.

    ⚠️ UNITS, before anyone wires in a real rate: `historical_win_rate` is a
    **fraction, 0.0-1.0**. The product's measured `win_rate` is a **percent,
    0-100**. Passing the percent straight in multiplies this term by 10-100x and
    pins every score at the 100 clamp — while the row below would still read
    "uncalibrated placeholder". Convert (`win_rate / 100.0`), and change this
    label when you do: at that point it stops being a placeholder.
    """
    horizon_scores = {"IMMEDIATE": 1.0, "SHORT": 0.85, "MEDIUM": 0.65, "LONG": 0.45}
    n = float(novelty_score or 0.0)
    a = float(absorption_pct or 0.0)
    hs = horizon_scores.get(time_horizon, 0.7)
    components = [
        {"label": f"novelty {round(n, 3)}", "value": round(n * 40.0, 1)},
        {"label": f"absorption {round(a, 1)}%", "value": round(-(a / 100.0) * 30.0, 1)},
        {"label": f"{time_horizon or '—'} horizon", "value": round(hs * 20.0, 1)},
        # Value deliberately unchanged; only the wording is honest now.
        {"label": "base weight (uncalibrated placeholder)", "value": round(historical_win_rate * 10.0, 1)},
    ]
    total = min(max(round(sum(c["value"] for c in components), 1), 0.0), 100.0)
    return {"components": components, "total": total}


def calculate_novelty_score(rule, region_or_sector, conn) -> float:
    """1.0 for a first-ever signal of this type in this region/sector; decays
    logarithmically with prior 30-day occurrences of the same pattern."""
    import math
    count = conn.execute(
        """SELECT COUNT(*) FROM alerts
           WHERE rule = ?
             AND (headline LIKE ? OR COALESCE(why_matters,'') LIKE ?)
             AND created_at >= datetime('now', '-30 days')""",
        (rule, f"%{region_or_sector}%", f"%{region_or_sector}%"),
    ).fetchone()[0]
    if count == 0:
        return 1.0
    # Floor at 0.1 — at extreme recurrence 1/(1+ln(n+1)) drifts below 0.1; the
    # invariant is enforced here, not just documented.
    return max(0.1, round(1 / (1 + math.log(count + 1)), 3))


def assign_time_horizon(rule, ticker=None, conn=None) -> str:
    """IMMEDIATE / SHORT / MEDIUM / LONG based on the rule family."""
    return RULE_TIME_HORIZONS.get(rule, "SHORT")


def _distinct_rule_count(rule: str, tags: str) -> tuple:
    """(corroborator_count, source_quality_weights) for scoring an alert.

    THE COUNT IS DISTINCT INSTRUMENTS, NOT RULE NAMES.
    D1 made the GATE count instruments — several rules can read one source — but the
    evidence path kept counting names, so the congressional trio (RULE_01B + RULE_02 +
    RULE_CLUSTER = three views of one `transactions` feed) inflated confidence as if it
    were three independent corroborators. Measured: trio + contracts + insider scored
    **80.0** on 5 rule names where 3 instruments is the truth.

    The parameter keeps its old name so every caller and the DB column stay valid; only
    what it MEANS changed. Quality weights stay per-rule — averaging source quality over
    the contributing rules is still right; it is the COUNT that was wrong.
    """
    if rule == "RULE_10":
        elig = rule10_eligible_rules(rule10_rules_from_tags(tags or ""))
        # ⚠️ PER-LEG WEIGHTS, read from the tags the emitter FROZE at detection time.
        # A contracts leg whose award is routine relative to its recipient's market cap
        # contributes less confidence while still counting as the `contracts` instrument —
        # the weight moves this average, never the integer count, because the tier table
        # steps on an int and a fractional count would score base 0.
        # Absent for every unweighted rule, which reads as 1.0, so nothing else moves.
        legs = _leg_weights_from_tags(tags or "")
        weights = [_SOURCE_QUALITY_WEIGHT.get(RULE_SOURCE_QUALITY.get(x, "Secondary"), 0.6)
                   * legs.get(x, 1.0)
                   for x in elig] or [0.3]
        # rule10_instruments is the gate's own authority — imported, never copied, so
        # the evidence count and the firing count cannot diverge.
        return max(len(rule10_instruments(elig)), 1), weights
    q = RULE_SOURCE_QUALITY.get(rule, "Secondary")
    return 1, [_SOURCE_QUALITY_WEIGHT.get(q, 0.6)]


def score_alert_fields(conn, rule: str, ticker: str, headline: str,
                       tags: str = "", conflict_score=None) -> dict:
    """Compute the full Phase-2 score set for one alert from live DB context."""
    anchor = (ticker or (headline or "")[:30]) or rule
    novelty = calculate_novelty_score(rule, anchor, conn)
    horizon = assign_time_horizon(rule)
    quality = RULE_SOURCE_QUALITY.get(rule, "Secondary")
    drc, sq_scores = _distinct_rule_count(rule, tags)
    has_conflict = bool(conflict_score and float(conflict_score) > 0.3)
    return {
        "novelty_score":       novelty,
        "time_horizon":        horizon,
        "source_quality":      quality,
        "evidence_confidence": calculate_evidence_confidence(drc, sq_scores, has_conflict),
        "opportunity_score":   calculate_opportunity_score(novelty, 0.0, horizon),
    }


def enrich_alert_scores(conn, only_unscored: bool = True) -> int:
    """
    Compute and store Phase-2 scores for alerts. When only_unscored, targets rows
    still at schema defaults (opportunity_score=0 AND evidence_confidence=0) — so
    it is cheap to run frequently and picks up every newly-inserted alert
    regardless of which rule script wrote it. Returns the number updated.
    """
    where = ("WHERE COALESCE(opportunity_score,0)=0 AND COALESCE(evidence_confidence,0)=0"
             if only_unscored else "")
    rows = conn.execute(
        f"SELECT id, rule, ticker, headline, tags, conflict_score FROM alerts {where}"
    ).fetchall()
    n = 0
    for r in rows:
        # Canonicalize the ticker in the same pass so legacy path-(b) rows (which
        # INSERT raw, without normalize_ticker) end up behaviorally equivalent to
        # path-(a) rows for downstream corroboration (RULE_10 / RULE_CLUSTER).
        norm_ticker = normalize_ticker(r["ticker"])
        s = score_alert_fields(conn, r["rule"] or "", norm_ticker or "",
                               r["headline"] or "", r["tags"] or "",
                               r["conflict_score"] if "conflict_score" in r.keys() else None)
        conn.execute(
            """UPDATE alerts SET ticker=?, novelty_score=?, time_horizon=?, source_quality=?,
                   evidence_confidence=?, opportunity_score=? WHERE id=?""",
            (norm_ticker, s["novelty_score"], s["time_horizon"], s["source_quality"],
             s["evidence_confidence"], s["opportunity_score"], r["id"]),
        )
        n += 1
    conn.commit()
    return n


def normalize_existing_tickers(conn) -> int:
    """One-pass historical backfill: canonicalize alerts.ticker for every row
    whose stored value differs from normalize_ticker(ticker). Ticker-only — does
    NOT rescore, so detection-time novelty/opportunity scores are left intact.
    Returns the number of rows whose ticker changed."""
    rows = conn.execute(
        "SELECT id, ticker FROM alerts WHERE ticker IS NOT NULL AND ticker != ''"
    ).fetchall()
    changed = 0
    for r in rows:
        norm = normalize_ticker(r["ticker"])
        if norm != r["ticker"]:
            conn.execute("UPDATE alerts SET ticker=? WHERE id=?", (norm, r["id"]))
            changed += 1
    conn.commit()
    return changed


def congress_day_digest(conn, day=None, limit=None) -> dict:
    """Below-threshold congressional trade digest, grouped by ticker with member
    count and buy/sell mix. Shared by the morning brief's 'Yesterday in Congress'
    section (day=None → rolling last 24h, limit=10) and the standalone
    /congress/digest/<date> view (day='YYYY-MM-DD', full list). Keyed on
    created_at (disclosure/ingestion time), matching the brief."""
    if day:
        window, tot_window, params = "date(t.created_at) = ?", "date(created_at) = ?", [day]
    else:
        window = "t.created_at >= datetime('now','-24 hours')"
        tot_window = "created_at >= datetime('now','-24 hours')"
        params = []
    lim = f" LIMIT {int(limit)}" if limit else ""
    rows = conn.execute(
        f"""SELECT COALESCE(tk.symbol, t.raw_ticker_string) AS ticker,
                  COUNT(DISTINCT t.member_id) AS members,
                  SUM(CASE WHEN t.transaction_type='purchase' THEN 1 ELSE 0 END) AS buys,
                  SUM(CASE WHEN t.transaction_type IN ('sale','sale_partial') THEN 1 ELSE 0 END) AS sells,
                  COUNT(*) AS txns
           FROM transactions t
           LEFT JOIN tickers tk ON t.ticker_id = tk.id
           WHERE {window}
             AND t.member_id IS NOT NULL AND t.member_id != 'None'
             AND COALESCE(tk.symbol, t.raw_ticker_string) IS NOT NULL
             AND COALESCE(tk.symbol, t.raw_ticker_string) != ''
           GROUP BY COALESCE(tk.symbol, t.raw_ticker_string)
           ORDER BY members DESC, txns DESC{lim}""",
        params,
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) n FROM transactions WHERE {tot_window} "
        "AND member_id IS NOT NULL AND member_id != 'None'", params,
    ).fetchone()["n"]
    return {"date": day, "rows": [dict(r) for r in rows], "total_txns": total}


def annotation_counts(conn, alert_ids=None) -> dict:
    """Backend aggregate: {alert_id: {'up': n, 'down': n}} in ONE grouped query
    (never per-card). Counts are for analysis only — not surfaced to the feed."""
    if alert_ids:
        ph = ",".join("?" * len(alert_ids))
        rows = conn.execute(
            f"SELECT alert_id, annotation, COUNT(*) c FROM alert_annotations "
            f"WHERE alert_id IN ({ph}) GROUP BY alert_id, annotation", list(alert_ids)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT alert_id, annotation, COUNT(*) c FROM alert_annotations "
            "GROUP BY alert_id, annotation"
        ).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["alert_id"], {"up": 0, "down": 0})[r["annotation"]] = r["c"]
    return out


def annotation_daily_summary(conn) -> str:
    """One-line summary of today's annotation activity for a daily job note."""
    row = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN annotation='up' THEN 1 ELSE 0 END) ups,
                  SUM(CASE WHEN annotation='down' THEN 1 ELSE 0 END) downs
           FROM alert_annotations
           WHERE date(annotated_at) = date('now')"""
    ).fetchone()
    total = row["total"] or 0
    return f"annotations_today={total} (up={row['ups'] or 0}, down={row['downs'] or 0})"


def normalize_ticker(ticker):
    """
    Canonicalize an equity symbol so variants collapse to one form before
    storage/corroboration counting. Strips '$', uppercases, and standardizes the
    class-share separator to a dot (BRK-B / brk.b -> BRK.B), so cross-source
    matching in RULE_10 doesn't split the same company.

    Returns None/empty unchanged (RULE_08/RULE_09 legitimately have no ticker).
    Multi-symbol strings (space-separated) are normalized token-wise, not merged.
    """
    if not ticker:
        return ticker
    raw = str(ticker).replace("$", "").strip().upper()
    if not raw:
        return None
    tokens = raw.split()
    # US class shares use '.' or '-' interchangeably across feeds -> pick dot.
    tokens = [t.replace("-", ".") for t in tokens]
    return " ".join(tokens)


def insert_alert(conn, rule, ticker, severity, headline, why_matters=None,
                 tags=None, member_id=None, source_url=None, verify_url=None,
                 detail=None, event_date=None, theme_id=None,
                 distinct_rule_count=None, has_conflict=False,
                 absorption_pct=0.0, novelty_key=None) -> int:
    """
    Single entry point for alert inserts that computes Phase-2 scores inline and
    normalizes the ticker (the one write point where canonicalization happens).
    Alerts inserted by other paths are still scored by enrich_alert_scores() on
    the scheduler. Returns the new row id.

    novelty_key: overrides the string novelty is computed against (default: the
    ticker). RULE_CLUSTER passes a cluster-identity fingerprint so novelty counts
    prior occurrences of *that cluster*, not ticker mentions overall. For it to
    match, the caller must also embed the same fingerprint in headline/why_matters
    (calculate_novelty_score does a LIKE match on those fields).
    """
    import json as _json
    ticker = normalize_ticker(ticker)
    anchor = novelty_key or (ticker or (headline or "")[:30]) or rule
    novelty = calculate_novelty_score(rule, anchor, conn)
    horizon = assign_time_horizon(rule)
    quality = RULE_SOURCE_QUALITY.get(rule, "Secondary")
    if distinct_rule_count is None:
        distinct_rule_count, sq_scores = _distinct_rule_count(rule, tags if isinstance(tags, str) else "")
    else:
        sq_scores = [_SOURCE_QUALITY_WEIGHT.get(quality, 0.6)]
        # ⚠️ THE CALLER'S COUNT IS AUTHORITATIVE; THE PER-LEG WEIGHTS STILL APPLY. RULE_10
        # passes the gate's own instrument count explicitly (pinned by
        # test_evidence_confidence_instruments.py, written after a mutation swapped it back
        # to the rule count), and this branch used to discard `tags.leg_weights` entirely —
        # so a routine mega-cap contract quietly moved the THEME's score while the ALERT's
        # stayed at 46.0. The alert and its own theme scored the same convergence
        # differently, and `mode=overwatch` sorts by the alert.
        #
        # Applied as a MULTIPLIER on the existing quality term rather than by adopting
        # `_distinct_rule_count`'s per-leg list, deliberately: adopting the list would move
        # RULE_10's *baseline* from 46.0 to 60.0 for every new corroboration and re-rank the
        # whole backlog, which is a formula-shape change needing its own sign-off. With no
        # weights recorded the mean is 1.0 and the score is bit-for-bit what it was.
        _legs = _leg_weights_from_tags(tags if isinstance(tags, str)
                                       else _json.dumps(tags) if isinstance(tags, dict) else "")
        if _legs:
            _elig = rule10_eligible_rules(rule10_rules_from_tags(
                tags if isinstance(tags, str)
                else _json.dumps(tags) if isinstance(tags, dict) else ""))
            _per_leg = [_legs.get(r, 1.0) for r in _elig] or [1.0]
            sq_scores = [sq_scores[0] * (sum(_per_leg) / len(_per_leg))]
    evidence = calculate_evidence_confidence(distinct_rule_count, sq_scores, has_conflict)
    opportunity = calculate_opportunity_score(novelty, absorption_pct, horizon)
    tags_str = _json.dumps(tags) if isinstance(tags, dict) else tags
    cur = conn.execute(
        """INSERT INTO alerts (
               rule, ticker, severity, headline, why_matters, tags, member_id,
               source_url, verify_url, detail, event_date, theme_id,
               novelty_score, absorption_pct, time_horizon,
               evidence_confidence, opportunity_score, source_quality, created_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))""",
        (rule, ticker, severity, headline, why_matters, tags_str, member_id,
         source_url, verify_url, detail, event_date, theme_id,
         novelty, absorption_pct, horizon, evidence, opportunity, quality),
    )
    conn.commit()
    return cur.lastrowid


def record_activity(source, scanned=0, flagged=0, emitted=0,
                    duration_seconds=None, notes=None) -> None:
    """log_activity on a fresh, self-managed connection — the one-liner rule
    scripts call at the end of run() (safe even if their own conn is closed)."""
    try:
        conn = db_connection()
        log_activity(conn, source, scanned=scanned, flagged=flagged,
                     emitted=emitted, duration_seconds=duration_seconds, notes=notes)
        conn.close()
    except Exception:
        pass


def log_activity(conn, source, scanned=0, flagged=0, emitted=0,
                 duration_seconds=None, notes=None) -> None:
    """Record a rule/scan run so the activity log can show 'clear airspace'."""
    try:
        conn.execute(
            """INSERT INTO activity_log
               (source, events_scanned, events_flagged, alerts_emitted, duration_seconds, notes)
               VALUES (?,?,?,?,?,?)""",
            (source, int(scanned or 0), int(flagged or 0), int(emitted or 0),
             duration_seconds, notes),
        )
        conn.commit()
    except Exception:
        pass


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


def contractor_attribution_is_exact(name: str) -> bool:
    """Did this recipient name resolve via the CURATED table, rather than token matching?

    ⚠️ THE CONFIDENCE NUMBER CANNOT ANSWER THIS, which is exactly why the helper exists.
    `resolve_contractor`'s token-containment fallback hardcodes `conf = 90`
    (:1473) while the curated overrides carry 80-99 — so the two paths are
    indistinguishable by confidence, and no threshold can separate them.

    The token path is genuinely unreliable and has a LIVE false positive: `SPCX` →
    "SPACE EXPLORATION TECHNOLOGIES CORP". SpaceX is private; the ticker belongs to an
    unrelated listed vehicle whose `tickers.company_name` happens to read the same way,
    and all four SEC share concepts 404 for its CIK. The same path mapped RAYTHEON to
    HNST once before, which is why migrations m003/m004 exist.

    That matters for the cap-relative contract weight and nowhere else: a wrong ticker
    plus a perfectly plausible market cap yields a confident, wrong ratio, and no
    plausibility check can catch it because both inputs are individually fine. So the
    weight may only be RAISED when this returns True. Returning a bare bool rather than
    extending `resolve_contractor`'s return shape is deliberate — `test_trust_fixes.py`
    asserts that tuple exactly.
    """
    low = _norm_company(name)
    if not low:
        return False
    return any(key in low for key in CONTRACTOR_OVERRIDES)


# Award-value-to-market-cap bands. The idea is SURPRISE RELATIVE TO THE ENTITY'S OWN
# SIZE: a $50M award is a Tuesday for Lockheed and transformative for a micro-cap.
CONTRACT_RATIO_ROUTINE = 0.01     # <1% of market cap — routine for this recipient
CONTRACT_RATIO_MATERIAL = 0.10    # >=10% — materially large relative to the company
CONTRACT_WEIGHT_ROUTINE = 0.35    # still counts as the `contracts` instrument, quietly
CONTRACT_WEIGHT_NEUTRAL = 1.0     # "no opinion" — every fail-closed path lands here
CONTRACT_WEIGHT_MATERIAL = 1.25   # the ONLY value above neutral, and the hard ceiling


def contract_leg_weight(conn, ticker: str, award_key: str) -> float:
    """How much should a contracts leg actually COUNT for? 1.0 = neutral / no opinion.

    ⚠️ THIS MOVES THE SCORE AND NEVER THE INSTRUMENT COUNT. `calculate_evidence_confidence`
    steps on an INTEGER (>=3 -> 40, >=4 -> 60, >=5 -> 75), so a fractional count of 2.7
    would fall below the first tier and score base 0 — the 6.0-vs-20.0 regression this
    project already fixed once. The weight therefore enters through the per-leg quality
    average instead, and a routine mega-cap award still counts as the `contracts`
    instrument. Mega-cap awards are NOT dropped.

    ⚠️ ASYMMETRIC BY DESIGN — this is the integrity property. The weight may fall freely,
    because calling an award routine is a CONSERVATIVE claim that is safe even on a
    mis-attributed ticker. It may never rise above neutral, and the ceiling is enforced
    here rather than trusted to the bands: a mis-attributed small-cap must not be able to
    make a bogus signal look STRONGER. See `contractor_attribution_is_exact`.

    ⚠️ FAILS CLOSED TO NEUTRAL, NOT TO ZERO. An unknown cap must not fabricate a ratio,
    and must not silently delete a leg's contribution either. Unknown means "no opinion".

    Caveat on the numerator, recorded rather than hidden: `contracts.amount` is the award's
    TOTAL OBLIGATED VALUE TO DATE across all modifications of the PIID
    (`scripts/rule_11_contracts.py:35-42`), not strictly the new-award value, so the ratio
    can overstate. The sweep uses `new_awards_only`, which keeps the two close but does not
    make them identical.
    """
    if not ticker or not award_key:
        return 1.0
    try:
        row = conn.execute(
            "SELECT amount, recipient_name FROM contracts WHERE award_id = ? "
            "ORDER BY amount DESC LIMIT 1", (award_key,)).fetchone()
    except Exception:
        return 1.0
    if not row or not row[0]:
        return 1.0
    amount, recipient = float(row[0]), (row[1] or "")
    if amount <= 0:
        return 1.0

    # ⚠️ A CAP IS USED ONLY IF SOMETHING ELSE HAS ALREADY RESOLVED ONE. The gate is the
    # caller here, and before this feature RULE_10 made ZERO network requests. Calling
    # `market_cap` on a cold cache resolves live (SEC shares outstanding + a Yahoo close),
    # so a cold run of up to `MAX_PER_RUN` convergences could have fired dozens of requests
    # inside the scheduler's 300s budget — the failure class `scheduler-reliability` exists
    # for. Requiring an existing cache row means the gate can only ever REUSE a cap warmed
    # by the collector (4x daily) or by `/api/tickers/{sym}/meta`; a name nobody has priced
    # yet is simply "no opinion", which is what every other unknown here means. TTL and the
    # plausibility self-heal stay inside `market_cap` rather than being reimplemented — the
    # divergence this codebase keeps being bitten by.
    try:
        if not conn.execute(
            "SELECT 1 FROM ticker_meta WHERE symbol = ? AND market_cap IS NOT NULL",
            (ticker,),
        ).fetchone():
            return 1.0
    except Exception:
        return 1.0

    try:
        # The RAW INTEGER, not `classify_cap`. `_band` answers "should the collector skip
        # this name" and returns only small|excluded|unknown, so a $131B Lockheed and a
        # $4.9T Apple are both "excluded" — the wrong shape for a ratio.
        #
        # ⚠️ `sys.path` IS MUTATED AT MOST ONCE, not on every call. The first version did an
        # unconditional `sys.path.insert` inside this function; a verification pass measured
        # `sys.path` growing 6 → 207 entries over 200 calls. Bounded today only because the
        # gate runs as a short-lived subprocess capped at 10 emissions — unbounded the moment
        # the FastAPI process calls this.
        _scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        from rule_reddit_collector import market_cap
        cap = market_cap(conn, ticker)
    except Exception:
        return 1.0
    if not cap or cap <= 0:
        return 1.0

    ratio = amount / cap
    if ratio < CONTRACT_RATIO_ROUTINE:
        # Routine for this recipient. Allowed on ANY resolved ticker: under-weighting a
        # mis-attributed award is harmless, so the conservative direction needs no proof.
        return CONTRACT_WEIGHT_ROUTINE
    if ratio >= CONTRACT_RATIO_MATERIAL:
        # ⚠️ THE ONLY PATH THAT RISES ABOVE NEUTRAL, AND THE ONLY ONE THAT NEEDS PROOF OF
        # WHO THE RECIPIENT IS. A wrong ticker plus a plausible cap gives a confident wrong
        # ratio that no plausibility check can catch, so a token-matched recipient gets
        # neutral — it may fail to be boosted, it may never be boosted wrongly. This is
        # what stops the live `SPCX` -> "SPACE EXPLORATION TECHNOLOGIES CORP" false positive
        # from turning a $3B award against a private company into extra confidence.
        return (CONTRACT_WEIGHT_MATERIAL if contractor_attribution_is_exact(recipient)
                else CONTRACT_WEIGHT_NEUTRAL)
    # In between: linear from routine up to NEUTRAL — never past it. Only the explicit
    # material band above can exceed neutral, so the interpolation needs no attribution
    # proof either.
    span = CONTRACT_RATIO_MATERIAL - CONTRACT_RATIO_ROUTINE
    frac = (ratio - CONTRACT_RATIO_ROUTINE) / span
    return round(CONTRACT_WEIGHT_ROUTINE
                 + frac * (CONTRACT_WEIGHT_NEUTRAL - CONTRACT_WEIGHT_ROUTINE), 4)


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


def _running_under_test() -> bool:
    """True when this process was launched by the test suite.

    Two entry points matter: `pytest` (sets PYTEST_CURRENT_TEST once a test is
    executing) and the documented standalone form `python3 tests/test_x.py`
    (argv[0] sits directly inside a `tests/` directory). Production entry points
    — uvicorn and the scheduler's `scripts/rule_*.py` subprocesses — match
    neither.
    """
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    argv0 = sys.argv[0] if sys.argv else ""
    if not argv0:
        return False
    try:
        return Path(argv0).resolve().parent.name == "tests"
    except (OSError, ValueError):
        return False


def _get_db_path(explicit: Optional[str]) -> Path:
    """
    Resolve the database file path.

    Priority:
    1. Explicit argument
    2. DATABASE_PATH env var
    3. Railway persistent volume (/app/data) if the directory exists
    4. Local ./data/jpt.db fallback

    Steps 3 and 4 are refused when running under the test suite: a test that
    reaches them would read and write the real database. Tests must be pointed
    at a disposable DB via DATABASE_PATH — `tests/conftest.py` does this
    automatically for every test.
    """
    load_dotenv()

    if explicit:
        return Path(explicit)

    env_path = os.getenv("DATABASE_PATH", "").strip()
    if env_path:
        return Path(env_path)

    if _running_under_test():
        raise RuntimeError(
            "Refusing to open the real database from a test.\n"
            "DATABASE_PATH is not set, so this call would read and write the "
            "live DB (Railway volume or Scope/data/jpt.db).\n"
            "Run the suite with pytest (tests/conftest.py provisions a fresh "
            "temp DB per test), or set DATABASE_PATH to a disposable file "
            "before running a test module directly."
        )

    if _RAILWAY_VOLUME.is_dir():
        return _RAILWAY_VOLUME / "jpt.db"

    return Path(__file__).resolve().parent / "data" / "jpt.db"


def _backup_db(db_file: Path) -> None:
    """RETIRED — intentionally a no-op. Kept so `db_connection()` needn't change.

    This used to `shutil.copy2` the live DB once an hour, from inside
    `db_connection()`. Two problems, both now removed by doing nothing:

    1. **A raw file copy of a live SQLite DB can capture a torn write** — a page
       caught mid-transaction. The resulting `jpt_*.db` file looks like a backup
       and was never integrity-checked, so it could fail exactly when relied on.
       `scripts/db_backup.py` supersedes it: SQLite's online backup API,
       `PRAGMA integrity_check` **before** the snapshot is kept, gzip, and tiered
       retention. That job now runs hourly, so no coverage is lost by retiring
       this.
    2. **It ran unguarded in the hot path.** Any exception here — a full disk, a
       permissions problem — propagated out of `db_connection()` and would have
       taken down every caller, i.e. the whole app, for a *backup* failure.

    Existing `jpt_*.db` files on the volume are left alone; RESTORE.md still
    documents how to fall back to one if that is ever all you have.
    """
    return


def db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Return a SQLite connection for the project database.

    On Railway the persistent volume at /app/data is used automatically.
    Locally falls back to DATABASE_PATH env var or ./data/jpt.db.
    Backups are NOT taken here — see _backup_db, which is retired. The verified
    hourly snapshot is scripts/db_backup.py.
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
    # RULE_16 = 1.4 — SIGNED OFF BY THE HUMAN 2026-07-27. This is a SCORING change, which
    # CLAUDE.md human-gates; it was surfaced with the peer comparison below and approved
    # explicitly rather than carried in on the back of the gate map entry.
    # Peer comparison as presented for that decision:
    #     RULE_10 2.0 (Derived) | RULE_06 1.5 | RULE_12 1.4 | RULE_13 1.4
    #     RULE_09 1.3 | RULE_14 1.2 | RULE_15 1.2 (Secondary)
    # Primary-source peers are [1.2, 1.3, 1.4, 1.4, 1.5], median 1.4.
    # Caveat recorded at sign-off: five Primary rules (RULE_01B, RULE_02, RULE_08,
    # RULE_11, RULE_CLUSTER) have no entry here and run at an effective 1.0, so "peers"
    # is a post-hoc set — counting them the median would be 1.1, not 1.4.
    # RATIONALE (accepted): keep 1.4 — it is the Primary median, it sits below RULE_06 (1.5,
    # an insider's own trade, a stronger signal than an external manager's quarterly
    # report), and above RULE_14/15 (1.2). A defensible fallback if you disagree is 1.3,
    # matching RULE_09, since both are periodic institutional disclosures.
    # `RULE_SOURCE_QUALITY["RULE_16"] = "Primary"` is also a scoring input and is
    # justified: a 13F-HR is an authoritative SEC filing, same class as Form 4.
    "RULE_16": 1.4,
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
