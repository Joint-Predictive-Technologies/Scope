---
aliases: [Ideas Backlog]
type: ideas
status: living
priority: medium
tags: [ideas, backlog, inbox]
related: [[Master Plan]], [[Roadmap Tracking]], [[Current Blockers]], [[Reading List]]
last-reviewed: 2026-07-23
---

# Ideas Backlog

The capture inbox for ideas that surface mid-work but aren't yet formalized into
the [[Master Plan]] or [[Roadmap Tracking]]. **Nothing here is committed** — it's
raw. When an idea graduates, move it into the plan/roadmap and delete it here.

> Convention: append freely with a date + a one-line tag `[near-term | research |
> reasoning | product | meta]`. Triage during roadmap reconciliation. Big
> strategic ideas belong in [[Master Plan]]; this is the scratchpad that feeds it.

---

## Near-term / engineering

- **`[near-term]` RULE_06 Phase B — incremental "seen" set.** Add a `rule06_seen`
  watermark table so each run only processes genuinely-new Form 4 filings.
  Deferred pending prod metrics from Phase A. *(from the RULE_06 fix plan.)*
- **`[near-term]` Per-alert permalink page.** RULE_10 receipts currently link
  contributing signals to `/ticker/{sym}` (no single-alert page). A
  `/alert/{id}` view would make evidence chains truly addressable.
- **`[near-term]` Migrate `/brief/{date}` to cache-only.** It still calls
  `generate()` (generates if uncached). Make it read-only like the new `/`
  landing so no page load can trigger generation. *(from brief-as-landing.)*
- **`[near-term]` Receipts on more surfaces.** The provenance block is on feed /
  ticker / thesis / cluster; extend to the **congress digest** and **member**
  pages for consistency.
- **`[product]` Receipts price-action sparkline.** For a member/insider trade
  receipt, inline a tiny sparkline of price between transaction date and
  disclosure date (data already fetched by `/tickers/{sym}/price-action`).

## Data moat / scoring (formalize via Master Plan Phase 1)

- **`[research]` Calibration report + real per-rule win rate.** Retire the
  `win_rate` 0.5 placeholder using `alert_outcomes`. Cross-linked to
  [[Master Plan]] Phase 1 and [[Outcome Tracking Status]].
- **`[research]` Proper scoring rules.** Report calibration with Brier / log
  scores, not just hit-rate. *(from [[Reading List]] — Superforecasting / Gneiting.)*
- **`[research]` Meta-labeling for outcomes.** Apply López de Prado's
  meta-labeling / purged CV so small per-rule samples don't overfit. *(from
  [[Reading List]] — AFML.)*

## Reasoning layer (Master Plan Phase 2–3)

- **`[reasoning]` Historical analogue retrieval.** Given a live thesis, retrieve
  the most similar *resolved* theses from `alert_outcomes` and show what happened
  next. Cross-linked to [[Master Plan]] Phase 2.
- **`[reasoning]` Regime recognition v1.** Condition opportunity/expectations on a
  classified market regime. Gated on calibration depth.
- **`[reasoning]` Structural-permanence score.** Distinguish a one-off catalyst
  from a durable structural edge. [[Master Plan]] Phase 3.

## Product / go-to-market

- **`[product]` Published verified track record.** The endgame proof-of-edge,
  built on the outcome dataset. [[Master Plan]] Phase 4.
- **`[product]` Short-side theses.** Detect and score negative convergences.
- **`[product]` Landing page rebuild** from the marketing drafts (positioning +
  hero + feature blurbs already drafted).

## Meta / knowledge-base

- **`[meta]` Consolidate the vault onto `main`.** Session notes are currently
  fragmented across unmerged branches (07-21 on main, 07-22 and 07-23 on separate
  branches) — main lacks a complete history. Decide a single home and merge.
- **`[meta]` Automate session capture.** A Claude Code **Stop hook** could append
  a session stub after each working session so nothing relies on memory. Optional;
  needs `settings.json` (see the update-config workflow).
- **`[meta]` Idea triage cadence.** Review this backlog whenever
  [[Roadmap Tracking]] is reconciled; promote or prune.

---

*Add ideas here the moment they occur — cheap to capture, expensive to lose.*
