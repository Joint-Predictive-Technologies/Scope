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

## 🔴 READ THIS FIRST — the local database is NOT production (2026-08-11)

    Scope/data/jpt.db  (LOCAL)   171 contracts ·  3,347 alerts · 0 themes
    /app/data/jpt.db   (PROD)    518 contracts · 35,705 alerts · 1 theme

**A query against the local file tells you nothing about production.** On 2026-08-11 several
remap scripts were run against the local file and their success was read as production being
fixed. It was not. The real fixes landed later the same night, executed inside the deployed
container.

**How to reach prod:** `railway ssh` — the CLI at `/opt/homebrew/bin/railway` is
authenticated and this repo is linked to project `respectful-generosity` → service **Scope**
→ environment **production**, volume `scope-volume-tHBX` at `/app/data`. Open the DB
`file:/app/data/jpt.db?mode=ro` for verification work.

⚠️ **If that path is unavailable to a session, the correct output is `UNVERIFIED` — never a
local number presented as prod.**

⭐ **The tell that a remap actually applied is its pre-image table**, not a headline figure.
Each remap creates one on `--apply`. Note the trap: the **local** file now carries
`rule09_ticker_remap_backup`, `rule01b_first_touch_retraction_backup`,
`rule01b_ticker_validation_backup`, `rule01b_direction_backup` **and
`rule02_directional_remap_backup`** — so check the tables *on prod*, not wherever you happen
to be connected.

🔴 **The `$51,269,205,263` heuristic is RETIRED.** It was the standing tell for "RULE_11 has
not been repaired". Verified against USASpending, that figure is the **true**
`total_obligation` of Humana's `HT940216C0001`, and `$48,063,737,196` is genuinely Lockheed's
Sandia award `DEAC0494AL85000`. The fabrication was one amount copied across *different*
award ids — now zero duplicate award_ids. Use `SUM(verified_at IS NULL)` instead.

See [[SESSION-2026-08-11-prod-deploy-verification]].

## Prod data state (2026-08-11) — what is repaired and what is not

**Repaired and independently verified on prod:** RULE_11 contracts (`verified_at IS NULL`
220 → 65), RULE_09 tickers (3 cleared, `IR` kept), and the full RULE_01B chain — chronology
(**694** retracted), ticker validation (**208** barred), direction (**1,432** corrected).

**Still outstanding on prod:**

- ✅ **RULE_02 #2 (ticker-resolution) applied on prod 2026-08-11**, barring exactly the six
  alerts that were keying the gate on symbols RULE_01B had refused (`US` ×4, `CA`, `HONAV`).
  **That residual is closed** — RULE_02 alerts on a barred symbol: **0**.
- 🛑 **RULE_02 #1 (directional-count), #3 (identity-dedup), #4 (cross-partisan) and the
  RULE_CLUSTER ticker-validity remap have still not run against prod.** #1's pre-image is on
  the local file instead. ⚠️ #1 and #2 are **order-independent by design**
  (`remap_rule02_ticker_resolution.py:24`) — #2 running first violated no guard.
- 🔴 **Three `_get_db_path()` call sites remain broken, and they are exactly the three scripts
  that have not run yet** — `remap_rule02_identity_dedup.py:83`,
  `remap_rule02_cross_partisan.py:105`, `remap_rule_cluster_ticker_validity.py:76`. ⚠️ **The
  cross-partisan one is NEW**: [[SESSION-2026-08-11-get-db-path-fix]] correctly found *four* on
  `main` at `052d081`, and merging `21e46ee` added a fifth. The fix is the same one-liner,
  `_get_db_path(None)`; the crash happens at `_connect`, before the database is touched.
- 🔴 **The gate still does not honour `lifecycle_stage`.** `RULE_10`'s candidate query never
  reads it, so **67 retracted RULE_01B rows across 58 tickers remain corroboration
  candidates**. Present exposure is nil — **0 fall inside the 14-day window** — so the risk is
  latent, not active. This must be decided before RULE_01B is signed.
- ⚠️ **RULE_01B signing is substantially, not fully, unblocked.** Verdict coverage went from
  **23 of 2,149 (1.1%)** to **1,469 of 2,163 (67.9%)**; among gate candidates, **299 of 366
  (81.7%)**. The 67 without a verdict are exactly the retracted rows above.
