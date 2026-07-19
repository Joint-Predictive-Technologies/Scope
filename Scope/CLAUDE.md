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

## Alerts: the single write point
**Insert every alert through `jpt_common.insert_alert(conn, rule, ticker, severity, headline, ...)`.**
It normalizes the ticker and computes the Phase-2 scores (novelty, opportunity,
evidence_confidence, time_horizon, source_quality) inline. Do **not** write raw
`INSERT INTO alerts` in rule scripts.

- Optional kwargs: `why_matters, tags (dict|str), member_id, source_url, verify_url, detail, event_date, theme_id, distinct_rule_count, has_conflict, absorption_pct`.
- `tags` may be a dict (auto-JSON) or a JSON string.
- Scoring safety net: `scripts/enrich_scores.py` (10-min job) backfills scores for any alert still at defaults — but that is a *fallback*, not a license to bypass `insert_alert`. (Known debt: ~16 legacy scripts still raw-insert; tracked for migration.)

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

## Conventions
- Reference code as `file_path:line`. Match surrounding style; no new frameworks.
- Commit/push only when asked. End commit messages with the Co-Authored-By line.
- Tests must pass before commit: run each `tests/test_*.py`.
