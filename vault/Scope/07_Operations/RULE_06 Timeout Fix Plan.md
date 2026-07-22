---
aliases: [RULE_06 Timeout Fix Plan]
type: fix-plan
status: proposed
priority: high
tags: [operations, incident, rule-06, form4, human-gated]
related: [[Production Health]], [[Current Blockers]], [[Master Plan]], [[RULE Design Decisions]]
date-created: 2026-07-22
---

# RULE_06 Timeout — Fix Plan (proposed, awaiting approval)

> **Status: design only.** `rule_06_form4.py` is a rule script — **human-gated,
> DATA-LOSS class** (see [[RULE Design Decisions]] / `Scope/CLAUDE.md`). Nothing
> here is implemented. This plan exists for review before any code or prod change.

## Problem

`RULE_06` (SEC Form 4 insider trades) **times out every run** in production —
CRITICAL `SCHEDULER_JOB_FAILURE` rows (`rule_06_form4.py timed out after 300s,
subprocess.TimeoutExpired`), observed 2026-07-22 at 08:27 and 10:27. RULE_06 has
**no successful activity row** in the live system. SEC Form 4 data is therefore
**not being collected**.

## Root cause

The job re-scans a rolling **7-day** window and fetches **every** Form 4 filing in
it, serially, on a **2-hour** cadence with a **300s** kill limit. The workload is
5–8× too large to finish:

- `--since` defaults to *7 days ago* (`rule_06_form4.py:386`).
- EDGAR reports **~1,950 Form 4 filings** in a 7-day window (measured 2026-07-22).
- `_get()` sleeps **0.5s before every request** (`:41`) + ~0.3s round-trip ⇒
  **~0.8s per filing**; each filing over $50k triggers `historical_avg()`
  (`:333`) = **1 + up to 5** more fetches.
- **1,950 × 0.8s ≈ 26 min** base pass vs a **300s** timeout ⇒ killed after ~15% of
  the window, **every run**.
- `record_activity()` is the last line (`:375`), *after* the loop, so the timeout
  kills the process before it can log — which is why no RULE_06 activity row exists.
  (Alerts *can* still be inserted mid-loop — `insert_alert` commits per row — so a
  few RULE_06 alerts may exist despite zero activity rows.)

Full diagnosis: [[Production Health]].

## Constraints (must respect)

- Human-gated: requires explicit approval to merge/deploy.
- Keep RULE_06 on **write path (b)** (raw INSERT + `enrich_scores` backfill). Do
  **not** touch scoring, novelty, severity thresholds, or the alerts schema.
- Migrations are **additive-only**, tracked in `scope_migrations`, guarded.
- SEC fair-access limit is ~10 req/s with a contact `User-Agent` (already set).

## Fix design — two phases

### Phase A — bound the work (no schema change) ← the actual timeout fix

1. **Time budget.** Capture `t0` (already done, `:311`). In the main loop, if
   `time.time() - t0 > BUDGET` (propose **240s**), `break`, then fall through to
   `record_activity(...)` and exit 0. Guarantees a clean finish + an activity row,
   and stops the safety-net CRITICALs.
2. **Shrink the default window.** Replace the fixed 7-day default: derive `since`
   from the last successful RULE_06 `activity_log` run (`MAX(run_at)`) minus a
   1-day overlap, clamped to `[2d, 7d]`; fall back to **2 days** when there's no
   prior run. (Once Phase A lands, activity rows start recording, so the watermark
   becomes real from the next run on.)
3. **Reduce `SLEEP`** 0.5 → **0.15s** (~6 req/s, within SEC's 10/s).
4. **Memoize `historical_avg` by `owner_cik`** within a run (in-process dict) so a
   repeat filer in the same window isn't re-fetched.

*Expected result:* a ~2-day window ≈ 300–500 filings × ~0.3s ≈ **90–150s** —
comfortably under a 240s budget, with room for the historical fetches.

### Phase B — true incremental (additive migration) ← removes redundant refetch

1. **New table** (migration `mNNN`, additive, guarded):
   `rule06_seen(adsh TEXT PRIMARY KEY, seen_at TEXT DEFAULT (datetime('now')))`.
2. Each run still queries the search window (to catch late-indexed filings) but
   **skips any `adsh` already in `rule06_seen`**, and records processed `adsh`
   after handling (or at the time-budget break). Each run then processes only
   genuinely-new filings (~tens per 2h), and is **resumable** across budget breaks.
3. *(Optional, defer)* persist a per-owner `historical_avg` cache with a TTL if
   history refetching is still material after B.

## Explicitly unchanged

Scoring / novelty / opportunity, `enrich_scores` backfill, alert dedup
(`alert_exists`, `:274`), severity thresholds, the `alerts` schema.

## Verification plan

- **Local dry-run:** `python3 rule_06_form4.py --since <2-days-ago>` (no
  `--emit-alerts`) → completes under budget, prints counts.
- **Local with scratch DB:** `DATABASE_PATH=/tmp/rule06_test.db
  python3 rule_06_form4.py --since <2-days-ago> --emit-alerts` → confirms a
  `record_activity` RULE_06 row is written and no timeout.
- **Regression:** run each `tests/test_*.py` (project convention). Consider adding
  `tests/test_rule06_time_budget.py` (asserts the loop honours the budget).
- **Prod post-deploy:** watch `/api/activity-log` for a RULE_06 row with
  `duration < budget`, and confirm `SCHEDULER_JOB_FAILURE` for `rule_06_form4.py`
  **stops appearing**.

## Rollout & rollback

- Branch `fix/rule06-incremental-window`. Ship **Phase A first** (no schema),
  verify in prod, then add **Phase B** only if redundant refetching still matters.
- **Rollback:** revert the commit. The Phase-B table is additive and can be left in
  place harmlessly.
- Merge/deploy is **human-gated** (rule + migration = DATA-LOSS class).

## Effort / risk

| Phase | Scope | Effort | Risk |
|---|---|---|---|
| A | single file, ~30–40 lines | small | low |
| B | + additive migration + seen-set | moderate | low–moderate |

## Open questions (need your call before coding)

1. Time budget value — **240s** OK (60s headroom under the 300s kill)?
2. Minimum window floor — **2 days** acceptable, or wider for safety on
   late-indexed filings?
3. Do Phase B now, or ship Phase A and measure first? *(Recommendation: A first.)*
