---
aliases: [Current Blockers, Known Issues]
type: issue
stage: iPhone-1
status: active
priority: critical
tags: [blockers, production, infrastructure]
related: [[Roadmap Tracking]], [[Production Health]]
---

# Current Blockers and Open Items

Issues actively blocking progress or needing a human decision.

## Open questions (active)

- **RULE_10 convergence has never fired on real data — RESOLVED as "never
  generated", not data loss.** Traced 2026-07-25 (read-only), full evidence in
  [[SESSION-2026-07-25-rule10-convergence-trace]]. The 28 RULE_10 emits in
  `activity_log` are entirely accounted for by the `ZWAR` test fixture in
  `tests/test_war_rooms.py:108-133` (28 themes and 140 = 28×5 theme_signals in
  `sqlite_sequence`, all deleted by the test teardown). **The earlier DATA-LOSS
  flag is retracted.** The real open questions this leaves:
  - **Why does nothing converge?** 0 ticker/24h-windows have *ever* reached the
    4-distinct-eligible-rule gate; the maximum ever observed is 3 (`SPCX`).
    Candidate causes for Priority #2: multi-symbol ticker keys (`LMT RTX NOC`
    stored as one opaque ticker across 511 alerts, 72 of them from the *eligible*
    RULE_08), the HIGH/CRITICAL severity gate, and rule cadences that make four
    distinct rules inside 24h structurally unlikely. Not investigated here.
  - ~~**Tests mutate the production database.**~~ **FIXED by WS1 (2026-07-26,
    branch `fix/test-isolation-and-untrack-db`, awaiting merge).**
    `tests/conftest.py` now gives every test a disposable temp DB, and
    `jpt_common._get_db_path` refuses to resolve to the real DB from a test
    process. Verified: the working DB is byte-identical across full suite runs.
    The four tests that asserted on live production content now seed their own
    fixtures. See [[SESSION-2026-07-26-ws1-completion]].
  - ~~**`Scope/data/jpt.db` is tracked in git** (42 commits).~~ **FIXED by WS1** —
    `git rm --cached` plus a `.gitignore` entry; the file stays on disk. Confirmed
    safe: `DATABASE_PATH=/app/data/jpt.db` is set in Railway, so production reads
    the volume, never the repo copy (human-confirmed via the Railway dashboard
    2026-07-26; not independently verified by Claude Code, which cannot reach prod).
  - Verification on production is still outstanding — the distinguishing query is
    in the session note's Provenance section.

## Production (active)

- **RULE_06 (Form 4) times out every run — data gap.** `rule_06_form4.py` re-scans
  a 7-day window (~1,950 filings) serially on a 2-hour job and is killed at the
  300s subprocess limit before finishing, so it records **no activity and collects
  no SEC Form 4 data**. The scheduler safety net logs it as CRITICAL
  `SCHEDULER_JOB_FAILURE` (caught, not silent). Diagnosed 2026-07-22. Remediation
  drafted (human-gated, not applied): **[[RULE_06 Timeout Fix Plan]]** — ship
  Phase A (incremental window + time budget, no schema change) first. See
  [[Production Health]]. *(Phase A is now implemented on `fix/rule06-incremental-window`,
  awaiting review — see [[Roadmap Tracking]].)*

## Data gaps — alert provenance ("receipts") follow-up (2026-07-23)

Surfaced by the `feat/alert-provenance` audit. Several rules don't capture enough
provenance at **ingestion time** to build strong receipts. Not fixed this session
(ingestion changes were out of scope); the receipts block degrades gracefully and
flags each gap honestly. Follow-up tickets, highest value first:

- **RULE_06 (Form 4 insider) — highest value.** Stores only `name,action,multiple`
  in `tags`; **no SEC Form 4 URL and no structured transaction detail**
  (shares/price/date). Capture the Form 4 accession URL + txn fields at ingestion.
