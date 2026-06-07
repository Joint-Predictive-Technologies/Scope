# Contributing to Scope

This doc is primarily for the co-founder / technical collaborator. Read [`docs/phase_zero_spec.md`](docs/phase_zero_spec.md) first — it covers the product philosophy, the 10 rules, the user persona, and the regulatory posture. Don't skip it.

---

## Where we are

Rule 1 (STOCK Act ingestion) is partially live:

- **Senate ingestion** — end-to-end real. Handles the eFD disclaimer session, paginates, parses PTR HTML, stores to SQLite, emits alerts with severity scoring.
- **House ingestion** — front half only. Downloads the annual XML index, filters to PTRs, registers each with `extraction_status='pending'`. **PDF parsing is not written yet.**
- **Rules 2–10** — not started.
- **Frontend** — `index.html` is a static landing page stub. No live data connection.

---

## What to work on — priority order

### 1. House PDF parser `scripts/parse_house_pdfs.py` ← biggest piece of real work

This is the next deliverable. The index registration is done; we have 515 PTR filings sitting at `extraction_status='pending'` waiting for a parser.

**Approach:**
- Use `pdfplumber` for text extraction
- Fall back to `pytesseract` (OCR) when text yield is empty or low-confidence
- Output the same dict shape as `parse_filing_html` in `ingest_senate.py` so it plugs into the same storage layer without changes
- Update each filing's `extraction_status` from `pending` to `parsed_ok` / `parsed_low_confidence` / `parse_failed`

**Before declaring done:** iterate against a corpus of 20–30 representative real PDFs. They vary a lot in layout. Don't trust a parser that only works on 5 examples.

House PTR PDF URL pattern: `/public_disc/ptr-pdfs/<year>/<doc_id>.pdf`

### 2. Ticker resolution

`ticker_id` on transactions is always NULL right now. The raw string is preserved but not resolved.

**Approach:**
- Bootstrap `tickers` table from SEC EDGAR's CIK→ticker mapping file
- Match `raw_ticker_string` against `symbol` exact
- Match `raw_description` against `company_name` with trigram-style fuzzy matching
- Punt to NULL with confidence dropped if ambiguous
- Don't over-engineer this — exact match covers ~80% of cases

### 3. Rule 2 — Cluster trade detection

First rule beyond Rule 1. Requires: ticker resolution to be done (or functional enough), a query that aggregates PTRs by ticker over a 7-day window, and an alert emitter for clusters of 3+.

Slow path — no speed requirement. Runs as a post-ingestion aggregation step.

### 4. Postgres port

When ready: apply `schema_v0.1.sql` (Postgres dialect), swap `jpt_common.db_connection()` to psycopg or SQLAlchemy. Generated columns on `transactions` can move out of application code at that point.

Not urgent. SQLite is fine for development and initial testing.

---

## Code conventions

The existing code is structured to be readable, not opinionated. You're free to:

- Add async if you want it (sync is fine at current scale — ingestion runs against ~5–20 new filings per hour)
- Add a type checker config (`mypy`, `pyright`) — the existing code has type hints throughout
- Choose a test framework — `pytest` is the obvious default; the `tests/` directory is empty
- Restructure project layout — the current flat `scripts/` structure will need to become a proper package as Rules 2–10 are added
- Add logging library of your choice — current code uses `print` for simplicity

Don't rewrite working ingestion code without a reason. Rule 1 Senate ingestion has been run against live data and verified. Treat it as stable unless you find a bug.

---

## Environment setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in CONGRESS_API_KEY at minimum to run bootstrap_members.py
mkdir -p data
```

## Running the ingestion

```bash
python scripts/bootstrap_members.py        # needs CONGRESS_API_KEY
python scripts/ingest_senate.py --since 2025-01-01 --emit-alerts
python scripts/ingest_house_index.py --year 2025
```

## Inspecting the database

```bash
sqlite3 data/jpt.db
> .tables
> SELECT headline, severity, tags FROM alerts ORDER BY severity DESC LIMIT 20;
> SELECT COUNT(*) FROM transactions WHERE extraction_status = 'pending';
```

---

## Architecture decisions already made

These are settled. Don't relitigate without a strong reason:

- **Python + SQLite (→ Postgres later)** — right tool for this workload. Ingestion is I/O bound, not compute bound.
- **Anthropic API for LLM layer** — Claude for summarization and entity extraction on slow-path rules. Not for prediction.
- **No async in the ingestion layer yet** — sync is fine at current scale. Add it when it's actually needed.
- **No retries with backoff wired up** — the skeleton structure is there in `jpt_common.py`. Wire it in once we're running in production.
- **Cron for scheduling** — no daemon. Run on cron until there's a reason for a daemon.
- **No auth, billing, or frontend wiring** — separate concerns, separate phase.

---

## Open questions (your input needed)

1. **Test framework** — pytest is the obvious default. Any preference?
2. **Virtualenv enforcement** — add a `pyproject.toml` / `poetry.lock`? Or keep it requirements.txt?
3. **Async** — do you want to start async from the beginning, or add it when ingestion volume requires it?
4. **X API access** — Rule 5 (watched senator accounts) requires X API. It's expensive. Worth evaluating the cost before committing. Alternatives: scraping (fragile), third-party aggregators.
5. **Postgres timing** — when do we want to make the switch? Before or after Rule 2 is working?
