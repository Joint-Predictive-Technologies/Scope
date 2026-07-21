# Architecture — Scope

Technical reference for the data pipeline, rule engine, scoring, and deployment.
For engineering ground truth (conventions, the exact rule table, database rules)
see [`Scope/CLAUDE.md`](Scope/CLAUDE.md).

---

## System overview

```
Public data sources ─► Ingestion + rule engine ─► SQLite (scored alerts) ─┬─► FastAPI terminal UI
                              │                                            ├─► Morning Brief / Telegram push
                       APScheduler (in-process, ~30 jobs)                  └─► Outcome tracking (alert_outcomes)
```

Everything runs in-process inside a single FastAPI application deployed on
Railway: the web server and an APScheduler background thread that runs every rule
and maintenance job on a schedule. Rules execute as subprocesses so a crash in one
never takes down the app, and a universal safety net logs every failure.

**Stack:** FastAPI + SQLite + APScheduler, Groq for LLM tasks, deployed on Railway.
(The original Phase-0 plan called for cron scheduling and a Flask/Postgres port;
the system consolidated on this stack instead.)

---

## Data domains and sources

All sources below are **live**. Most require no API key.

### Domain A — Congressional & insider

| Source | How accessed | Status |
|---|---|---|
| Senate eFD (PTRs) | HTTP + session handling (no API) | ✅ Live |
| House disclosure index + PTR PDFs | ZIP index + per-filing PDF (pdfplumber + OCR) | ✅ Live |
| SEC Form 4 | EDGAR full-text search API | ✅ Live (RULE_06) |

### Domain B — Government, regulatory & finance

| Source | How accessed | Status |
|---|---|---|
| Federal Register | federalregister.gov API | ✅ Live (RULE_08) |
| Lobbying (Senate LDA) | LDA API | ✅ Live (RULE_09) |
| Federal contracts | USASpending API | ✅ Live (RULE_11) |
| FARA (foreign agents) | FARA bulk data | ✅ Live (RULE_12) |
| FEC (campaign finance) | api.open.fec.gov | ✅ Live (RULE_13) |
| Patents | PatentsView API | ✅ Live (RULE_14) |

### Domain C — Markets, prediction & OSINT

| Source | How accessed | Status |
|---|---|---|
| Polymarket | Gamma + CLOB API (public) | ✅ Live (RULE_07) |
| GDELT | data.gdeltproject.org | ✅ Live (RULE_OSINT) |
| Reddit | Arctic Shift | ✅ Live (RULE_REDDIT) |
| Telegram | channel monitoring | ✅ Live (RULE_TELEGRAM_OSINT) |
| OpenSky (ADS-B) | public ADS-B feed | ✅ Live (RULE_ADSB) |

---

## Database

SQLite. The application always connects through `jpt_common.db_connection()`,
which runs schema init, idempotent migrations, and periodic backups.

Core tables:

```
members            — congressional members
transactions       — parsed PTR / Form 4 transactions
alerts             — rule-engine output, one per triggered rule (scored)
alert_outcomes     — forward returns per alert (+1/+5/+20d, SPY-relative)
themes             — Market Theses built by the corroboration engine
theme_signals      — evidence links for a theme
activity_log       — every rule run + every scheduled-job failure
scope_migrations   — additive migration tracking
```

Key design decisions:

- **Additive-only migrations**, tracked in `scope_migrations`. Tables are never
  dropped; column adds are guarded with `PRAGMA table_info`.
- **Amount bands** are stored as enum values, never numeric midpoints.
- **Ticker normalization** is applied on both alert write paths so corroboration
  counts are consistent.
- **Detection-time scores are immutable** — see [Scoring](#scoring).

---

## Rule engine

Each rule is an independent script run as a scheduled subprocess with
`--emit-alerts`. Alerts are written either via `jpt_common.insert_alert()`
(preferred — computes scores inline at write time) or via a raw insert backfilled
by the 10-minute `enrich_scores` job. Every rule run ends by recording a row to
`activity_log` with scanned / flagged / emitted counts.

The full rule → file → cadence table is maintained in
[`Scope/CLAUDE.md`](Scope/CLAUDE.md). See the [README](README.md#the-rules--live)
for the domain summary.

**Corroboration (RULE_10)** is the keystone: it fires when 4+ *distinct eligible*
detection mechanisms converge on the same ticker within 24h, then creates/evolves
a `themes` row (Market Thesis) and links the supporting evidence in
`theme_signals`.

**Clustering (RULE_CLUSTER)** fires when 3+ distinct congressional members trade
the same normalized ticker inside a rolling 72h window (trade-proximity, not
wall-clock — PTRs disclose 30–45 days late). Cluster identity is
`(sorted member set, ticker, direction)`.

---

## Scoring

Two **independent** scores, never merged, both fixed at detection time:

- **Evidence Confidence** — how well-supported the signal is (distinct
  corroborating-source count → confidence level; conflict penalized).
- **Opportunity** — how much edge remains:
  `novelty × 40 − absorption × 30 + horizon × 20 + win_rate × 10`, scaled by
  liquidity. The `win_rate` term is a placeholder reserved for per-rule *realized*
  win rate once the outcome dataset is calibrated.
- **Novelty** — `1.0` first-ever, log-decaying with 30-day recurrence.

Scores are **never retroactively recomputed**. This preserves the calibration
record that `alert_outcomes` depends on. Full detail lives in
[`Scope/CLAUDE.md`](Scope/CLAUDE.md).

---

## Outcome tracking (the moat)

A daily job walks alerts whose +20-trading-day horizon has elapsed, fetches price
data at detection and at +1 / +5 / +20 days, computes returns and SPY-relative
alpha, and writes them to `alert_outcomes`. Over time this becomes a proprietary
record of what the market did *after* Scope called each signal — the raw material
for calibrating scores and for future reasoning features.

---

## LLM layer

Groq, on the **slow path only**. Never used for prediction — only for:

- Summarization (Federal Register rules, multi-source threads, the Morning Brief)
- Entity extraction (tickers / company names from unstructured text)
- Narrative generation (the "why this matters" paragraph on corroboration alerts)

Narrative generation uses a primary key with a fallback provider key, logging
which provider served each call.

---

## Scheduling & resilience

APScheduler runs ~30 jobs in a background thread inside the FastAPI process —
detection rules on intervals (5 min to 6 h) and cron jobs (daily briefs, backups,
outcome labeling, roster checks). Every job runs through a wrapper that guarantees
**any** failure — non-zero exit, timeout, or an import-time crash before the
script's own error handling — produces an `activity_log` row. No scheduled-job
failure can be silent.

Resilience additions: hourly DB snapshots + a daily backup job with integrity
checks and retention, a stall monitor for the scoring backfill, and disk-usage
monitoring.

---

## Frontend

Client-rendered static pages (`Scope/api/static/*.html`) — vanilla JS + `fetch`
against the JSON API routers, in a dark terminal aesthetic. No Jinja templating.
30+ pages cover alerts, war rooms, congress, sectors, performance, OSINT, the
brief, and more.
