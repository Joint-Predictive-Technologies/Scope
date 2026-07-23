---
aliases: [Fey-Slash Design System]
type: decision
stage: iPhone-5
status: in-review
priority: high
tags: [decision, design, tokens, typography]
related: [[Roadmap Tracking]], [[2026-07-23 Fey-Slash Design Pass]]
date-created: 2026-07-23
---

# Decision: Design system — Fey/Slash synthesis

**Branch:** `design/fey-slash-synthesis` (in review, not merged). Single token
source `api/static/tokens.css` (served at `/tokens.css`).

## (a) Why this synthesis — Fey terminal + Slash editorial

Scope has two genuinely different jobs, so it gets two visual **registers** from
one token set:

- **Terminal register** (feed, war rooms, ticker, `/clusters`) — **Fey-inspired:**
  dense, dark, all-sans + mono, functional. This is where an analyst *works*; it
  should feel like an instrument, not a document.
- **Editorial register** (morning brief **only**) — **Slash-inspired:** a serif
  hero, generous 64px section rhythm, still-dense data rows. The brief is the
  landing / first impression; it should read like a *briefing*, carrying
  credibility before the reader has parsed a single number.

One token file drives both — the registers differ in *application* (fonts,
spacing, serif-vs-sans hero), not in palette. This keeps the product coherent
while letting the entry point feel distinct from the workbench.

## (b) Why copper accent (#c89664) over orange

A single chromatic accent, and it's **copper**, not a brighter orange/ember.
Orange reads as *consumer energy* (fintech-app "look at me"). Copper is quieter
and warmer — it reads as **editorial credibility**, the color of a serious tool
that expects to be trusted, not one competing for attention. Severity still
escalates through it (medium = accent copper → high orange → critical red), so
urgency has somewhere to go without the baseline shouting.

## (c) Why square corners (≤8px) over pills

Radii are restrained: 3/4/6/8px, **never** `999px`. Pill-shaped controls read as
consumer-app chrome. Scope is a professional instrument; square-ish corners +
hairline borders + surface layering (no shadows) say "tool," not "app." Elevation
is communicated purely by `--surface-canvas → 1 → 2 → 3` and 1px borders.

## (d) Why serif is restricted to the brief hero ONLY

Serif is powerful and easily overused. It appears in **exactly one place** — the
morning-brief hero headline (Fraunces 400, 40px). Everywhere else — feed titles,
war-room headers, score numbers, the brand — is sans. This makes the one serif
moment *mean* something (this is the briefing) and keeps the terminal register
unambiguously functional. Data everywhere is monospace (JetBrains Mono) — the
mechanic that makes tickers, amounts, %s, dates, and IDs feel authoritative.

## Fonts

Inter (sans) · JetBrains Mono (mono) · Fraunces (serif, hero only) — all via
Google Fonts. Playfair Display + IBM Plex Mono retired.

## Contrast caveat (flagged for decision)

Every text/background pairing meets WCAG AA **except `--text-tertiary #5a5a62`**
(2.90:1 on `--surface-canvas`; below the 4.5 AA and the 3.0 large-text minimums).
It's used for incidental metadata/timestamps. Left as-specified pending your call
— a compliant fix (~`#757581` for ~3.5:1, or `#808088` for ~4:1) exceeds the ±5
hex latitude, so it's a deliberate token change, not an auto-adjustment.

## Reversibility

The whole pass is CSS/tokens on one branch. Revert the branch and nothing else is
touched (routes, data, features unchanged).
