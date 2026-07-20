# Scope — Engineering Conventions

Political-market intelligence terminal. **FastAPI** (not Flask) + **SQLite** +
APScheduler, deployed on Railway. LLM tasks use Groq. This file is ground truth
for conventions — keep it in sync when they change.

## Architecture at a glance
- `api/main.py` — FastAPI app, page routes, APScheduler wiring, `/api/*` meta endpoints.
- `api/routers/*.py` — JSON API routers (alerts, congress, themes, lobbying, evidence, social, …).
- `api/static/*.html` — client-rendered pages (vanilla JS + `fetch`), dark terminal aesthetic. No Jinja.
- `jpt_common.py` — shared DB connection, scoring engine, sector maps, migrations, `insert_alert`.
- Rule scripts — some at repo root (`rule_0X_*.py`), some in `scripts/`. Run as subprocesses by the scheduler with `--emit-alerts`.
- `tests/*.py` — self-contained (pytest-compatible, also runnable via `python3 tests/<f>.py`).

## Database
- SQLite. Path resolution (`jpt_common._get_db_path`): explicit arg → `DATABASE_PATH` env → Railway volume `/app/data/jpt.db` if present → local `./data/jpt.db`.
- **Always** connect via `jpt_common.db_connection()` in app/rule code (runs schema init + idempotent migrations + hourly backup). For **read-only diagnostics**, connect directly with `sqlite3.connect('file:data/jpt.db?mode=ro', uri=True)` to avoid triggering migrations/backups.
- Migrations: additive only, tracked in `scope_migrations` (m001…m009). Never drop tables. Guard column adds with `PRAGMA table_info`.
- `data/backups/` is gitignored (hourly snapshots).
- `created_at` is stored UTC-naive (`datetime('now')`). Window comparisons use SQL `datetime('now','-Xh')` — keep them SQL-side and UTC-naive; do not mix tz-aware Python datetimes into these comparisons.

## Alerts: two valid write paths
There are **two accepted ways** to write an alert. New rules SHOULD use path (a).

**(a) `jpt_common.insert_alert(conn, rule, ticker, severity, headline, ...)` — preferred.**
Normalizes the ticker and computes the Phase-2 scores (novelty, opportunity,
evidence_confidence, time_horizon, source_quality) **inline, at write time**.
- Optional kwargs: `why_matters, tags (dict|str), member_id, source_url, verify_url, detail, event_date, theme_id, distinct_rule_count, has_conflict, absorption_pct`.
- `tags` may be a dict (auto-JSON) or a JSON string.
- On path (a) today: `scripts/rule_osint.py`, `scripts/rule_reddit.py`,
  `scripts/rule_10_corroboration.py`, `scripts/rule_cluster.py`.

**(b) Direct `INSERT INTO alerts`, relying on `enrich_scores` to backfill.**
Legacy rules insert raw and leave scores at schema defaults; the 10-min
`scripts/enrich_scores.py` job (`jpt_common.enrich_alert_scores`, criterion
`opportunity_score=0 AND evidence_confidence=0`) fills in novelty_score,
opportunity_score, evidence_confidence, time_horizon and source_quality afterward.
- **This path depends on `enrich_scores` running reliably** — it is a single point
  of failure. `MONITOR_ENRICH_STALL` (hourly) watches for it (see Known Issues).
- `enrich_scores` **normalizes tickers** on the rows it touches (`normalize_ticker`
  in the same UPDATE), so path (a) and path (b) are behaviorally equivalent for
  downstream corroboration (RULE_10 / RULE_CLUSTER) once enrichment has run.
- On path (b) today (raw INSERT): `rule_07_polymarket.py`, `rule_06_form4.py`,
  `rule_08_federal_register.py`, `rule_09_lobbying.py`, `rule_02_cluster.py`,
  `scripts/rule_01b_first_touch.py`, `scripts/rule_11_contracts.py`,
  `scripts/rule_12_fara.py`, `scripts/rule_13_fec.py`, `scripts/rule_14_patents.py`,
  `scripts/rule_15_earnings_nlp.py`, `scripts/rule_anomaly.py`,
  `scripts/rule_adsb.py`, `scripts/rule_telegram_osint.py`, `ingest_senate.py`.

## Scoring model (jpt_common)
Two **independent** scores, never merged:
- `calculate_evidence_confidence(distinct_rule_count, source_quality_scores, has_conflict)` — how well-supported.
- `calculate_opportunity_score(novelty, absorption_pct, time_horizon)` — how much opportunity remains (clamped 0–100).
- `calculate_novelty_score(rule, region_or_ticker, conn)` — 1.0 first-ever, log-decays with 30-day recurrence (intended floor 0.1).
- Rule → `RULE_TIME_HORIZONS` / `RULE_SOURCE_QUALITY` maps also live in jpt_common.

## Activity log
Every rule `run()`/`main()` ends with `record_activity(source, scanned, flagged, emitted, duration_seconds)` (fresh connection). `scanned` = raw records examined, `flagged` = passed quality filter, `emitted` = alerts inserted. Powers `/status` and the homepage activity strip.