- ⚠️ **`repair_rule11_contracts.py` writes no pre-image table** — it is the only application
  in the chain with **no undo**, and its transition counts are unverifiable after the fact.
- ⚠️ **RULE_09's cleared alerts still print the removed symbol in their headlines**
  (`Lobbying spike: $DTGI …`). No gate effect — RULE_09 is context — but a live display
  residual.
- ⚠️ **Stale comment:** `remap_rule01b_ticker_validation.py:284` says 9 rows overlap the
  chronology set; the real figure on prod is **55**. The code is correct; the number is not.

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

- ~~**RULE_06 (Form 4) times out every run — data gap.**~~ **FIXED and
  LIVE-VERIFIED 2026-07-26**, merged to local `main` (not pushed). Both phases of
  [[RULE_06 Timeout Fix Plan]] shipped, and Phase B needed **no migration** — the
  seen-set lives in the existing `filings` table under `source='sec_form4'`.

  **Verified against real EDGAR on a throwaway DB** (working DB sha256 unchanged,
  0 rows written to it):
  - Run 1, cold start: 1,414 filings in the 7-day window; **stopped cleanly at the
    240s budget** instead of being killed at 300s; `status=partial`, 217 examined,
    8 alerts, and **an `activity_log` row was written** — the thing that never
    existed before.
  - Run 2: resumed at `217 already processed, 1197 to process`, examined 101 more,
    **0 duplicate doc_ids**. Incremental scan and resumability both confirmed on
    live data.

  ⚠️ **Operational note for deploy:** a cold start needs roughly **14 runs** to
  clear a 7-day backlog at ~217 filings per 240s run — about **28 hours** at the
  120-minute cadence. Expect `status=partial` rows for the first day; that is the
  design working, not a failure. `SCHEDULER_JOB_FAILURE` for `rule_06_form4.py`
  should stop appearing immediately.

  Still not machine-verified: whether the emitted alerts are *correct* against the
  underlying filings. Eight alerts from run 1 look structurally sound (real
  tickers, real officer titles, plausible multiples) but nobody has cross-read a
  Form 4 to confirm the arithmetic.

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

  **Candidate off-volume target identified (2026-08-03):** a friend's always-on
  box — ~1TB, console/CLI only (no GUI) — reachable over the network. This is a
  genuinely *different failure domain* from the Railway volume, which is exactly
  the residual gap (local snapshots today share the primary's volume). Checklist
  before it counts as mitigation:
  - [ ] **Integration path — decide.** `upload_remote()` speaks S3/boto3 only, so
        either (a) run an S3-compatible server on the box (e.g. MinIO) and the
        existing code works with just the four `BACKUP_S3_*` env vars, no code
        change; or (b) add an SFTP/rsync-over-SSH transport to `db_backup.py`
        (code change → human-gated, one workstream). (a) is the smaller move.
  - [ ] **Reachability:** stable address or tunnel from Railway's egress to the
        box (CLI/SSH only, no GUI); confirm it survives reboots.
  - [ ] **Encryption:** snapshot encrypted in transit and at rest — the DB is the
        moat and would live on third-party hardware.
  - [ ] **Retention/space:** budget the 1TB; confirm `db_backup.py`'s existing
        tiered retention applies to the remote copy, not just the local one.
  - [ ] **Re-test the RESTORE path *from the box*.** Standing discipline: restore
        is only proven when tested against a scratch copy pulled *back* from this
        target — not assumed from the local `RESTORE.md` run.

## Scoring-display and surfacing residuals (2026-07-26)

Found while fixing the surfacing ladders and the win-rate placeholder. **All are
pre-existing and none is a regression** — recorded here so they are not lost.

- **`opportunity_score_breakdown()["total"]` and `calculate_opportunity_score()`
  can disagree by 0.1.** The breakdown rounds each component then sums; the scorer
  sums then rounds once. Independently measured at **23.7% of uniform-random
  inputs** (e.g. novelty 0.8125 / absorption 37.5 / SHORT → breakdown `43.3`,
  scorer `43.2`). Verified **identical on `main`** — not introduced by any recent
  change. **Dormant today:** 0 of 3,347 scored alerts diverge, because
  `absorption_pct` is `0.0` on every one of them. It becomes visible — tooltip
  total disagreeing with the score printed beside it — the moment absorption is
  non-zero. Fix is to make one call the other, which is scoring code, so
  human-gated.
- **`test_war_rooms.py:112`'s immutability guard is weaker than it reads.** It
  asserts `decomposition["total"] == alert["opportunity_score"] == 65.0`, where
  `65.0` is a constant the fixture itself writes into the row — so it checks the
  breakdown's arithmetic, not that the breakdown agrees with the production
  scorer. Given the item above, that is exactly the divergence it cannot catch.
- **`api/static/thesis.html:183-190` duplicates the score formula in JavaScript.**
  Its own comment admits it "mirrors jpt_common.opportunity_score_breakdown". Now
  bounded: **0 divergence across all 3,347 real alerts and 100,000 synthetic
  inputs**, so this is maintenance drift, not a live numeric bug. The concrete
  vector is `:184`'s own `win=0.5` default — if the Python default changes, the JS
  silently keeps `0.5`. Right fix: serve the breakdown from the API and delete the
  JS copy.
- **`historical_win_rate` is a 0.0–1.0 fraction; the product's measured `win_rate`
  is a 0–100 percent.** Same word, different units, no range check. Passing the
  percent straight in multiplies the term by up to 100× and **pins every score at
  the 100 clamp**, while the tooltip row still reads "uncalibrated placeholder".
  The unit is now documented in the docstring and pinned by two tests
  (`fix/winrate-placeholder-honesty`), but the parameter still shares the name.
  Renaming touches scoring signatures → human-gated.
- **`scripts/send_digest.py:75` crashes on a blank ticker.**
  `(s.get("ticker") or "").replace("$","").split()[0]` raises `IndexError` for
  `None`, `""`, `"  "` and `"$"` — all four reproduced. It is called at `:133`,
  **before** the `try` at `:140`, so it escapes `run()` and **no email is sent at
  all**. 236 HIGH/CRITICAL rows in the local DB carry a blank ticker (RULE_09 182,
  RULE_11 54). Pre-existing. Measured whether the new score-ordering increases
  exposure: **it does not** — across all 14 rolling 48h windows with data, the old
  ladder put a blank-ticker row in the top 5 once, the new ordering zero times. Fix
  is a one-line guard, but it is on a surface with an unclear runtime (below), so
  it is worth confirming that first.
- **Is `send_digest.py` even running? UNVERIFIED — needs prod.** It has **no caller
  anywhere in the repo** and appears in neither `_RULE_SCHEDULE` nor
  `_CRON_SCHEDULE` in `api/main.py`; the only invocation documented is a crontab
  line in its own docstring. It also never calls `record_activity`, so there is no
  `activity_log` trace to check. To settle: inspect the deployment's crontab /
  Railway job definitions for `send_digest.py`, and confirm `GMAIL_FROM` and
  `GMAIL_APP_PASSWORD` are set — without them `send()` returns `False` immediately
  at `:123-125`. **If nothing invokes it, the surfacing fix on that file is inert in
  production.**
- **The `+5` placeholder term is still dead weight in the score.** Now honestly
  *labelled*, but it adds a flat +5 to every alert, compressing the usable range.
  Related: `opportunity_score` takes only **17 distinct values across 100 eligible
  alerts** in a 48h window, and in one measured case **11 of 20 surfaced rows
  shared both score and timestamp**, so their order fell through to raw `id`.
  Whether to zero the term or wire a real rate from `alert_outcomes` is a scoring
  decision.

### Generic-ticker promoters — ALL RESOLVED (2026-07-26)

Every surface now ranks by `opportunity_score`, and no rule holds a reserved slot.
All merged to local `main` (**not pushed**).

- **Ordering ladders removed** from the daily brief, the clusters war room, the
  email digest and the chat context. The prompt-level steer in `generate_brief.py`
  ("lead with insider, contract, congressional" whenever RULE_10 is empty — i.e.
  every morning) is gone too.
- **`chat.py` fixed with a severity floor.** Ranking by score alone was measured to
  evict most CRITICALs, so `severity IN ('CRITICAL','HIGH')` was added first and the
  ranking applied on top. Measured, 7d window: old `21C+4H` → score-only-no-floor
  `19M+3H+3C` (rejected) → **now `4C+21H`, no MEDIUM**. Note the honest consequence:
  within CRITICAL/HIGH the score decides, so most CRITICALs no longer lead — that is
  what ranking by opportunity means, and it matches the other surfaces.
- **The Gov Contracts card is gone as a guarantee.** `brief.html` renders only
  sections the brief produced; the prompt now omits a section whose rule did not
  fire instead of emitting filler. RULE_10's absence is still stated explicitly,
  deliberately.
- **The weekly digest** per-rule slots now pick highest-opportunity rather than most
  recent (the code said "most recent" while the prompt asked for "most notable"),
  and a new rule-agnostic `signal_of_week` section leads it.
- **Fixed in passing:** the prompt emitted `prediction` while the page read
  `prediction_markets`, and `lobbying` was never produced — so both cards showed a
  permanent "No data for this section." A test now asserts prompt and page agree.

**The rule that must survive all of this:** *score-ranking is only safe where a
severity floor exists.* `morning_brief.py`, `send_digest.py`, `generate_brief.py`,
`chat.py` and the digest's `top_signals` all have one. Any new surface must add the
floor before removing a ladder.

Still open, lower priority:

- **`rule10_is_valid` filtering runs in Python *after* `LIMIT 20`**
  (`generate_brief.py`), so invalid corroborations shrink the surfaced list below 20
  rather than being replaced. Needs the validity test pushed into SQL or a
  fetch-then-trim.

## Contracts surface (RULE_11) — UI/link bugs seen in prod (PARKED, not urgent)

Logged 2026-07-29 from observation of the live site **after** the RULE_11 repair
merged (`60d1802`). Presentation/link layer only — batch these two together in
one small session. **Neither blocks clusters**, and both are safe to run
alongside anything not touching RULE_11 or the contracts view. Pick up after the
clusters close-out and the existing rule-repair queue below.

**1. Contracts span months with no visible order — split into two claims.**

- *Dates spanning March, May, … is almost certainly the repair WORKING, not a
  bug.* Before it, award identity collapsed to `(recipient, run-day)`, so ~1
  award per recipient per run survived and the rest were silently dropped. The
  fix keeps every distinct award, and real federal awards carry their own
  disclosure dates spread across months. **Confirm before "fixing" anything:**
  are the displayed dates the real per-award `award_date`, or a fallback? The
  repair removed the run-date fallback — verify none survives on the *display*
  path (`api/routers/contracts.py`, and the contracts view).
- *No sensible order IS a real display bug.* The list should be sorted on a real
  field — award date newest-first, or amount largest-first — not left in
  ingestion/insertion order. Check the `ORDER BY` in the contracts API/view.
  (Note `api/routers/contracts.py` already accepts a `sort` param defaulting to
  `award_date DESC`/`amount DESC` — verify what the page actually requests.)

**2. Contract source links land on the USASpending homepage — REAL BUG.**
The URL is being built without a valid award identifier, so it resolves to the
site root. Possibly always broken, or it reads a field the repair changed: the
repair standardised identity on the stable per-award id
(`generated_internal_id`, which encodes the PIID) and fixed a backfill that had
decoupled `amount` from `award_id`. **Fix:** find where the contract source URL
is constructed (frontend and/or the API feeding it), make it use that stable
award id, and point at the USASpending award-detail URL.
*Note:* `alerts.html`'s `sourceUrl()` already deep-links RULE_11 alerts as
`https://www.usaspending.gov/award/<award_id>/` from tags field 3, falling back
to a recipient query — so the broken link is most likely on the **contracts
page** (`contracts.html`), not the alerts feed. Check there first.
**Verify:** take one live contract alert, read its stored `award_id`, confirm
the constructed URL contains it and resolves to that award — not the homepage.

**Read-only triage done 2026-07-29 — start here, it narrows both items a lot:**

- **The link builder is already correct.** `contracts.html:288` builds
  `https://www.usaspending.gov/award/${c.award_id}/` and falls back to a
  recipient search. So this is a **data** problem, not a URL-template problem:
  when `award_id` is empty the URL degenerates to `…/award//` (or an empty
  search) and USASpending serves its root. On the local DB **65 of 171 contract
  rows have no `award_id` at all** — those are exactly the rows whose links die.
- **Why prod still has them:** the RULE_11 repair's data-repair script
  (`scripts/repair_rule11_contracts.py`) is **manual and has never been run on
  prod** — only the code shipped. So prod still holds the pre-repair rows: the
  legacy no-award_id ones (dead links) plus rows whose `amount`/`award_id` were
  decoupled. Newly ingested awards will link fine. **Running the repair is
  probably the actual fix for item 2**, and it is the human-gated step already
  documented in [[SESSION-2026-07-28-rule11-contracts-repair]] (with 4 prod
  queries owed first, incl. the duplicate-award_id check).
- **Sorting is already parameterised**, so item 1's "no order" is narrower than
  it looks: `contracts.html:266` requests `/contracts/data?sort=${sortVal}` and
  `api/routers/contracts.py:65` maps that to `amount DESC` or `award_date DESC`
  (default `amount`). So check what `#f-sort` is actually set to on load — the
  likely bug is the control defaulting to something unhelpful, or the page not
  re-sorting after the new rows arrived, **not** a missing `ORDER BY`.
- **On the dates:** locally the pre-repair table still shows only **5 distinct
  `award_date` values** (the old run-date fallback). If prod now shows dates
  spread across months, that is the repaired rule writing real
  `Base Obligation Date`s — i.e. the expected behaviour, confirming item 1's
  first half is *not* a bug.

## Ingestion linker — PARKED (decision 2026-08-04)

The `resolve_by_company_name` fuzzy company-name linker is **parked**, not being
fixed incrementally. Rationale: even with the case-asymmetry repaired it resolves
at roughly **2:1 correct:wrong** on the new links a run creates (~57 correct / 27
wrong on the rows `resolve_transactions` touches), and each fix peels back another
layer (guard misfire on duplicate `company_name`s, a dedupe landmine, suspect
links). Crucially, **RULE_CLUSTER's validity set already defends the gate** from
linker garbage (a `ticker_id` only becomes a key if it validates), and the
diagnosis counterfactual proved correcting all existing mis-links moves the gate
by **zero**. So the linker does not need to be perfect for the gate to be honest.

**The real fix, when it's worth a schema change:** canonical-ID linking
(CIK/CUSIP) — match exact or leave NULL, no fuzz — the same pattern that fixed
RULE_11 (`generated_internal_id`) and RULE_09 (difflib ban). Its own human-gated
session; not urgent while the gate is defended.

**Branch disposition:**
- `fix/linker-casefold` (verified green, **UNCOMMITTED**) — ⚠️ **do NOT merge
  alone.** It is a correct *function* fix (case asymmetry, `FB`→`META` preserved,
  0 exact-path changes) but on its own it adds ~27 wrong `ticker_id` links for ~57
  correct, and it does **not** repair the stored rows it's named for (MMC→BHC stay
  wrong — `resolve_transactions` only visits `ticker_id IS NULL`; those need the
  option-E backfill). If ever revived, pair it with a suspect-link (token-overlap)
  guard first so it is a net gain. Full ledger: [[SESSION-2026-08-04-linker-casefold]].
