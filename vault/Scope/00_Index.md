---
type: index
status: active
tags: [index, map-of-content]
last-reviewed: 2026-07-22
---

# Scope — Vault Index

Knowledge base for Scope, the political-market intelligence terminal. Start with
the Master Plan; everything else is detail hanging off it. The vault is organised
by domain (numbered folders): **plan → how it works → what's live → what's
broken → what we decided → the moat → sessions.**

## Start here

- **[[Master Plan]]** — the living long-term plan: what Scope is at the end, the
  phased path from here to there, the constraints it respects, and the risk
  register. **The source-of-truth roadmap.**

## `03_Roadmap/` — the plan

- [[Master Plan]] — North Star + phases 0→4 (living).
- [[iPhone Stage Progress]] — stage-by-stage status against the maturity ladder.
- [[Roadmap Tracking]] — short-horizon: what's in flight this week (+ Feature Candidates backlog).
- [[Competitive Positioning]] — Scope vs. QuiverQuant; compete on convergence, not dataset breadth.

## `01_Architecture/` — how it works

- [[Scoring System]] — Evidence Confidence + Opportunity, how they're computed.
- [[RULE Design Decisions]] — rule mechanics, thresholds, score immutability.

## `07_Operations/` — production health & fixes

- [[Production Health]] — live-system health log + how to re-check the deployment.
- [[RULE_06 Timeout Fix Plan]] — proposed remediation for the Form 4 timeout
  (human-gated, awaiting approval).

## `04_Known_Issues/` — what's blocked

- [[Current Blockers]] — active blockers + pending human decisions.

## `06_Data_Moat/` — the moat

- [[Outcome Tracking Status]] — the calibration dataset; the defensible moat.

## `05_Decisions/` — settled decisions

- [[2026-07-25-subagent-roster]] — five **advisory, human-gated** specialist subagents; autonomous production-mutating agents stay excluded. Notes that the four agents `CLAUDE.md` documents do not exist on disk.
- One file per architecture decision (rationale + date). See its `README`.

## `08_Ideas/` — idea inbox

- [[Ideas Backlog]] — the capture inbox for raw ideas before they enter the plan.

## `09_Reference/` — reading & references

- [[Reading List]] — curated, Scope-specific reading, tiered by leverage.

## `02_Sessions/` — work log

- [[SESSION-2026-07-26-verifier-agent]] — added the `verifier` subagent (roster now six); independent pass that re-derives PROVEN claims and may overturn a headline. Restart required to load.
- [[SESSION-2026-07-26-test-isolation]] — **WS1: tests no longer touch the live DB** (working DB byte-identical across full runs); `jpt.db` untracked from git. Awaiting review — 4 prod-coupled tests + a Railway `DATABASE_PATH` check need a decision.
- [[SESSION-2026-07-25-subagent-roster]] — scaffolded the five advisory specialists; found the four agents `CLAUDE.md` documents don't exist. *(backfilled)*
- [[SESSION-2026-07-25-generic-ticker-diagnosis]] — surfacing amplifies concentration 3.2×; two scoring terms dead. Its DATA-LOSS flag was retracted next session. *(backfilled)*
- [[SESSION-2026-07-25-gate-reachability]] — **verdict (B): the convergence gate is structurally unreachable**; 8 of 14 eligible rules can't contribute and 3 of the 6 that can are one instrument. Key normalization is voided as a convergence fix.
- [[SESSION-2026-07-25-rule10-convergence-trace]] — **verdict: convergence was never generated, not destroyed**; the 28 "lost" RULE_10 alerts were ZWAR test fixtures. DATA-LOSS flag retracted.
- One entry per session (`SESSION_TEMPLATE` for new ones). Latest: `2026-07-23`
  (design-pass regression repair), `2026-07-23` (fey-slash design pass),
  `2026-07-23` (provenance + brief landing), `2026-07-22` (prod verification +
  RULE_06), `2026-07-21` (status reconciliation).

---

**Conventions:** `Scope/CLAUDE.md` is engineering ground truth and wins over the
vault on any factual conflict. Scoring/ingestion/rule changes are human-gated (no
scoring subagent by design). Note filenames match their `[[wikilink]]` titles.
Keep the Master Plan's Change Log and [[Production Health]] current.
