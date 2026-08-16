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

## Standalone notes in this folder

- **[[the-whale-and-provenance-layer]]** — *The whale rule and the person-provenance
  layer.* The through-line behind the refined instrument definitions: **the strongest
  signals are not events but PEOPLE with provenance** — who the trader is and what they
  plausibly know (win rate, relations, military background, prior employers, political
  alignment). Recorded as the intended **moat**: nobody cross-references *"this
  small-cap buyer is a military veteran who worked at Raytheon."* The **whale rule** is
  a curated/earned watchlist whose **buys** are a concrete copy-trade signal, on two
  dimensions — **track record** (realized win % *and* amount won, so a single lucky call
  cannot mint a whale; **earned**, so it waits on [[Outcome Tracking Status]]) and
  **provenance** (the *why* behind the record). ⚠️ **NOT actionable, and four questions
  are deliberately left OPEN**: does provenance ever **gate** or only weight (*"yes,
  dependent"* — undecided); **where provenance data comes from** (LinkedIn/public
  bios/LLM-assembled — flagged **UNRESEARCHED**, with the scraping/ToS/accuracy
  minefield noted and hand-built profiles as the safe MVP); the curated-vs-earned
  threshold; and **inter-ticker cross-referencing**, recorded with the explicit warning
  that **it is how baskets are born**. Definitions it feeds: [[Instrument Definitions]].

- **[[the-world-web-vision]]** — *The World-Web: AI as connector, not oracle.* The
  direction for the reasoning layer: AI that **assembles** sourced facts and maps
  how they connect, never one that **judges**. Two piles — the calibrated numbers
  as the objective relevance filter, a cited context-web as the *why*. One hard
  rule: **every node and edge must come from a retrieved source and cite it**, or
  it's a hallucinated web wearing the costume of research. **Explicitly sequenced
  after convergence works in prod** — a web around a signal that hasn't fired is a
  frame around an empty picture.
  Its **UI directions** section now also holds **the ball pit** — the reddit
  collector's native view. A black screen of floating tickers, **orderless on
  purpose**, because the collector produces a *universe* and ranking would imply
  the names are signals. Size = `times_seen`, brightness = recency, tint =
  `cap_status`, so momentum is visible **without** ranking; a ball is a **doorway,
  not a verdict** — click through to the ticker's real page. **Build it after the
  collector is full and its caps are correct**, or you debug the visual and the
  data at once. Same instinct as the globe and the relationship-graph: one
  **spatial-UI language**, not three one-off toys.

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
