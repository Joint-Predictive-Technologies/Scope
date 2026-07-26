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
- `tests/*.py` — run with `pytest tests/` (the supported path; `tests/conftest.py` gives each test a disposable DB). Running a module directly now fails fast unless `DATABASE_PATH` points at a throwaway file.

## Database
- SQLite. Path resolution (`jpt_common._get_db_path`): explicit arg → `DATABASE_PATH` env → Railway volume `/app/data/jpt.db` if present → local `./data/jpt.db`.
- **Always** connect via `jpt_common.db_connection()` in app/rule code (runs schema init + idempotent migrations; it does **not** take backups — see below). For **read-only diagnostics**, connect directly with `sqlite3.connect('file:data/jpt.db?mode=ro', uri=True)` to avoid triggering migrations/backups.
- Migrations: additive only, tracked in `scope_migrations` (m001…m009). Never drop tables. Guard column adds with `PRAGMA table_info`.
- **Backups:** `scripts/db_backup.py` runs **hourly at :05** — SQLite online backup API,
  `PRAGMA integrity_check` *before* the snapshot is kept, gzip, tiered retention
  (24h hourly → 30d daily → 90d weekly → monthly), and an env-gated S3 upload that is
  dormant until `BACKUP_S3_*` is set. `scripts/monitor_backup_stall.py` (hourly) alarms
  if no fresh snapshot **file** appears. The old connect-triggered raw copy in
  `_backup_db` is **retired** — a raw copy of a live DB can tear, and a torn copy still
  passes `integrity_check`. Restore procedure: `RESTORE.md`. `data/backups/` is gitignored.
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

**Detection-time scores are immutable.** `enrich_scores` populates *missing*
scores but never overwrites existing ones (`enrich_alert_scores(only_unscored=True)`
is the default and the only form the scheduler runs). **Never run enrich with
`only_unscored=False` on historical alerts** (that's what `enrich_scores.py --all`
does) — it recomputes novelty against *today's* population and destroys the
detection-time values that calibration and `alert_outcomes` depend on. For
ticker-only backfills, use the dedicated ticker-normalization path
(`jpt_common.normalize_existing_tickers`), **not** full enrichment.

## Scoring model (jpt_common)
Two **independent** scores, never merged:
- `calculate_evidence_confidence(distinct_rule_count, source_quality_scores, has_conflict)` — how well-supported.
- `calculate_opportunity_score(novelty, absorption_pct, time_horizon)` — how much opportunity remains (clamped 0–100). Formula: `novelty*40 − (absorption/100)*30 + horizon*20 + win_rate*10`, then `× liquidity_score`. The `win_rate*10` term is real but currently a fixed **0.5 placeholder (+5 on every alert)**, reserved for per-rule *realized* win rate from `alert_outcomes` once calibrated; `liquidity_score` defaults 1.0. The score decomposition surfaces all four terms.
- `calculate_novelty_score(rule, region_or_ticker, conn)` — 1.0 first-ever, log-decays with 30-day recurrence (intended floor 0.1).
- Rule → `RULE_TIME_HORIZONS` / `RULE_SOURCE_QUALITY` maps also live in jpt_common.

## Scheduler safety net
Every scheduled job runs through `_run_rule` (subprocess). **Any** failure —
non-zero exit, **import-time crash (ImportError/SyntaxError) that runs before the
script's own error handling**, timeout, or an exception invoking the subprocess —
is guaranteed to produce an `activity_log` row with `source='SCHEDULER_JOB_FAILURE'`
capturing the job name, exit code / exception type, and the stderr/traceback tail.
This is the universal net: no scheduled-job failure, including import-time, can be
silent. (Per-script logging still adds richer rows from inside `run()`.)

## Activity log
Every rule `run()`/`main()` ends with `record_activity(source, scanned, flagged, emitted, duration_seconds)` (fresh connection). `scanned` = raw records examined, `flagged` = passed quality filter, `emitted` = alerts inserted. Powers `/status` and the homepage activity strip.

## The rules (ground truth — file → source label → cadence)
| Source label | File | Cadence |
|---|---|---|
| RULE_01B | `scripts/rule_01b_first_touch.py` | 120 min |
| RULE_02 | `rule_02_cluster.py` | 240 min (interval) — 7-day cluster, looser sibling of the 72h RULE_CLUSTER; same 4h cadence, independent subprocess. |
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
`scripts/run_backtest.py` (cron Sun 02:00),
`scripts/label_outcomes.py` (cron daily 02:00, forward-return labeling),
`scripts/roster_check.py` (cron monthly 1st 04:00, recurring-unmatched-filer guard),
`ingest_house_index.py` (cron 6h) + `parse_house_pdfs.py` (cron 4h), congressional ingestion.

**Outcome labeling / calibration seed:** `alert_outcomes` (separate table, one row
per alert) records forward returns once an alert's +20-trading-day horizon has
elapsed — `price_at_detection`, `price_/return_{1d,5d,20d}` (returns are decimals),
SPY `benchmark_return_{1d,5d,20d}` for alpha, and `status`
(`complete`/`unavailable`/`pending`). Written only by `scripts/label_outcomes.py`;
never entangle rules or scoring with it. Non-equity / basket / delisted tickers get
`status='unavailable'`. This is the raw material for the future calibration report —
do not interpret small per-rule samples early.