## The rules (ground truth — file → source label → cadence)
| Source label | File | Cadence |
|---|---|---|
| RULE_01B | `scripts/rule_01b_first_touch.py` | 120 min |
| RULE_02 | `rule_02_cluster.py` | (via cluster/main) |
| RULE_06 | `rule_06_form4.py` | 120 min |
| RULE_07 | `rule_07_polymarket.py` | 20 min |
| RULE_08 | `rule_08_federal_register.py` | 240 min |
| RULE_09 | `rule_09_lobbying.py` | cron daily 03:00 |
| RULE_10 | `scripts/rule_10_corroboration.py` | 60 min |
| RULE_11 | `scripts/rule_11_contracts.py` | 360 min |
| RULE_12 | `scripts/rule_12_fara.py` | cron Mon 04:00 |
| RULE_13 | `scripts/rule_13_fec.py` | cron daily 05:00 |
| RULE_14 | `scripts/rule_14_patents.py` | cron Tue/Fri 04:30 |
| RULE_15 | `scripts/rule_15_earnings_nlp.py` | 360 min |
| RULE_OSINT | `scripts/rule_osint.py` | 15 min |
| RULE_REDDIT | `scripts/rule_reddit.py` | 30 min |
| RULE_ANOMALY | `scripts/rule_anomaly.py` | 180 min |
| RULE_ADSB | `scripts/rule_adsb.py` | 5 min |
| RULE_CLUSTER | `scripts/rule_cluster.py` | 240 min |
| RULE_TELEGRAM_OSINT | `scripts/rule_telegram_osint.py` | 60 min |
| RULE_OPTIONS | `scripts/rule_options_correlation.py` | 15 min (enriches existing alerts) |

Non-rule scheduled jobs: `scripts/enrich_scores.py` (10 min, scoring backfill),
`scripts/telegram_bot.py` (60 min, push), `scripts/decay_alerts.py` (cron 01:00),
`generate_brief.py` (cron 06:30), `scripts/ingest_lobbying.py` (cron Mon 04:45),
`scripts/run_backtest.py` (cron Sun 02:00).

**RULE_10 is the corroboration engine:** fires when 4+ *distinct eligible* rules
converge on the same ticker within 24h. Excluded from the eligible set:
RULE_07, RULE_OSINT, RULE_REDDIT, RULE_ANOMALY (and RULE_10 itself). It also
creates/evolves a `themes` row (Market Thesis) and links evidence in `theme_signals`.

## External sources (status per last diagnostic)
GDELT (`data.gdeltproject.org`), Arctic Shift (Reddit), Polymarket Gamma+CLOB,
FEC (`api.open.fec.gov/v1`), OpenSky, USASpending, Federal Register, Senate LDA,
SEC (needs a contact `User-Agent`), PatentsView (`search.patentsview.org` — DNS
blocked in some sandboxes, fine in prod). **Not used:** ReliefWeb, FRED.

## Known issues (tracked, not yet fixed)
- **Unmatched House filers — largely resolved.** `match_member_id` now does
  deterministic anchor matching (credential/suffix stripping, compound-surname
  subset match, first-given-token equality, unique-candidate guard, with the old
  difflib as fallback). This fixed the recurring misses — April McClain Delaney
  (M001232), Neal P. Dunn (D000628), Earl L. "Buddy" Carter (C001103) — and a
  one-time backfill (`scripts/backfill_member_ids.py`, re-downloads the FD.zip
  indexes since raw names aren't persisted) matched 27 filings / ~360 txns.
  **Residual (needs manual review):** *Linda T. Sanchez* (doc 20033755, 1 txn) —
  she is **absent from the `members` table** (only `Sanchez, Loretta` is present),
  so this is a roster-completeness gap, not a normalization bug. Fix = refresh the
  members roster, not the matcher. Match/unmatch counts are surfaced in the
  INGEST_HOUSE_INDEX activity_log notes as "matched=X, unmatched=Y".
- **Member-matching is a Stage-1 metric.** The unmatched count is logged by
  INGEST_HOUSE_INDEX (where matching happens), not PARSE_HOUSE_PDFS.
- **`transactions.member_id` string-`'None'` bug — fixed.** An older parse path
  (`parse_house_pdfs.fetch_pending_filings`) coerced `str(row["member_id"])`,
  writing the literal string `'None'` for unmatched filers. Now preserves SQL
  NULL; the backfill above rewrote the 75 legacy `'None'` rows.
- **~15 rules on write path (b)** (raw INSERT, listed under "Alerts: two valid
  write paths"). Accepted pattern — scoring survives via the 10-min `enrich_scores`
  backfill. This is a **single point of failure**: if `enrich_scores` stalls, new
  alerts sit at default scores. Guarded by the hourly `MONITOR_ENRICH_STALL` job
  (`scripts/monitor_enrich_stall.py`) which logs a CRITICAL `activity_log` row
  (and optional Telegram) when alerts >30 min old remain unscored. Not migrating
  the 15 scripts for now (too much surface area on working code).
- **~3% PDF parse failure rate** (88 historical `parse_failed`, 14 from the
  backlog catch-up). Worth a one-time look at whether the failures share a common
  PDF format/layout the parser doesn't handle.

## Conventions
- Reference code as `file_path:line`. Match surrounding style; no new frameworks.
- Commit/push only when asked. End commit messages with the Co-Authored-By line.
- Tests must pass before commit: run each `tests/test_*.py`.
