# Architecture — Scope

Technical reference for the data pipeline, rule engine, and planned system components.

---

## System overview

```
Data sources ──► Ingestion layer ──► SQLite / Postgres ──► Rule engine ──► Alert store ──► Frontend / Push
```

Everything is currently synchronous and single-process. That's fine for the current scale (~5–20 new filings per hour). The architecture is designed to grow into async and distributed without a rewrite.

---

## Data domains and sources

### Domain A — Congressional & insider

| Source | How accessed | Status |
|---|---|---|
| Senate eFD (PTRs) | HTTP + session handling (no API) | ✅ Live |
| House disclosure XML index | ZIP download from house.gov | ✅ Live (index only) |
| House PTR PDFs | HTTP download, per-filing | 🔲 PDF parser needed |
| SEC Form 4 | EDGAR full-text search API | 🔲 Rule 6 |
| SEC 13F/13D/13G | EDGAR | 🔲 Later |

### Domain B — Political signal

| Source | How accessed | Status |
|---|---|---|
| Truth Social | Scraping (no official API) | 🔲 Rule 3 |
| X / Twitter (tracked accounts) | X API v2 — expensive | 🔲 Rule 5 — evaluate cost first |
| Congressional hearing calendar | house.gov / senate.gov scraping | 🔲 Rule 4 |
| Federal Register | federalregister.gov API (free) | 🔲 Rule 8 |

### Domain C — Prediction markets

| Source | How accessed | Status |
|---|---|---|
| Polymarket | CLOB API (public) | 🔲 Rule 7 |
| Kalshi | REST API (key required) | 🔲 Rule 7 supplement |

---

## Database schema (SQLite current, Postgres planned)

Core tables:

```
members           — congressional members (from Congress.gov)
tickers           — resolved ticker symbols (from EDGAR CIK→ticker)
filings           — raw filing records, one per PTR/Form4/etc.
transactions      — parsed transactions from filings
alerts            — rule-engine output, one per triggered rule
ingestion_runs    — observability — every run logged with counts and errors
```

Key design decisions:
- Amount bands stored as enum values, never as numeric midpoints
- `extraction_status` on filings: `pending` → `parsed_ok` / `parsed_low_confidence` / `parse_failed` / `manual_review`
- `ticker_id` nullable until ticker resolution is built; `raw_ticker_string` always preserved
- Generated columns on `transactions` (computed fields) — currently in application code, can move to DB when Postgres port happens

---

## Rule engine

Each rule is a function with this signature (conceptually):

```python
def rule_N(db: Connection, context: RuleContext) -> list[Alert]:
    ...
```

Rules are run after each ingestion pass. Fast-path rules emit alerts immediately. Slow-path rules queue a secondary processing job (currently synchronous, can be async later).

**Rule 10** runs last — it queries the alerts table for two or more rule fires on the same ticker within 48 hours, then generates a corroboration alert with an LLM-written narrative.

---

## LLM layer

Used only on the slow path. Never for prediction — only for:

- Summarization (hearing transcripts, Federal Register rules, multi-tweet threads)
- Entity extraction (tickers and company names from unstructured text)
- Semantic matching (thesis-driven relevance filtering — the under-the-hood differentiator)
- Narrative generation (Rule 10 "why this is interesting" paragraph)

Model: Anthropic Claude via API. Model selection (Haiku vs Sonnet vs Opus) tuned per use case — Haiku for entity extraction, Sonnet for narrative generation.

---

## Latency tiers

**Fast path** (target: 30 sec – 5 min from source publication)
- Minimal processing, immediate push notification
- Rule 1 (new STOCK Act filing), Rule 3 (Truth Social), Rule 7 (Polymarket move)

**Slow path** (target: 5 – 30 min)
- LLM summarization, cross-referencing, aggregation
- Rules 2, 4, 5 (context), 6, 8, 9, 10

---

## Scheduling

Currently: **cron**. Run each ingestion script on a schedule. No daemon.

```cron
# Every 15 minutes — Senate PTR check
*/15 * * * * cd /path/to/scope && python scripts/ingest_senate.py --emit-alerts

# Daily at 06:00 — House index refresh
0 6 * * * cd /path/to/scope && python scripts/ingest_house_index.py --year 2025
```

---

## Planned components (not yet built)

- **Ticker resolution service** — SEC EDGAR CIK→ticker + fuzzy company name match
- **Push notification layer** — Telegram bot initially; mobile push later
- **Frontend data API** — FastAPI or Flask endpoint serving alerts as JSON to `index.html`
- **User watchlist store** — per-user ticker watchlists and thesis strings for relevance filtering
- **Postgres migration** — when SQLite becomes a bottleneck or multi-process access is needed
- **Rule 10 LLM pipeline** — corroboration narrative generation

---

## What intentionally doesn't exist yet

- Authentication, user accounts, billing
- Retries with backoff (skeleton in `jpt_common.py`, not wired)
- Concurrent / async fetching (sync is fine at this scale)
- Tests (framework TBD)
- Real-time market data feeds (out of scope — too expensive)
