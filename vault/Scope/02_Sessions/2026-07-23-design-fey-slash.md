---
aliases: [2026-07-23 Fey-Slash Design Pass]
type: session-summary
stage: iPhone-5
status: completed
priority: high
tags: [session, work-log, design, tokens, ui]
related: [[Roadmap Tracking]], [[Fey-Slash Design System]]
date-created: 2026-07-23
---

# Session: Design system pass — Fey/Slash synthesis

**Date:** 2026-07-23
**Branch:** `design/fey-slash-synthesis` (one commit per phase; pushed for review,
**not merged**). Builds on `feat/alert-provenance` + `feat/brief-as-landing`
(merged into the branch, not into main).
**Status:** Completed. Visual-system only — no structure/routing/data/copy changes.

## 1. Tokens established (`api/static/tokens.css`, served at `/tokens.css`)

- **Surfaces** #0a0a0c / #131317 / #1a1a1f / #232329 (+ overlay). **Borders**
  #2a2a30 / #3a3a42 / #5a5a62. Elevation = layering + hairlines only (no shadows).
- **Text** #e8e8ee / #8a8a92 / #5a5a62. **Accent (single):** copper #c89664 (+ dim
  #8a6a48). **Direction** buy #4ebe96 / sell #e06868 / neutral #8a8a92.
  **Severity** low #8a8a92 / medium #c89664 / high #e88b4a / critical #e06868.
  **Link** #7ba3d4 (hyperlinks only).
- **Type scale** 11–52px; leading 1.4/1.2/1.5; tracking -0.02em headings / 0.06em
  labels; weights 400/500/600. **Spacing** 4px grid (4…80); terminal gap 32px,
  editorial 64px. **Radius** 3/4/6/8px, no pills. Motion tokens + reduced-motion.
- Legacy per-page var names aliased to canonical tokens; per-page `:root` removed.
  Values used **verbatim** from spec (no ±5 adjustments).

## 2. Fonts loaded (Google Fonts) — all three confirmed

**Inter** (sans, 400/500/600), **JetBrains Mono** (mono, 400/500), **Fraunces**
(serif, variable 400/600). Playfair Display + IBM Plex Mono **dropped**. Serif
restricted to the brief hero (`var(--font-serif)`); brand + terminal titles →
sans; all data → mono.

## 3. Before / After (`shots/`)

`before/*` (base, pre-tokens) vs `after/*` (all phases), 1440px: brief, feed,
`/clusters`, cluster war room, **thesis war room** (populated via a demo theme in
the throwaway DB), ticker. Mobile 380px: `mobile_brief.png`, `mobile_feed.png`.

## 4. Pages retokenized — all six

Feed, brief (editorial), cluster war room, thesis war room, `/clusters`, ticker.
Receipts amplified to a copper-bordered `--surface-2` panel (names sans / data
mono / 180ms reveal); severity badges from `--severity-*` at 15% bg; brief hero
Fraunces 40px + sans section headers + 64px gaps + SUMMARY chip; all drop shadows
removed; radii tightened.

## 5. Motion added (functional only)

- **(a) New alert** — feed cards new since last visit fade in + 4px translate
  (220ms). Verified: 2 marked-new → exactly 2 `.alert-new`.
- **(b) Score updating** — `score-flash` keyframe (200ms), primitive-ready.
- **(c) Cluster HIGH→CRITICAL** — card pulses once (scale 1.03 + brightness,
  400ms, not a shadow), cross-visit.
- **(d) Receipts expand** — 180ms reveal (Phase 2).
Nothing else.

## 6. Regressions found

None. Only intended changes (amber→copper, Playfair→Fraunces-hero/sans-terminal,
IBM Plex→JetBrains Mono, blue-severity→severity tokens, shadows gone). Process
note: the browser cached aggressively — after-shots use cache-busted URLs.

## 7. Contrast check (WCAG AA)

All pairings pass **except `--text-tertiary #5a5a62`** — 2.90:1 on
`--surface-canvas` (2.71 on surface-1), below AA (4.5) and the 3.0 large-text
minimum. Used for incidental metadata. Flagged for a decision (see
[[Fey-Slash Design System]]); a compliant fix (~#757581/#808088) exceeds the ±5
hex latitude, so it's the user's call. text-primary 16.2, text-secondary 5.8,
accent 7.5, direction/severity/link all ≥5.9 — pass.

## 8. Mobile audit (deferred followups)

380px renders with **no horizontal overflow**; both registers readable. Minor:
feed filter chips stack tall, collapsed alert-card badges wrap across rows (dense
but functional). Flag for a later mobile polish pass — not fixed this session.

## 9. Branch

`design/fey-slash-synthesis` — pushed for review.

## Needs human decision

- **`--text-tertiary` contrast** — approve a lighter value (~#757581+) or keep
  as-specified.
- Merge order: the two feature branches first, then this design branch.

---
### Related
[[Roadmap Tracking]], [[Fey-Slash Design System]]
