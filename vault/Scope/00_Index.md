---
type: index
status: active
tags: [index, map-of-content]
last-reviewed: 2026-07-21
---

# Scope — Vault Index

Knowledge base for Scope, the political-market intelligence terminal. Start
with the Master Plan; everything else is detail hanging off it.

## Start here

- **[[Master Plan]]** — the living long-term plan: what Scope is at the end, the
  phased path from here to there, the constraints that path respects, and the
  risk register. **The source-of-truth roadmap.**

## Roadmap (`03_Roadmap/`)

- [[Master Plan]] — North Star + phases 0→4 (living).
- [[iPhone Stage Progress]] — stage-by-stage status against the maturity ladder.
- [[Roadmap Tracking]] — short-horizon: what's in flight this week.

## Architecture (`01_Architecture/`)

- [[Scoring System]] — Evidence Confidence + Opportunity, how they're computed.
- [[RULE Design Decisions]] — rule mechanics, thresholds, score immutability.

## Data moat (`06_Data_Moat/`)

- [[Outcome Tracking Status]] — the calibration dataset; the defensible moat.

## Known issues (`04_Known_Issues/`)

- [[Current Blockers]] — active blockers + pending human decisions.

## Sessions (`02_Sessions/`)

- Work-log per session. Use `SESSION_TEMPLATE` for new entries.

## Decisions (`05_Decisions/`)

- One file per architecture decision (rationale + date). See its `README`.

---

**Conventions:** `Scope/CLAUDE.md` is engineering ground truth and wins over the
vault on any factual conflict. Scoring/ingestion changes are human-gated (no
scoring subagent by design). Keep the Master Plan's Change Log current.
