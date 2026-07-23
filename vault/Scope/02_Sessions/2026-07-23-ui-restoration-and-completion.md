---
aliases: [2026-07-23 UI Restoration and Completion]
type: session-summary
stage: iPhone-5
status: completed
priority: high
tags: [session, work-log, ui, design, motion, brief]
related: [[Roadmap Tracking]], [[Main Page Information Architecture]], [[Fey-Slash Design System]], [[Current Blockers]]
date-created: 2026-07-23
---

# Session: UI restoration + completion

**Date:** 2026-07-23
**Branch:** `fix/ui-restoration-and-completion` (off `fix/design-pass-regressions`).
One commit per phase; **not merged, not deployed.**
**Status:** Completed all 6 phases + verification. Token values unchanged (except
removing a retired Playfair fallback); no scoring/rule/ingestion/schema changes.

## What was done

- **Phase 0 — route audit.** Enumerated all routes from the FastAPI source
  (**~28 page surfaces / 32 routes, not ~16**) and audited every one live via
  Playwright. Finding: the "removed" elements (ticker belt, week calendar, full
  nav) were **never deleted** — they live in `index.html` (`/home`); brief-as-
  landing simply made `/` render the minimal brief. Only `/brief` was
  untokenized. War rooms work (audit 404s were test-param artifacts).
- **Phase 1 — restore.** Ported the ticker belt (`/api/ticker-tape`), a 7-cell
  week calendar (`/api/activity`), and the full 10-link nav into the brief-based
  main page. Retokenized.
- **Phase 2 — main-page restructure.** Synthesized **convergence hero** (serif;
  "LMT converges across 3 source types"), 4-figure **activity strip**,
  source-**diversified overnight signals** with honest coverage notice, removed
  the alert-count + brief/feed toggle. See [[Main Page Information Architecture]].
- **Phase 3 — token completion.** Tokenized `brief.html`; purged **all box-shadow**
  (21 across 16 files); hardened congress/contracts tables; removed the retired
  Playfair fallback from `tokens.css`; fixed the `/digest` route shadow
  (content-negotiated: page to browsers, JSON to fetch).
- **Phase 4 — cut AI analysis + restyle globe.** Removed the `alert.detail`
  "AI Analysis" block (ticker + feed); no per-load Groq call existed. Restyled the
  Three.js globe: severity-token dots, size-by-count, dark ocean, no glow, static
  ring. See [[Remove War-Room AI Analysis]], [[Globe Retained and Restyled]].
- **Phase 5 — functional motion.** Exactly the 7 specified motions (belt scroll,
  card stagger, score count-up, calendar hover, cluster-upgrade pulse, tab
  crossfade, expand/collapse), all `prefers-reduced-motion`-gated. Nothing
  decorative.
- **Phase 6 — verification (3 rounds).** Route re-audit, token greps, acceptance
  checklist — below.

## Phase 6 acceptance (all PASS)

31/31 routes load with tokens (copper accent, Inter). **21/22 acceptance items
PASS**; the lone flag is two console 404s (`/cluster/<test-fp>` — test-param, real
links round-trip; `/sector/Defense` — pre-existing API case-sensitivity). Mobile
`/` and `/feed` at 380px: **no horizontal overflow.** Token greps: **box-shadow 0,
pills 0, legacy fonts 0**; residual: 476 legacy inline-hex (26 palette values) —
see blockers.

## Root causes found

- **Overnight geopolitical monoculture:** `morning_brief.py` hardcoded
  `WHERE rule IN ('RULE_OSINT','RULE_07')` — only two rules fed the block, one
  inherently defense/geopolitical. Fixed by round-robin diversification across
  source-type buckets + honest coverage notice. **Not a scoring bug — a
  presentation query.**
- **Lobbying "no data":** `/lobbying` reads the `lobbying_filings` table (2,113
  rows in the diagnostic DB — renders fine there); the empty state seen earlier is
  **prod-specific** (that table isn't ingested in prod), not a UI bug.
- **Cluster sparsity:** `/clusters` shows one cluster (SPCX) because RULE_CLUSTER
  genuinely has one — real sparsity, not a query bug.

## Branches

| Branch | Description | Status |
|--------|-------------|--------|
| `fix/ui-restoration-and-completion` | 6-phase UI restoration + completion | Pushed, **awaiting review** — not merged, not deployed |

## Next session should

- Review + merge the branch (with its base `fix/design-pass-regressions`).
- Context-aware **hex→token sweep** (the 476-hex residual — must avoid SVG/canvas
  contexts where `var()` breaks).
- Optional: globe graticule + on-hover tooltip; fix the pre-existing
  `/api/osint-region-context` 500 and `/sector` case-sensitivity (backend).

---

### Related

[[Roadmap Tracking]] · [[Main Page Information Architecture]] ·
[[Remove War-Room AI Analysis]] · [[Globe Retained and Restyled]] ·
[[Competitive Positioning]] · [[Current Blockers]]
