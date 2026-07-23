---
aliases: [Main Page Information Architecture]
type: decision
stage: iPhone-5
status: accepted
priority: high
tags: [decision, ui, brief, information-architecture]
related: [[2026-07-23 UI Restoration and Completion]], [[2026-07-23-brief-as-default-landing]], [[Roadmap Tracking]]
date-created: 2026-07-23
---

# Decision: main-page information architecture

**Branch:** `fix/ui-restoration-and-completion` (Phases 1–2). The main page is the
brief rendered by `scripts/morning_brief.py` (served at `/`).

## Principle

**The main page is the product's first impression and must be informative on
first paint** — a briefing document, dense and scannable, not an early-alpha
dashboard with dead space. Density over emptiness.

## Structure (top to bottom)

1. **Ticker belt** — live `/api/ticker-tape`, continuous scroll, pause on hover.
2. **Hero (serif, Fraunces)** — **synthesizes convergence across source types**,
   not a single signal. `_synthesize_headline()` groups the last 7 days by ticker,
   counts DISTINCT source types (via a rule→source-type taxonomy), and names the
   most cross-corroborated ticker ("LMT converges across 3 source types"). If
   nothing converges it says so honestly and names the strongest single signal.
3. **Activity strip** — four mono figures (rules fired 24h / alerts 24h / new
   clusters 24h / sources active 24h) + per-category breakdown.
4. **Week calendar** — 7 cells from `/api/activity`, severity-tinted, click →
   `/feed?since=<date>`.
5. **Overnight signals** — diversified across source types (round-robin, not the
   old `RULE_OSINT`+`RULE_07`-only filter that produced a defense monoculture),
   each tagged with its source type, with an honest "N of M rule families active"
   coverage notice.
6. **Live clusters**, then **theses**, then **yesterday in congress**.

## Key why's

- **Convergence is Scope's differentiation**, so the hero must *show* convergence,
  not promote one loud signal. The synthesis is deterministic (no LLM) and fails
  honest when there's nothing to converge.
- **Source diversity is a first-class UI concern.** The overnight monoculture was
  a presentation bug (a hardcoded 2-rule filter), and the fix surfaces breadth +
  admits thin coverage rather than hiding it.
- **Removed**: the raw alert-count vanity metric and the "Morning Brief / Feed"
  toggle — the feed is a first-class nav tab now, so the page stands on its own.

## Related

Builds on [[2026-07-23-brief-as-default-landing]]. Full session:
[[2026-07-23 UI Restoration and Completion]].
