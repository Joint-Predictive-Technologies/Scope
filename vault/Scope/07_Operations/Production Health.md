---
aliases: [Production Health]
type: operations
status: living
priority: high
tags: [operations, production, monitoring, health]
related: [[RULE_06 Timeout Fix Plan]], [[Current Blockers]], [[Master Plan]]
last-checked: 2026-07-23
---

# Production Health

Live-system health log for Scope in production. Re-run the checks below and append
a dated snapshot whenever you verify the deployment.

- **Prod URL:** `https://scope-production-1c3a.up.railway.app`
- **Platform:** Railway (FastAPI + in-process APScheduler)

## How to check (read-only)

```bash
B=https://scope-production-1c3a.up.railway.app
curl -s "$B/health"                       # db present, alert_count, groq_key_set
curl -s "$B/api/scheduler-status"         # scheduler running?, job_count, per-source last_run
curl -s "$B/api/stats"                    # counts, data_through
curl -s "$B/api/activity-log?limit=200"   # recent rule runs (scanned/flagged/emitted) + failures
```
The `SCHEDULER_JOB_FAILURE` source in the activity log is the universal safety net
— any row there is a job that failed (exit code / timeout / import crash).

## Snapshot — 2026-07-23 (post-deploy: UI restoration + completion)

**Verdict: healthy; the UI restoration session is live and verified in prod.**

- **Deploy:** merged `fix/ui-restoration-and-completion` → `main` (`aad9e9d`, 9
  commits incl. the design-pass-regression base) and pushed; Railway auto-deployed
  from `main`. 133 tests passed pre-merge.
- **Health:** `ok`; DB present at `/app/data/jpt.db`; **24,835 total alerts**;
  Groq key set. **Scheduler running, 32 jobs.**
- **New code confirmed live (read-only curls):**
  - `/` brief marker = `scope-brief-template:ui-restore-3` → the version-aware
    cache regenerated on deploy; full nav + convergence hero + ticker belt +
    activity strip all present.
  - `/feed`: card-stagger motion present; AI "Analysis" block gone.
  - `/osint`: new globe severity color `0xe06868` (was `0xff2020`).
  - `/digest` (Accept: text/html) serves the tokenized page (shadow fixed);
    `/brief` links `/tokens.css`.
- **Known residuals (non-blocking, tracked in [[Current Blockers]]):** 476 legacy
  inline-hex (context-aware sweep pending); pre-existing `/api/osint-region-context`
  500 and `/sector` case-sensitivity.

## Snapshot — 2026-07-22

**Verdict: healthy and actively collecting data — with one broken rule (RULE_06).**

- **Health:** `ok`; DB present; **21,935 total alerts**; Groq key set.
- **Scheduler:** **running, 32 jobs**, next runs all scheduled correctly.
- **Collection (08:22–11:17 UTC window):** **17 distinct sources ran, 180 alerts
  emitted**; data fresh through **11:07**. Examples: `RULE_OSINT` 1,492 scanned →
  15 emitted; `RULE_07` 2,100 → 3; `RULE_REDDIT` 732 scanned; `SCORING`/enrich
  running every 10 min.
- **Not stale (looked it up):** `RULE_12`/`RULE_14` are weekly/biweekly crons (last
  ran 07-20 / 07-21 as scheduled). The duplicate *space-labelled* sources
  (`RULE OSINT`, `ENRICH SCORES`) frozen at 07-10 are just legacy label history —
  the current underscore versions run fine.
- **Restore drill:** the DB restore procedure was verified end-to-end on 2026-07-21
  (both `snapshot_*.db.gz` and the raw fallback restore clean; `integrity_check=ok`)
  — see [[Current Blockers]] / [[Master Plan]] Phase 0.

### ⚠️ Open issue — RULE_06 (Form 4) timing out

`rule_06_form4.py` fails **every run** with `TimeoutExpired` after 300s (CRITICAL
rows at 08:27 and 10:27). Root cause: it re-scans a rolling **7-day** window
(~1,950 filings) serially with a 0.5s per-request sleep on a 2-hour job — ~26 min
of work against a 300s kill limit, so it never finishes and never records activity.
**SEC Form 4 data is not being collected.** The safety net is catching it (working
as designed).

→ Diagnosis + remediation: **[[RULE_06 Timeout Fix Plan]]** (proposed, human-gated,
awaiting approval).
