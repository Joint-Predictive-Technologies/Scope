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
- Migrations: additive only, tracked in `scope_migrations` (m001…m014). Never drop tables. Guard column adds with `PRAGMA table_info`. **m014** adds `alerts.corroborates` / `corroboration_note` (the signed-leg verdict — NULL means *unknown*, which fails closed for a signed rule) and **moves `alerts.award_key` into the ordered path**: it was previously created by a lazy `ALTER` inside `rule_11_contracts.py`, so on a DB where RULE_11 had never run the gate's own SELECT would have raised `no such column`. Both adds stay `PRAGMA`-guarded, so the two are idempotent in either order.
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
- ⚠️ `rule_06_form4.py` has its **own local `insert_alert`** (same name, different function
  — 7 columns, not `jpt_common`'s). It carries the signed verdict in
  `corroborates` / `corroboration_note`. Its `tags` is a bare positional comma string
  (`owner,action,multiplier`), which is exactly why the verdict lives in typed columns: an
  owner name containing a comma shifts the direction out of index 1. **RULE_06's dedup key
  is its HEADLINE** (`alert_exists` on `(rule, ticker, headline)`), so any change to the
  headline text silently re-emits the whole corpus — the parse may be widened, that string
  may not.

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
| RULE_15 | `scripts/rule_15_earnings_nlp.py` | 360 min |
| RULE_16 | `scripts/rule_16_institutional.py` | cron daily 06:15 |
| ~~RULE_12/13/14~~ | **RETIRED 2026-07-27** — unscheduled and in `RULE_10_EXCLUDED`. RULE_12 read RULE_09's own LDA endpoint; RULE_13's FEC requests 422 and it cannot finish in 300s; RULE_14's host is NXDOMAIN. | — |
| RULE_OSINT | `scripts/rule_osint.py` | 15 min |
| RULE_REDDIT | `scripts/rule_reddit.py` | 30 min |
| RULE_ANOMALY | `scripts/rule_anomaly.py` | 180 min |
| RULE_ADSB | `scripts/rule_adsb.py` | 5 min |
| RULE_CLUSTER | `scripts/rule_cluster.py` | 240 min |
| RULE_TELEGRAM_OSINT | `scripts/rule_telegram_osint.py` | 60 min |
| RULE_OPTIONS | `scripts/rule_options_correlation.py` | 15 min (enriches existing alerts) |

Non-rule scheduled jobs: `scripts/enrich_scores.py` (10 min, scoring backfill),
`scripts/telegram_bot.py` (60 min, push), `scripts/decay_alerts.py` (cron 01:00),
`scripts/morning_brief.py` (cron 06:30, the deterministic brief — the old `generate_brief.py` cron entry was deleted 2026-07-27 as a 100%-failing duplicate of this slot; it remains reachable on demand via `api/routers/brief.py`), `scripts/ingest_lobbying.py` (cron Mon 04:45),
`scripts/run_backtest.py` (cron Sun 02:00),
`scripts/label_outcomes.py` (cron daily 02:00, forward-return labeling),
`scripts/roster_check.py` (cron monthly 1st 04:00, recurring-unmatched-filer guard),
`ingest_house_index.py` (cron 6h) + `parse_house_pdfs.py` (cron 4h), congressional ingestion.

**Outcome labeling / calibration seed:** `alert_outcomes` (separate table, one row
per alert) records forward returns once an alert's +20-trading-day horizon has
elapsed — `price_at_detection`, `price_/return_{1d,5d,20d}` (returns are decimals),
SPY `benchmark_return_{1d,5d,20d}` for alpha, and `status`
(`complete`/`unavailable`/`pending`). Written only by `scripts/label_outcomes.py`;
never entangle rules or scoring with it. `_is_equity_ticker` (`scripts/label_outcomes.py:106`) excludes only
**empty tickers and tickers containing a space** (multi-symbol baskets). Single-symbol
ETFs are NOT excluded — SPY has 135 `complete` outcome rows. Tickers that cannot be
priced get `status='unavailable'` via the price lookup, not via a basket-name rule. This is the raw material for the future calibration report —
do not interpret small per-rule samples early.

**RULE_10 is the corroboration engine:** fires when **3+ distinct INSTRUMENTS**
converge on the same ticker within **14 days** (ingestion time, `created_at`).
Excluded from the eligible set: RULE_07, RULE_OSINT, RULE_REDDIT, RULE_ANOMALY (and
RULE_10 itself) as **noise**, plus **RULE_12, RULE_13, RULE_14** as **retired**. That set
is the single source of truth: `scripts/rule_10_corroboration.py`'s
`EXCLUDED_FROM_CORROBORATION` is **derived from it**, so a retired rule is excluded from
both instrument-counting and corroboration-candidacy. They had silently diverged, letting
retired rules inflate a corroboration's `evidence_confidence` 6.0 -> 81.0. It also creates/evolves a `themes` row (Market Thesis) and
links evidence in `theme_signals`.

**SIGNED LEGS — the gate asks a SECOND question, per ALERT** (added 2026-07-30,
human-gated). Rule-name eligibility answers *can this kind of signal ever corroborate*;
per-alert eligibility answers *does this particular signal actually say the thing we are
counting it as saying*. Without it an insider **sell** corroborated a bullish thesis exactly
as well as a buy, and that shipped: prod theme 1 fired on RTX at exactly 3 instruments where
the insider leg was an **exercise-and-sell**.

- `jpt_common.SIGNED_RULES` is the entire blast radius and is currently **`{"RULE_06"}`**.
  For every other rule an absent verdict means "corroborates", exactly as before — that is
  what makes "the untouched instruments are unchanged" provable
  (`test_gate_redesign.py::test_the_UNSIGNED_instruments_are_completely_untouched`).
- **A rule may only be signed once its ATTRIBUTION is repaired.** RULE_15 (misattributed
  *rituximab* to RTX) and RULE_01B (~46% of sales mislabelled as opens) are deliberately
  unsigned: a confident sign on known-wrong data makes a future false convergence look
  *more* credible. See `vault/Scope/01_Architecture/signed-signal-engine.md`.
- **Insider bar = a genuine open-market buy**: code `P`, acquired, non-derivative,
  non-10b5-1. Do NOT re-derive this — it is `insider_clusters.py::_buy_predicate`, with a
  Python twin `jpt_common.is_genuine_open_market_buy` whose equivalence is proven over an
  exhaustive matrix. `M` (exercise) is excluded *structurally* by the whitelist-of-one;
  `is_10b5_1` is **tri-state** (NULL = undisclosed, kept).
- **Fails closed.** `alerts.corroborates` NULL ⇒ no corroboration for a signed rule.
  Forward-only, no backfill — historical insider legs go dark until re-parsed. The stored
  `sale`/`purchase` tag is **not** an acceptable fallback: it classifies on the code letter
  alone over a P/S-only list, so it reads a `P`/*disposed* row as a purchase.
- **`tags.rules` on a RULE_10 alert is the CORROBORATING set**, not every rule present.
  Five consumers re-derive the count from it (`_distinct_rule_count`, `evidence.py`,
  `theme_instrument_count`, `receipts.py`, `generate_brief.py`), so this keeps them agreeing
  with the gate for free. `tags.rules_present` and `tags.non_corroborating` carry the
  provenance — a rejected leg is **disclosed, never hidden**.
- **Per-leg weights** (`tags.leg_weights`, frozen at detection time) move the **score, never
  the count**: `calculate_evidence_confidence` steps on an integer, so a fractional count
  would fall below the first tier and score base 0. Contracts is weighted by
  award ÷ market cap; it may fall on any resolved ticker but may only **rise** where
  `contractor_attribution_is_exact` — a mis-attributed ticker must never inflate anything.
  Unknown cap ⇒ **neutral 1.0**, never 0. Non-finite stored weights also read as neutral
  (`json` accepts bare `NaN`/`Infinity`, and `min(CEILING, inf)` is the *ceiling*).
- ⚠️ **`contract_leg_weight` will NOT resolve a cap that isn't already cached.** Before this
  feature RULE_10 made **zero network requests**; a cold `market_cap` call resolves live via
  SEC + Yahoo, so a cold run could have fired dozens of requests inside the scheduler's 300s
  budget. It therefore requires an existing `ticker_meta` row and otherwise returns neutral —
  caps are warmed by `rule_reddit_collector` (4×/day) and `/api/tickers/{sym}/meta`.
- **`theme_signals` links only CORROBORATING legs.** Linking every present alert made a theme
  whose summary read "3 independent signals" list four items in its receipt, the fourth being
  the rejected leg, unlabelled. The rejection is disclosed on the corroboration itself in
  `tags.non_corroborating`; `theme_signals` is the *evidence* list, and a sell is not evidence
  for a bullish thesis.
- ⚠️ **There are FOUR other places that re-express the gate's candidate predicates** and all
  of them must move together: `api/routers/forming.py` (its own SQL copy; resolves the gate's
  functions at **call** time, because import-bound references go stale after a module
  reload), `scripts/morning_brief.py` (inlined predicates; two-way agreement is tested),
  and `api/routers/tickers.py` → `api/static/ticker.html` (the browser counter, which is
  handed a **server-computed** `corroborates_gate` flag rather than re-implementing the rule
  in JS). `scripts/check_convergence.py` is the only consumer coupled purely by import.

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

**D1 now reaches the score.** `evidence_confidence` counts INSTRUMENTS everywhere the
gate does: `_distinct_rule_count`, the RULE_10 emitter, `api/routers/evidence.py` (both
the corroboration branch and the single-rule one), `api/receipts.py`, and the homepage
hero card's `corrConfidence()` in `api/static/index.html`. The congressional trio no
longer opens a corroboration **or** inflates its confidence. All of them call
`rule10_instruments` — except the browser, which reads `tags.instrument_count` written
by the emitter. **Never reimplement the map client-side.**

`RULE_CLUSTER` passes `distinct_rule_count=1`: it is ONE instrument however many members
it has. Member count still drives severity and the headline — it is not corroboration
*breadth*.

**The tiers are in the GATE's units** (rescaled 2026-07-27, human-signed-off).
`calculate_evidence_confidence` steps at **>=3 -> 40, >=4 -> 60, >=5 -> 75**. The first
tier IS `RULE_10_MIN_INSTRUMENTS` — **if that threshold ever moves, move the first tier
with it.** They had drifted apart: the tiers still stepped at 4/5/6 in rule-name units
while the gate fired at 3 instruments, so every minimum corroboration fell below the
first tier and persisted **6.0 against a lone `RULE_06`'s 20.0** — a corroboration
ranking at one third of its own constituent signals, in a column `mode=overwatch`
sorts by. A minimum fire now persists **46.0**. The coupling is asserted in
`tests/test_evidence_confidence_instruments.py::test_the_tiers_are_in_the_gates_units`.

⚠️ **Scores are forward-only, so the rescale does NOT touch history.** Corroborations
detected before 2026-07-27 keep their pre-rescale values (a 3-instrument fire sits at
6.0), and `mode=overwatch` sorts old and new together — **old corroborations rank below
new ones for reasons that are not about evidence.** This resolves as the backlog ages
out; do NOT "fix" it by re-enriching, which would destroy detection-time values.

⚠️ **Known, not fixed — the top tier saturates at 5.** Because the tiers are
`>=3/>=4/>=5`, a 5-, 6- or 9-instrument convergence all take base 75 and persist the
**same** score (81.0 at the emitter's weight). Pre-rescale the top tier was `>=6`, so 5
and 6 were 15 points apart — the rescale did not create this, it moved it **down** to a
more reachable convergence size. Two consequences: `mode=overwatch` ties them and falls
through to `created_at DESC`, so a **newer 5-instrument corroboration can outrank an
older 6-instrument one**; and at the *theme* level (`score_alert_fields` uses per-rule
weights, not RULE_10's flat `Derived` 0.3) the quality average becomes the only
discriminator above the top tier, so **adding a lower-quality sixth leg lowers the
score** — 5 all-Primary = 95.0 beats 6-with-a-Secondary = 93.7. Themes are ordered by
`opportunity_score` (`api/routers/themes.py:40`), so that half is display-only. Fixing
it means a **fourth tier**, a formula-shape change needing sign-off; pinned in
`tests/test_evidence_tier_ordering.py::test_the_top_tier_saturates_at_five_and_that_is_a_known_limitation`.
**How often n>=5 actually occurs is `UNVERIFIED — needs prod`** (0 RULE_10 rows locally).

⚠️ **Known, not fixed:** `api/routers/evidence.py::_confidence_breakdown` computes its
own drawer number on a different scale (instruments x10 capped at 60, plus severity /
freshness / insider / contract points). It is not `evidence_confidence` and does not
track it — the drawer can read 80 where the stored score is 46. Pre-existing, flagged
rather than silently unified. See `05_Decisions/2026-07-25-gate-redesign.md`.

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
SEC (needs a contact `User-Agent`), ~~PatentsView~~ (**`search.patentsview.org` is authoritative NXDOMAIN** — `aa` flag from
the zone's own nameservers; PatentsView migrated to the USPTO Open Data Portal and the
successor API needs an ODP key. RULE_14 retired.). **Not used:** ReliefWeb, FRED.

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
