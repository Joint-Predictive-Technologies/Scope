---
aliases: [2026-07-23 Design Pass]
type: session-summary
stage: iPhone-5
status: completed
priority: high
tags: [session, work-log, design, tokens, ui]
related: [[Roadmap Tracking]], [[Current Blockers]]
date-created: 2026-07-23
---

# Session: Design system pass (Refero-inspired)

**Date:** 2026-07-23
**Branch:** `design/refero-inspired-pass` (one branch, one commit per phase; pushed
for review — **not merged**)
**Status:** Completed. Visual-system pass only — no structure, routing, data,
scoring, or copy changed.

## What Was Done

A consolidation-and-refinement design pass on the combined-features base
(`feat/alert-provenance` + `feat/brief-as-landing` merged into the design branch,
not into main). Verified live via a local run against a throwaway DB + Playwright
at 1440px (and 380px mobile).

### Tokens established (`api/static/tokens.css`, served at `/tokens.css`)

Single source of truth; each in-scope page's local `:root` removed and legacy var
names aliased (resolving prior per-page inconsistencies — e.g. `--red`/`--green`
differed between pages — to ONE value). Elevation = `--bg-0/1/2` + borders only.

- **Color:** `--bg-0 #0b0a08`, `--bg-1 #131109`, `--bg-2 #1c1810`,
  `--border #2a2620`, `--border-strong #3a3428`; text `--text-primary #e8e0cc` /
  `--text-secondary #c0b6a0` / `--text-tertiary #83795f`; `--accent #c8922a` /
  `--accent-bright #e8aa3a`; `--severity-critical #e5544a` / `--severity-high
  #d9932b` / `--severity-medium #8a8175`; `--direction-buy #4dc47a` /
  `--direction-sell #e5544a`; `--link #6ab0e0`.
- **Type (two families only):** `--font-sans: Inter`, `--font-mono: IBM Plex
  Mono`; **Playfair Display serif retired** (`--font-display` → sans). Scale
  `--text-xs .68 / sm .78 / base .86 / lg 1 / xl 1.25 / 2xl 1.6rem`; weights,
  leading, `--tracking-label` tokenized.
- **Spacing:** `--space-1..8` on a 4px grid (4/8/12/16/24/32/48/64).
- **Radius:** `--radius-sm 3px`, `--radius-md 6px` (8/10px tightened to 6).
- **Motion:** `--ease-out cubic-bezier(.22,1,.36,1)`, `--dur-fast 150 / mid 220 /
  slow 400ms`; `prefers-reduced-motion` respected.

### Pages retokenized

Feed (`alerts.html`), flagship brief (`scripts/morning_brief.py`), cluster war
room (`cluster.html`), thesis war room (`thesis.html`), ticker (`ticker.html`),
clusters (`clusters.html`), plus the SPA `brief.html`.

- **Receipts amplified (Principle 2):** now an elevated `--bg-1` panel with an
  `--accent` left border + header divider, mono on actors/sizes/dates/links
  (tabular-nums), honest gap note — more prominent than before, on feed / ticker /
  thesis (cluster war room's member table was already the gold standard).
- **Brief (Principle 3):** serif retired, densified vertical rhythm, section
  headers unified, data rows mono; Groq preamble stays sans with a small
  "summary · generated" chip in `--text-tertiary` above it; "See raw feed →" +
  fallbacks preserved.
- **Severity badges** derive from `--severity-*` (MEDIUM moved blue → neutral).
- **All drop shadows removed** (score-decomp tooltip + command palette) — elevation
  via layering/borders only.

### Motion added (functional only)

- **(a) New alert arriving** — feed cards new *since last visit* (localStorage)
  fade in + slide down (220ms). Verified: marking 2 alerts new applied
  `.alert-new` to exactly those 2 cards; first visit seeds silently.
- **(c) Cluster HIGH→CRITICAL** — card pulses ONCE (scale+brightness, not a
  shadow) when severity rose to CRITICAL since last visit.
- **(d) Receipts expand** — 220ms reveal (from Phase 2), confirmed smooth.
- **(b) Score updating** — cross-fade keyframe in place (primitive-ready; no live
  trigger in-session).
Nothing else — no page transitions, scroll, or hover elaborations.

## Before / After

`shots/before/*` (pre-token) vs `shots/after/*` (all phases), 1440px: brief, feed,
clusters, cluster war room, ticker. Mobile 380px: `mobile_brief.png`,
`mobile_feed.png`. *(Thesis war room omitted — 0 themes in local data.)*

## Regressions found

None. Only intended changes (serif→sans titles/brand, MEDIUM badge blue→neutral,
tighter radii, shadows gone, receipts amplified). One process gotcha: the browser
cached aggressively — after-shots use cache-busted URLs.

## Mobile audit (deferred followups — not fixed this session)

380px renders with **no horizontal overflow**; content readable. Minor: feed
filter chips stack tall, and collapsed alert-card badges wrap across 2 rows (dense
but functional). Flag for a later mobile polish pass.

## Next Session Should

- Review + merge `design/refero-inspired-pass` (after the two feature branches it
  builds on).
- Optional: extend the token consolidation to the ~20 out-of-scope pages (they
  still carry local `:root`).
- Optional mobile polish (filter chips, badge wrap).

---
### Related
[[Roadmap Tracking]], [[Current Blockers]]
