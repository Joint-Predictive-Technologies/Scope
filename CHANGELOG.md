# Changelog

What's been built in Scope, most recent first. This is a human-readable summary
of progress — for exact commits see `git log`, and for engineering ground truth
see [`Scope/CLAUDE.md`](Scope/CLAUDE.md).

Scope grew well beyond its original 10-rule Phase-0 plan
([`phase_zero_spec.md`](phase_zero_spec.md)). What follows is the journey from
that plan to the live system today.

---

## Production hardening & knowledge base — Jul 2026

- **Obsidian knowledge vault** (`vault/Scope/`) — a living [Master Plan](vault/Scope/03_Roadmap/Master%20Plan.md),
  architecture decisions, known-issue tracking, and per-session work logs.
- **Database backup automation** — compressed, integrity-checked daily snapshots
  with tiered retention (`scripts/db_backup.py`) and a documented restore runbook
  (`Scope/RESTORE.md`).
- **Groq LLM primary/fallback** — narrative generation retries the primary key,
  then falls back to a secondary provider key, logging the provider used.
- **Standalone congressional digest** — a browsable, dated digest view
  (`/congress/digest/<date>`).
- **Universal scheduler safety net** — every scheduled job failure, *including
  import-time crashes*, is guaranteed to log a row to `activity_log`. No silent
  failures.
- **Member-name matching overhaul** — deterministic anchor matching plus diacritic
  folding; 0 unmatched House filers remain.
- **Ticker normalization** unified across both alert write paths.
- **Monitoring** — disk-usage checks and a stall monitor that alerts if the scoring
  backfill pipeline stops.

## The reasoning & measurement layer — 2026

- **Outcome tracking (`alert_outcomes`)** — every alert is followed forward
  (+1 / +5 / +20 trading days, SPY-relative) by a daily labeling job. This is the
  raw material for calibration and the product's long-term moat.
- **RULE_CLUSTER** — congressional cluster detection: 3+ members trading the same
  ticker inside a 72h trade-proximity window (HIGH for 3–4, CRITICAL for 5+), with
  consensus-direction and cluster-identity dedup.
- **RULE_10 corroboration engine** — fires when 4+ *distinct* detection mechanisms
  converge on one ticker within 24h; builds and evolves a Market Thesis linking the
  evidence.
- **Dual scoring model** — independent Evidence Confidence and Opportunity scores,
  plus novelty decay; scores are immutable at detection time.
- **Morning Brief** — a scheduled 7-section daily summary.
- **War rooms & annotations** — thesis and cluster war rooms with multi-level user
  annotations.

## Rule & source expansion — 2026

Grew from the original 10-rule plan to ~20 live detectors across new data domains:
federal contracts (RULE_11), FARA foreign influence (RULE_12), FEC campaign
finance (RULE_13), patents (RULE_14), earnings-call NLP (RULE_15), GDELT OSINT,
Reddit, Telegram, ADS-B aircraft movement, statistical anomaly detection, and
options-flow correlation.

## Platform foundation — 2026

- **FastAPI + APScheduler** terminal — the app serves both the API and a
  client-rendered terminal UI (30+ pages), with an in-process scheduler running
  every rule and maintenance job. (The original plan called for cron + a Flask/
  Postgres port; the system consolidated on FastAPI + SQLite + APScheduler on
  Railway instead.)
- **LLM layer on Groq** — narrative generation and entity extraction on the slow
  path only.
- **Congressional ingestion completed** — House PTR PDF parsing (pdfplumber with
  OCR fallback) landed on top of the earlier index registration; Senate eFD
  ingestion hardened.

## Phase 0 baseline — Jun 2026

The original starting point, per [`phase_zero_spec.md`](phase_zero_spec.md):

- Senate PTR ingestion (end-to-end), House PTR index registration (front half).
- A 10-rule product plan with cross-source corroboration (Rule 10) as the thesis.
- SQLite schema, shared helpers (`jpt_common.py`), a landing-page stub.