**RULE_10 is the corroboration engine:** fires when **3+ distinct INSTRUMENTS**
converge on the same ticker within **14 days** (ingestion time, `created_at`).
Excluded from the eligible set: RULE_07, RULE_OSINT, RULE_REDDIT, RULE_ANOMALY
(and RULE_10 itself). It also creates/evolves a `themes` row (Market Thesis) and
links evidence in `theme_signals`.

**Instruments, not rule names** (`jpt_common.RULE_10_INSTRUMENTS`). Rules that read
the same underlying source count once: `RULE_01`/`RULE_01B`/`RULE_02`/`RULE_CLUSTER`
are all the congressional `transactions` feed = **one** instrument, and
`RULE_09`/`RULE_12` are both Senate LDA filings (RULE_12 is a strict subset —
LDA rows with a non-empty `foreign_entities`) = **one** instrument. `RULE_06`
(Form 4) and `RULE_15` (8-K) share the EDGAR host but are disjoint document
populations, so they stay separate. The test is *same document population*, not
same endpoint. An unmapped eligible rule counts as its own instrument — if you add
a rule that reads an existing source, **map it**, or it silently becomes a second
leg. `jpt_common.rule10_is_valid` must agree with the gate: it is what decides
whether the brief and the evidence API may cite a corroboration.

⚠️ **D1 stops at the gate.** `_distinct_rule_count` and `api/routers/evidence.py`
still count rule *names*, so the congressional trio no longer *opens* a
corroboration but still inflates its `evidence_confidence`. Fixing that is a
scoring change and human-gated. See `05_Decisions/2026-07-25-gate-redesign.md`.

**RULE_CLUSTER (`scripts/rule_cluster.py`, path a):** fires when 3+ DISTINCT
members trade the same normalized ticker inside a rolling 72h window by
`transaction_date`. HIGH for 3-4 members, CRITICAL for 5+. Direction is
per-member buy/sell → `consensus_buy` / `consensus_sell` / `mixed` (mixed carries
`has_conflict=True`, all three fire). **Cluster identity = (sorted member set,
ticker, direction)**; a member joining a prior smaller cluster is a NEW alert that
supersedes the earlier one (`lifecycle_stage='superseded'`, "expanded to N
members") — dedup is on identity, never on ticker+time-window. Novelty is computed
on the cluster identity via `insert_alert(..., novelty_key=fingerprint)` (with the
fingerprint also embedded in `why_matters` so `calculate_novelty_score`'s LIKE
match finds prior occurrences), so a cluster launches novel even though RULE_01B
already fired the ticker per member. **Windowing:** because PTRs disclose 30-45
days after the trade, "last 72h" means trades within 72h *of each other*, found by
sliding a 72h window over a 45-day disclosure-lag scan horizon (`SCAN_HORIZON_DAYS`)
and picking the strongest window per ticker (most members; ties → most recent).

## External sources (status per last diagnostic)
GDELT (`data.gdeltproject.org`), Arctic Shift (Reddit), Polymarket Gamma+CLOB,
FEC (`api.open.fec.gov/v1`), OpenSky, USASpending, Federal Register, Senate LDA,
SEC (needs a contact `User-Agent`), PatentsView (`search.patentsview.org` — DNS
blocked in some sandboxes, fine in prod). **Not used:** ReliefWeb, FRED.

## Known issues (tracked, not yet fixed)
- **Unmatched House filers — resolved.** `match_member_id` now does deterministic
  anchor matching (credential/suffix stripping, compound-surname subset match,
  first-given-token equality, unique-candidate guard, with the old difflib as
  fallback) plus **diacritic folding** (`fold_accents`). This fixed the recurring
  misses — April McClain Delaney (M001232), Neal P. Dunn (D000628), Earl L. "Buddy"
  Carter (C001103), and Linda T. Sánchez (S001156 — present all along, spelled
  `Sánchez`; the ASCII PTR "Sanchez" never matched). A one-time backfill
  (`scripts/backfill_member_ids.py`, re-downloads the FD.zip indexes since raw
  names aren't persisted) matched 28 filings / ~361 txns. **0 unmatched House
  filers remain; 0 `transactions.member_id` NULL.** Match/unmatch counts surface in
  the INGEST_HOUSE_INDEX activity_log notes as "matched=X, unmatched=Y".
- **Roster-freshness guard:** `ingest_house_index` records still-unmatched filer
  names in `unmatched_filers`; the monthly `ROSTER_CHECK` job
  (`scripts/roster_check.py`) re-tests them against the current matcher (resolved
  names auto-clear) and logs a WARNING when a name recurs on 2+ filings — so the
  next Sánchez surfaces without a manual diagnostic.
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
- **PDF parse failures are a stable paper-filing long-tail — accept, don't fix.**
  102 house `parse_failed` filings. The split is structural: **98/102 are 7-digit
  `8xxxxxx`/paper (scanned image) doc_ids; only 4 are electronic `200xxxxx`.**
  Electronic PTRs parse at 720/724 ≈ 99.5%; the failures are handwritten/scanned
  forms from paper filers (Khanna, McCaul, Rogers — 18 each) that pdfplumber can't
  read as text. This is not a growing electronic-format regression, so the parser
  needs no fix. Recovering paper filers would be a dedicated OCR project (high
  effort, low yield) — tracked separately, not urgent.

## Conventions
- Reference code as `file_path:line`. Match surrounding style; no new frameworks.
- Commit/push only when asked. End commit messages with the Co-Authored-By line.
- Tests must pass before commit: run each `tests/test_*.py`.