- `fix/house-parser-no-drop` — **NOT parked; still deploy it** (it stops live
  data loss). ⚠️ **Coupling:** the parser's newly-kept rows must NOT be run
  through `resolve_transactions` while the linker is parked, or an `Agilent (A)`-
  style row mis-resolves (→ `SINT`). Safe posture: deploy the parser fix; do not
  manually run `resolve_transactions` over the new rows until the linker is
  addressed. `resolve_transactions` is human-gated and never scheduled, so this
  holds by default — just don't trigger it.

**Left open by the linker sessions (recorded, not fixed):** the ambiguity guard
misfires on duplicate `company_name`s (Regions Financial's correct 0.863 match
discarded as "ambiguous"); an obvious `company_names` dedupe silently relinks 104
congressional rows (pinned by test, do not "clean up"); the 45 existing mis-links
(option E backfill — gate-impact-zero, correctness-of-record only). See
[[SESSION-2026-08-03-ingestion-linker-diagnosis]].

## Rule-repair backlog (from the 2026-07-28 mechanical audit)

Read-only audit of the six rules NOT reworked this week (RULE_01, RULE_01B,
RULE_02, RULE_CLUSTER, RULE_09, RULE_11); every headline claim re-derived by an
independent verifier pass (15/15 upheld). Findings table:
[[SESSION-2026-07-28-rule-audit-congressional-lda-contracts]]. Nothing fixed in
that session — recorded here so the fixes are not lost. All counts are from the
local snapshot DB and need a prod re-run before external use.

