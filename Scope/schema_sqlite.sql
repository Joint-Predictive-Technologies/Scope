CREATE TABLE IF NOT EXISTS members (
    bioguide_id TEXT PRIMARY KEY,
    full_name   TEXT NOT NULL,
    party       TEXT,
    state       TEXT,
    chamber     TEXT,
    district    TEXT,
    is_current  INTEGER DEFAULT 1,
    url         TEXT,
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tickers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL UNIQUE,
    company_name TEXT,
    cik          TEXT,
    -- updated_at is what makes staleness detectable, and resolve_tickers' upsert
    -- REQUIRES it. This file omitted it while resolve_tickers.ensure_tables declared
    -- it, so on any DB where db_connection() ran first the CREATE ... IF NOT EXISTS
    -- there became a no-op and every upsert died with "no column named updated_at".
    -- Fixed at the source here; resolve_tickers keeps a PRAGMA-guarded ADD COLUMN for
    -- databases created before this line existed.
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS filings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source           TEXT,
    member_id        TEXT REFERENCES members(bioguide_id),
    doc_id           TEXT UNIQUE,
    filing_date      TEXT,
    report_type      TEXT,
    extraction_status TEXT DEFAULT 'pending',
    raw_url          TEXT,
    ingested_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    filing_id          INTEGER REFERENCES filings(id),
    member_id          TEXT REFERENCES members(bioguide_id),
    ticker_id          INTEGER REFERENCES tickers(id),
    raw_ticker_string  TEXT,
    raw_description    TEXT,
    transaction_type   TEXT,
    amount_band        TEXT,
    transaction_date   TEXT,
    filing_date        TEXT,
    created_at         TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rule       TEXT,
    headline   TEXT,
    severity   TEXT,
    tags       TEXT,
    ticker     TEXT,
    member_id  TEXT,
    detail     TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    filings_seen INTEGER DEFAULT 0,
    errors       INTEGER DEFAULT 0,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT NOT NULL UNIQUE,
    added_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ticker_meta (
    symbol       TEXT PRIMARY KEY,
    market_cap   INTEGER,
    cap_updated  TEXT,
    social_spike INTEGER DEFAULT 0,
    social_at    TEXT
);

CREATE TABLE IF NOT EXISTS price_action (
    symbol      TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    start_price REAL,
    end_price   REAL,
    pct_change  REAL,
    fetched_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (symbol, start_date, end_date)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    alert_id   INTEGER PRIMARY KEY,
    ticker     TEXT,
    price_at   REAL,
    price_7d   REAL,
    price_30d  REAL,
    return_7d  REAL,
    return_30d REAL,
    fetched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reddit_posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     TEXT UNIQUE,
    subreddit   TEXT,
    title       TEXT,
    ticker      TEXT,
    upvotes     INTEGER,
    url         TEXT,
    ingested_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS digests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start   TEXT UNIQUE,
    content      TEXT,
    generated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alert_votes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(alert_id, session_id)
);

CREATE TABLE IF NOT EXISTS alert_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS telegram_pushes (
    alert_id  INTEGER PRIMARY KEY,
    pushed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contracts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_name TEXT NOT NULL,
    ticker         TEXT,
    amount         REAL,
    agency         TEXT,
    award_date     TEXT,
    award_id       TEXT,
    description    TEXT,
    ingested_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(recipient_name, award_date)
);

CREATE TABLE IF NOT EXISTS daily_briefs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT UNIQUE,
    content_json  TEXT,
    generated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS gdelt_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT UNIQUE,
    ingested_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watchlist_rules (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    label            TEXT NOT NULL,
    condition_type   TEXT NOT NULL,
    condition_value  TEXT NOT NULL,
    created_at       TEXT DEFAULT (datetime('now'))
);