- **RULE_01B (first-touch trade).** Has member + action + date but **no PTR
  filing_url**, even though RULE_CLUSTER already stores one per member. Add the PTR
  PDF link.
- **RULE_02 (7-day cluster).** `detail` empty; member names are comma-joined in
  `tags` (unparseable — names contain commas) with no per-member detail or links.
- **RULE_08 / RULE_09.** `detail` empty; no source URL (Federal Register doc /
  lobbying disclosure).
- **RULE_11 (contracts).** `award_id` stored but **no USASpending URL** captured.
- **RULE_14 / RULE_15 / RULE_TELEGRAM_OSINT / RULE_ADSB.** No `source_url` stored.

These are **ingestion/rule changes → human-gated** (DATA-LOSS class); do not
automate. See [[2026-07-23-provenance-and-brief-landing]] for the full audit table.

## Data gaps — empty states (not bugs)

- **0 active themes → empty `/theses` + no thesis war rooms.** Confirmed by the
  2026-07-23 design-regression diagnostic. `/theses` (`intelligence.html`) and
  the thesis war rooms it links are legitimately empty because the DB has **no
  active themes**. Themes are created by RULE_10 corroboration (4+ distinct
  rules converging on a ticker within 24h), so this clears itself once
  corroboration fires — it is a data-accumulation gap, **not** a UI regression,
  and was **not** fabricated to fill. Directly downstream of RULE_10's long
  broken window and the RULE_06 data gap (fewer distinct rules firing = fewer
  corroborations). See [[2026-07-23 Design Pass Regression Repair]].

## UI / design residuals (2026-07-23, from the UI restoration session)

On `fix/ui-restoration-and-completion` (awaiting review). None block the branch;
listed for the follow-up pass. See [[2026-07-23 UI Restoration and Completion]].

- **Hex→token residual — 476 legacy inline hex** (26 distinct palette values)
  across ~18 static pages. `box-shadow`, pills, and legacy fonts are all zero and
  the palette is visually consistent with tokens, but these inline literals bypass
  the token system (e.g. 43 spots still render the OLD amber `#c8922a` instead of
  copper). **Cannot be scripted blindly** — `#c8922a` appears in SVG `fill="…"`
  attributes and JS chart-color maps where `var()` doesn't resolve. Needs a
  context-aware (CSS-only) sweep.
- **`/api/osint-region-context` returns 500** (`api/main.py:1326`) and
  `osint_region.html`'s `loadAlerts()` reads `data.items` while `/alerts` returns
  a bare array when unpaginated — so `/region/<name>` alert lists render empty.
  **Pre-existing backend bug**, surfaced during Phase 5; not touched (out of the
  UI scope).
- **`/sector/<name>` case-sensitivity 404** — the client lowercases the sector
  ("Defense"→"defense") and `/api/intel/sector/defense` has no match, so the page
  shows "Unknown sector". Pre-existing; verify against real linked sector values.
- **`insiders.html` has no `<table>`** — it's a card list, so the Phase-3 table
  hardening didn't apply. A rigorous insiders *table* view would be a restructure
  (follow-up), not styling.
- **Globe follow-ups (deferred, non-blocking):** lat/lon graticule hairlines and
  on-hover dot-expand tooltip. The click-to-open side panel already covers detail.

## Infrastructure

- **Database backups:** Automated locally (verified compressed daily snapshot,
  integrity-checked, tiered retention — `scripts/db_backup.py`, `feat/db-backup
  -automation`, merged to main). No remote/off-volume storage yet — still the
  single biggest residual risk, since local backups share the same Railway
  volume as the primary DB (same failure domain). Blocked on a cloud storage
  decision (see below). **Restore procedure verified 2026-07-21** — both the
  preferred `snapshot_*.db.gz` and the fallback raw copy restore to a complete,
  integrity-checked DB via `RESTORE.md` (tested against a scratch copy; live DB
  untouched). So the residual risk is now purely the *missing off-volume copy*,
  not an unproven restore path.

## Decisions Pending