**The through-line:** the audit found the same bug class the reworked rules
fixed — *identity reconstructed from a projection (fuzzy name-match, run-date
fallback, insertion order, headline string) instead of a canonical id* — sitting
in the rules that were never re-scrutinized. Three are LIVE and writing corrupted
identities into corroboration-eligible streams. The gate has never fired, so no
fake convergence has shipped — but a first fire built on this data would be
false. This is the next real work after the cap fix and cluster rewrite.

Ranked by live damage (repair in this order):

1. ~~**RULE_11 (contracts) — WORST.**~~ **REPAIRED 2026-07-28 on
   `fix/rule11-contracts-repair` (2 commits, UNMERGED — human-gated).** Identity
   is now `generated_internal_id`; the table is keyed on the award, not
   `(recipient, run-day)`; the sweep uses `date_type=new_awards_only` so
   "awarded $X" is true; coverage is all 270 newly-signed ≥$50M awards instead
   of the year's top ~150 by lifetime size; severity 102/102 CRITICAL → new
   awards MEDIUM 36 / HIGH 33 / CRITICAL 5. Stored data repaired forward-only
   from source (106 rows re-derived, verifier matched 376/376; 61 unattributable
   rows cleared; 62 alerts corrected, 40 retracted). See
   [[SESSION-2026-07-28-rule11-contracts-repair]] — includes 4 prod queries owed
   before the migration runs there. Original finding, for the record:
   `award_date` falls back to the run date, so
   the dedup key collapses to `(recipient, run-day)`: Boeing/RTX/Huntington
   stored 5× at identical amounts, every award after a recipient's first per run
   silently dropped. Verifier found worse: a backfill overwrites `award_id`, so
   a stored row's amount and its `award_id` describe **different contracts**.
   Only sees the year's top ~150 awards (min stored $2.73B); headlines lifetime
   values as "awarded $X" → all 102 alerts CRITICAL. A live convergence
   instrument feeding incoherent identities.
