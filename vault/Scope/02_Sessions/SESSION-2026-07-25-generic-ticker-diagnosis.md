---
type: session-summary
stage: iPhone-1
status: completed
priority: high
tags: [session, work-log, diagnosis, surfacing, scoring, read-only, backfilled]
related: [[Master Plan]], [[Scoring System]], [[Current Blockers]]
date-created: 2026-07-25
---

# Session: generic-ticker surfacing diagnosis

> **Backfilled 2026-07-26.** This session predates the standing rule that every
> session gets a note. The full deliverable is
> `04_Known_Issues/generic_ticker_surfacing_diagnosis.md`; this note is the
> session-level record pointing at it.

**Date:** 2026-07-25
**Branch:** `diagnose/generic-ticker-surfacing` (commit `d1bb40f`)
**Status:** Completed. Read-only. Not merged.

## Goal

Characterise *why* the daily report and war room surface generic, high-liquidity
tickers — with numbers from real queries, not a redesign. Diagnosis only.

## Outcome

Done, with the framing question overturned. **Full findings:
[[generic_ticker_surfacing_diagnosis]]**.

## What changed

Nothing but the findings report. No scoring, rule, threshold, or surfacing code
was touched; no migrations run.

## Findings — CONFIRMED (headline set)

- **The convergence layer emits nothing.** `RULE_10` alerts = 0, `themes` = 0,
  `theme_signals` = 0. The brief's question ("is convergence measuring signal or
  popularity?") could not be answered as posed, because nothing is surfaced *as*
  convergence. This became the seed for
  [[SESSION-2026-07-25-rule10-convergence-trace]].
- **Surfacing amplifies concentration 3.2×.** Brief top-10 share 37.6% vs 11.8%
  in the eligible-rule fired population. Defense primes take 22.7% of brief slots
  while being 3.9% of eligible fired alerts.
- **Mechanism identified:** `scripts/generate_brief.py:36-41` hardcodes RULE_11 to
  sort priority 2, over a feed whose entire ticker universe is 13 names, 62.5%
  defense primes.
- **Novelty decay works but is inert** — SPY 0.154, XOM 0.591→0.163 in three
  weeks. No user-facing list orders on `opportunity_score`; the ones that do read
  the empty `themes` table.
- **Two scoring terms are dead:** `absorption_pct` = 0.0 on all 3,347 rows;
  `evidence_confidence` has exactly 3 distinct values, encoding only the rule name.
- **Calibration is skewed:** 324 complete outcomes, 135 (42%) a single ticker
  (SPY) from RULE_07 — a rule excluded from corroboration. Largest non-SPY cell
  n=8. No rule has enough for a win rate.

## Findings — SUPERSEDED

- The report flagged 28 RULE_10 emissions with no surviving rows as
  **DATA-LOSS class**, and cited "~62% of alert rows missing". **Both were
  retracted the next session** — they were test fixtures and documented purges
  respectively. See [[SESSION-2026-07-25-rule10-convergence-trace]]. Read the
  original report with that correction in mind.

## Provenance

Local snapshot `Scope/data/jpt.db` only; production was unreachable. Surfacing
history is not persisted (`daily_briefs`, `digests`, `themes` all empty), so the
surfaced-set figures were produced by re-running the surfacing SQL per historical
day, not by reading what was actually shown.

## Human-gated

Diagnosis only — no fix proposed or applied, by design.

---

### Related

[[generic_ticker_surfacing_diagnosis]],
[[SESSION-2026-07-25-rule10-convergence-trace]], [[Scoring System]]