- **Theme Temperature architecture:** Circularity guard design. Deferred until
  a joint design session. (SCOPE_IPHONE15_VISION.md, Layer 2)

- **Cloud storage provider:** Backups automation needs remote credentials
  (Backblaze B2, Cloudflare R2, or other S3-compatible store). User has opted
  to provision this themselves; `scripts/db_backup.py`'s `upload_remote()` is
  already storage-ready — it activates the moment `BACKUP_S3_ENDPOINT` /
  `BACKUP_S3_BUCKET` / `BACKUP_S3_ACCESS_KEY_ID` / `BACKUP_S3_SECRET_ACCESS_KEY`
  are set and `boto3` is added to requirements.txt. No code change needed,
  just the credentials.

## Awaiting review / merge (complete, not merged)

Confirmed by the 2026-07-21 reconciliation: both branches are complete,
pushed, and in sync with origin. Not merged — only `fix/rule10-emit-alerts`
was ever pre-approved (and it is already merged). These wait for review:

- **`feat/llm-fallback`** (`9f77654`) — Groq primary/fallback narrative
  generation. Ready.
- **`fix/remove-dead-generate-brief-job`** (`d3687eb`) — removes the dead
  `generate_brief.py` cron entry. Ready.

## Open action items (no code — production config)

- **Add `GROQ_API_KEY_FALLBACK` to the Railway production environment.** The
  fallback code is deployed-ready but the env var only exists in the local
  `.env` today, so the secondary provider is inert in prod until this is set.
  (Depends on merging `feat/llm-fallback` first.)

## Resolved (kept for the audit trail)

- **Design-pass partial coverage + stale brief cache — RESOLVED 2026-07-23**
  (on `fix/design-pass-regressions`, awaiting review). The fey-slash pass had
  tokenized only 5 pages + the brief; the other 23 nav-reachable pages (incl.
  `/theses`) kept the old amber/IBM Plex, and `/` served a pre-deploy cached
  brief. Fixed: all 23 pages tokenized (pure consolidation, no value changes);
  brief cache made template-version-aware (`TEMPLATE_VERSION` + marker +
  non-blocking regen on `/`). No features/nav were ever removed — verified live.
  Process fix in [[2026-07-23-design-pass-regression-postmortem]]: a Phase-0
  route-inventory audit is now the acceptance gate for any design pass.
- **RULE_10 argparse contract — RESOLVED 2026-07-20.** Fixed AND merged to
  main (`fix/rule10-emit-alerts`, commit `6ea6a7a`); confirmed live in
  production — clean hourly runs since deploy, 0 failures. Root cause: RULE_10
  was broken for its *entire* scheduled lifetime (~13.5 days, 2026-07-07
  onward) before this fix — 100% failure rate, zero automatic corroboration
  alerts in that window. Not retroactively recoverable, but the exposure is
  now closed and documented.
- **Groq LLM fallback — IMPLEMENTED, awaiting merge.** `jpt_common
  .generate_narrative()` retries the primary Groq key twice, then falls back
  to a secondary key (`GROQ_API_KEY_FALLBACK`), logging
  `provider=primary|fallback|none` to `activity_log` every call. Verified live
  end-to-end against the real fallback key. On `feat/llm-fallback` (see
  "Awaiting review" above); prod env var still needed (see "Open action
  items").
- **`generate_brief.py` dead cron entry — FIXED, awaiting merge.** Was
  registered at the wrong path, 100% failure since 2026-07-10 (~11 days) —
  superseded by `scripts/morning_brief.py`, so the entry was removed rather
  than path-fixed. On `fix/remove-dead-generate-brief-job`.
- **Disk usage at 92% — reported fixed (resized to 5GB).** *(Not independently
  verified this session — carried over from a prior audit.)*
- **pdfplumber / pillow missing — FIXED** (added to requirements.txt, merged).

---

See also: [[Roadmap Tracking]], CLAUDE.md Known Issues section