2. **RULE_09 (lobbying) — two live defects.** (a) Ticker attribution
   fuzzy-matches company names (difflib @0.7), **wrong 42.6%** (92/216):
   IBM→$VIRC, HEXCEL→a bank ($HBIA), RELX→$ARDX. The project already **banned
   this method for RULE_11 and remap-migrated the damage (m003/m004) — RULE_09
   never got either.** The fix is known, just not applied. (b) Cannot finish in
   the 300s scheduler cap — observed runs of 3,520s/1,909s/1,080s (the LDA API
   silently caps pages at 25 rows; a sweep is ~6,650 requests). Same failure
   class as RULE_06. Alerts commit per period but the activity row writes only
   at the end, so a killed run leaves alerts unlogged (554 vs 45 accounted).
3. **RULE_01B (congressional first-touch) — LIVE, corroboration-eligible.**
   First-touch by insertion order not chronology → 20.3% of alerts falsely
   claim "no prior trade"; the 90-day window filters `transaction_date` not
   `filing_date` → 495 late-filed first-touches never alert (late filers are
   the target population); direction hardcoded "opens new position" → 45.8%
   are actually sales/exchanges; unvalidated PDF-artifact tickers (`NY`, `LLC`,
   `THE`, …) are live corroboration keys (`NY` already spans two members).
