# Scope — Political & Insider Activity Terminal

A research terminal for macro and event-driven investors. Watches congressional trades, executive social media, hearing schedules, regulatory filings, and prediction-market positioning — then surfaces signal-bearing events with LLM-generated context against your watchlist.

**You keep the judgment. Scope keeps the watch.**

---

## Current build status

| Component | Status |
|---|---|
| Senate PTR ingestion (Rule 1) | ✅ Live — end-to-end real |
| House PTR index registration (Rule 1) | ✅ Live — front half only |
| House PTR PDF parser | 🔲 Next deliverable |
| Ticker resolution | 🔲 Pending |
| Rules 2–10 | 🔲 Planned |
| Frontend | 🔲 `index.html` stub — not wired to data |
| Postgres port | 🔲 Planned (currently SQLite) |

---

## The 10 Rules

Each rule is a concrete "if this, then surface that" unit. The product ships all ten at launch. Rule 10 — cross-source corroboration — is what justifies the product existing.

| # | Name | Domain | Tier |
|---|---|---|---|
| 1 | STOCK Act new filing | Congressional | Fast |
| 2 | Cluster trade detection | Congressional | Slow |
| 3 | Trump Truth Social ticker mention | Political | Fast |
| 4 | Senate hearing — public company witness | Political | Slow |
| 5 | Watched senator/rep posts on policy | Political | Fast + Context |
| 6 | Executive insider trade — deviation flag | Insider | Slow |
| 7 | Polymarket significant position move | Prediction markets | Fast |
| 8 | Federal Register sector-impact rule | Regulatory | Slow |
| 9 | Lobbying disclosure spike | Regulatory | Slow |
| 10 | **Cross-source corroboration alert** | All | Slow |

Full spec: [`docs/phase_zero_spec.md`](docs/phase_zero_spec.md)

---

## Repo layout

```
scope/
├── index.html                     Landing page
├── docs/
│   ├── phase_zero_spec.md         Full product spec (read this first)
│   └── architecture.md            Technical architecture notes
├── scripts/
│   ├── schema_sqlite.sql          DB schema — SQLite dialect
│   ├── jpt_common.py              Shared types, DB helpers, maps
│   ├── bootstrap_members.py       (1) Populate `members` from Congress.gov
│   ├── ingest_senate.py           (2) Senate eFD PTR ingestion — full
│   └── ingest_house_index.py      (3) House XML index — front half
├── data/                          Created on first run — gitignored
│   └── jpt.db
└── tests/                         Framework TBD
```

---

## Setup

```bash
pip install requests beautifulsoup4 lxml
mkdir -p data
cp .env.example .env
# Fill in your API keys in .env
```

No virtualenv enforcement — your call.

## Running

```bash
# 1. Get a Congress.gov key (free, ~60 seconds):
#    https://api.congress.gov/sign-up/
export CONGRESS_API_KEY=your_key_here
python scripts/bootstrap_members.py

# 2. Ingest Senate PTRs (real, works today):
python scripts/ingest_senate.py --since 2025-01-01 --emit-alerts

# 3. Register House PTR filings (index only — no PDFs yet):
python scripts/ingest_house_index.py --year 2025

# 4. Inspect:
sqlite3 data/jpt.db
> SELECT headline, severity, tags FROM alerts ORDER BY severity DESC LIMIT 10;
```

---

## Verified working

- `ingest_senate.py` run live against `efdsearch.senate.gov` on 2025-12 PTRs. Three filings fetched, two parsed into 11 transactions, 11 alerts emitted, one paper PTR correctly flagged `manual_review`.
- `ingest_house_index.py` run live against the 2025 House index zip. 2,394 total entries parsed, 515 identified as PTRs ready for PDF-parsing phase.

---

## Notes

- Senate eFD `report_type` IDs: **11 = PTR**, **7 = Annual**. Easy to confuse reading older blog posts.
- House PTR PDF URL pattern: `/public_disc/ptr-pdfs/<year>/<doc_id>.pdf` — verified working.
- Paper PTRs (URLs containing `/view/paper/`) are scanned images. Stored as `extraction_status='manual_review'`, not skipped.
- Amount bands are stored as enum values. Do not compute midpoints and treat them as numbers.

---

## Legal

This is an information aggregation and research tool. It is not investment advice and does not provide personalized recommendations. All data sourced from public filings and open sources. See [`docs/phase_zero_spec.md`](docs/phase_zero_spec.md) §8 for regulatory posture.
