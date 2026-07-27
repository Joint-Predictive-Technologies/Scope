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
RULE_10_EXCLUDED: set[str] = {"RULE_07", "RULE_OSINT", "RULE_ANOMALY", "RULE_REDDIT", "RULE_10"}

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
    """How strongly is the thesis supported? (Not whether opportunity remains.)"""
    base = 0.0
    if distinct_rule_count >= 4:
        base = 40.0
    if distinct_rule_count >= 5:
        base = 60.0
    if distinct_rule_count >= 6:
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
    """(distinct_rule_count, source_quality_weights) for scoring an alert."""
    if rule == "RULE_10":
        elig = rule10_eligible_rules(rule10_rules_from_tags(tags or ""))
        weights = [_SOURCE_QUALITY_WEIGHT.get(RULE_SOURCE_QUALITY.get(x, "Secondary"), 0.6)
                   for x in elig] or [0.3]
        return max(len(elig), 1), weights
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