4. **RULE_02 (congressional cluster).** Counts exchanges as directional (a live
   HIGH says "3 members sold WAT" — one did; 8/82 overstate); groups on raw
   parse strings (`ticker='US'` alerts exist); identity key is the headline
   string (blind to member set — the insider-cluster bug class); novelty anchor
   LIKE-matches substrings (`'%US%'` hits 82/82 headlines, corrupting novelty
   for every short ticker).
5. **RULE_08 (Federal Register) — restore `fed-register` as an HONEST instrument.**
   Added 2026-07-29. Same disease as items 2/3/4 — *attribution from a projection* —
   in its purest form: `rule_08_federal_register.py:26`'s `SECTOR_MAP` fans a
   **keyword** in a document's title/abstract out into a hardcoded ticker basket
   (`"bank"` → JPM/BAC/WFC/GS). The word "bank" appearing in a proposed rule is not
   evidence JPMorgan is involved in it. **RULE_08 is now in `RULE_10_EXCLUDED`** (this
   session) so the basket cannot complete a convergence; the rule still runs and still
   emits, it just contributes no gate leg. **The fix is real issuer attribution** —
   derive the ticker from the entities the *document itself* names (agencies, docket
   parties, commenters, the regulated entities in the text), the same key/value
   direction the structural detector treats as correct
   (`tests/test_basket_rule_gate_class.py::test_the_INVERTED_map_is_cleared_because_of_its_DIRECTION`).
   ⚠️ **Note the history so it is not repeated:** a prior session deliberately split
   RULE_08's composite ticker (`"LMT RTX NOC"` → three single-symbol alerts) precisely
   to make `fed-register` a real, matchable instrument — which is what armed the
   problem. The split is **correct and retained**; it is the groundwork this item
   needs. The one alert per symbol shape stays, the *source of the symbol* is what
   changes. Until then the exclusion holds, and it has a real cost: convergences that
   would have counted a `fed-register` leg no longer fire. That cost was accepted at
   sign-off. See [[SESSION-2026-07-29-rule08-exclude]] and
   [[SESSION-2026-07-29-basket-rule-gate-class]].
6. **RULE_01 — keep dormant (no action).** Not a real rule: a dormant label in
   `ingest_senate.py`, unscheduled, never run, member matcher scored 0/60 in
   simulation. Do NOT enable without the 10 preconditions in
   [[SESSION-2026-07-28-rule01-rule01b-mechanical-audit]].

### Signed-signal follow-ups (from the 2026-07-30 signed-leg session)

Step 1 of the signed-signal engine is built on `feat/gate-direction-insider-contracts`
(unmerged): insider counts only on a genuine open-market buy, contracts is weighted by
award-size-relative-to-cap. Design for the rest: [[signed-signal-engine]].

⚠️ **Each item below is blocked behind a REPAIR, and the order is the point.** Signing a
leg attaches a confident interpretation; attaching one to data that names the wrong company
makes a *future* false convergence look **more** credible than an unsigned one. Encoded, not
just written: `jpt_common.SIGNED_RULES` is asserted to be exactly `{"RULE_06"}`, and
`test_the_UNSIGNED_instruments_are_completely_untouched` fails and names any rule signed
without that decision being taken deliberately.

6. **Sign earnings (RULE_15) — surprise vs the entity's own history.** Good earnings for the
   Nth quarter running is priced in (low weight); a break in the streak is signal; a
   small-cap turning good after a bad stretch is the sharpest case. **BLOCKED behind
   RULE_15's attribution repair** — its "+2557% QoQ" on RTX used a denominator from
   *Artiva Biotherapeutics'* 8-K where "RTX" means **rituximab**; the true RTX-to-RTX figure
   is −35.4%. You cannot measure surprise-vs-history until you are sure whose history you
   are reading. Specific dependency: the `history` path (`rule_15_earnings_nlp.py:297-303`)
   does not filter `ingested_at` and produced a fabricated alert **five days after** the
   CIK repair shipped.
7. **Sign RULE_01B — a buy counts, a sell is ambiguous.** Do NOT count a sell as bullish
   corroboration (the ONDS case: sold to pay taxes, not a bearish signal). Inferring *why*
   a sale happened is a harder later layer — do not smuggle it in with the easy half.
   **BLOCKED behind RULE_01B's direction repair** (item 3): ~46% of sales are mislabelled as
   opens, so signing now would invert the sign on nearly half the population.
8. **Lobbying (RULE_09) / institutional (RULE_16) — PARKED, not blocked.** "Which lobbying
   filing implies which ticker is bullish" is a **thematic association**, the same reasoning
   shape as the OSINT basket disease. Needs skepticism and a falsification test, not a quick
   sign. 13F is 45 days stale by construction.
9. **`resolve_contractor`'s token-matching fallback has a live false positive.** `SPCX` →
   "SPACE EXPLORATION TECHNOLOGIES CORP" (SpaceX is private; `SPCX` is an unrelated listed
   vehicle whose `tickers.company_name` reads the same — all four SEC share concepts 404 for
   its CIK). 3 alerts, $3.05B. The path hardcodes `conf = 90` while curated overrides carry
   80-99, so **no threshold can separate them** and it cannot be tuned out. Same path mapped
   RAYTHEON→HNST once before (hence migrations m003/m004). Contained for now by the
   cap-weight asymmetry — a token-matched recipient can never be *boosted* — but the wrong
   ticker is still on the alert.
10. **Two pre-existing test defects found while doing this, neither caused by it.**
    (a) Real order-dependence: with test files reversed, 4 tests fail
    (`test_exclusion_single_source::test_divergence_is_impossible_not_merely_absent` plus 3
    in `test_check_convergence`) because `importlib.reload(r10)` rebinds function objects
    that other modules hold by identity. **Reproduced on a clean `main` worktree.**
    (b) `test_market_cap_plausibility`'s 541-day boundary tests fail nightly in the
    CEST-offset window: they build `as_of` from `date.today()` (local) while
    `rule_reddit_collector.py:286` uses `datetime.now(timezone.utc).date()`. Both cheap.

**Cross-cutting free win:** the **LIKE-substring novelty anchor** (item 4) is
not RULE_02-only — `calculate_novelty_score` matches `headline LIKE '%<anchor>%'`
for every rule that anchors on a ticker, so any short ticker is exposed. Fix it
**once, centrally**, and several rules clean up together. Worth doing early.

**Prod queries still owed** (run when next touching prod; full SQL in the three
sub-audit notes):

- Has any artifact ticker (`NY`, `LLC`, `THE`, …) already opened a real
  theme/corroboration — i.e. a live *fake* convergence?
- Live magnitude of RULE_01B's late-filing drop since the snapshot.
- RULE_09's per-period-commit orphan count (alerts vs `activity_log` accounting)
  and whether 03:00 runs are dying in the `SCHEDULER_JOB_FAILURE` net.
- Confirm RULE_01 dormancy in prod (0 alerts, 0 INGEST_SENATE activity rows).
- **RULE_08 exclusion remediation (owed the moment this branch reaches prod).** The
  exclusion is **forward-only** — it does not retract themes RULE_08 already helped
  complete. Every row this returns is a convergence a lookup-table ticker helped build,
  and must be **hand-reviewed and retired by hand**:
  ```sql
  SELECT t.id, t.ticker, t.created_at, GROUP_CONCAT(DISTINCT ts.rule) AS legs
  FROM themes t JOIN theme_signals ts ON ts.theme_id = t.id
  GROUP BY t.id HAVING SUM(ts.rule = 'RULE_08') > 0;
  ```
  Zero rows locally (0 RULE_10 rows in the snapshot DB), so live magnitude is
  **UNVERIFIED — needs prod**. Sibling query worth running alongside, for the
  convergences that RULE_08 was *about* to complete: any ticker with a live RULE_08 leg
  plus exactly two other instruments inside the 14-day window is a fire that silently
  will not happen now — expected and intended, but worth knowing the count.

**Suggested repair order:** the central LIKE fix first (cross-cutting), then
RULE_11 → RULE_09 → RULE_01B → RULE_02 — after the cap fix and cluster rewrite
land. RULE_CLUSTER needs no urgent fix (mechanically sound; medium items queued
in [[SESSION-2026-07-28-cluster-rules-mechanical-audit]]). All of these touch
rule scripts / scoring / migrations → **human-gated; one fix per session.**

## Awaiting review / merge (complete, not merged)

Confirmed by the 2026-07-21 reconciliation: both branches are complete,
pushed, and in sync with origin. Not merged — only `fix/rule10-emit-alerts`
was ever pre-approved (and it is already merged). These wait for review:

- **`feat/llm-fallback`** (`9f77654`) — Groq primary/fallback narrative
  generation. Ready.
- **`fix/remove-dead-generate-brief-job`** (`d3687eb`) — removes the dead
  `generate_brief.py` cron entry. Ready.

**Added 2026-07-26 — four more, all local-only (never pushed), all independent:**

- **`fix/rule06-reliability`** (1 commit) — RULE_06 completes inside the 300s
  timeout and logs activity on every observable path. Needs sign-off on writing
  the seen-set into the shared `filings` table.
- **`fix/surfacing-opportunity-sort`** (2 commits) — brief + clusters war room rank
  by `opportunity_score`; the prompt's "lead with insider, contract" steer removed.
- **`fix/winrate-placeholder-honesty`** (1 commit) — the `base win-rate 0.5`
  tooltip row relabelled as an uncalibrated placeholder. Labelling only.
- **`fix/surfacing-sibling-ladders`** (3 commits) — email digest ranks by
  `opportunity_score`. **`chat.py` and `digest.py` deliberately SKIPPED** (above);
  the chat change was made, measured, and reverted in-branch. Ran unattended.

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
