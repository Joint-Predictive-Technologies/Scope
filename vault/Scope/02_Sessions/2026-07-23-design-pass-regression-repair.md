---
aliases: [2026-07-23 Design Pass Regression Repair]
type: session-summary
stage: iPhone-5
status: completed
priority: high
tags: [session, work-log, design, tokens, repair, brief-cache]
related: [[Roadmap Tracking]], [[Fey-Slash Design System]], [[2026-07-23 Fey-Slash Design Pass]], [[Current Blockers]]
date-created: 2026-07-23
---

# Session: Design-pass regression repair

**Date:** 2026-07-23
**Branch:** `fix/design-pass-regressions` (off `main`, one Phase-3 commit
`107670b`; **pushed for review, not merged, not deployed**).
**Status:** Completed. Repair only — no redesign, no token/color/font value
changes, no scoring/data/ingestion/schema changes.

## The report vs. the reality

The fey-slash design pass (now on `main`) was reported to have introduced
regressions: *"war-room navigation missing/broken, main page empty, visual
artifacts on multiple pages."* A **live Playwright diagnostic** (Phase 1, no code
changes) found none of those were removed features:

| Reported | Actual root cause |
|----------|-------------------|
| War-room nav missing/broken | **Not broken.** Nav intact (11 links), war rooms reachable. |
| Main page empty | **Stale brief cache** — `/` served a pre-deploy cached brief with the old template. |
| Visual artifacts on many pages | **Incomplete token coverage** — the design pass tokenized only 5 pages + the brief; the other 23 nav-reachable pages still used the old amber/IBM Plex, so the app looked inconsistent page-to-page. |
| Empty /theses | **Data gap**, not a bug — 0 active themes in the DB, so the theses list and its war rooms are legitimately empty. Not fabricated to fill. |

0 features and 0 nav entries were removed by the design pass. 0 JS console errors
(only a benign `favicon.ico` 404).

## What was done (Phase 3 — "B + cache fix")

**Part B — token coverage completion (23 pages).** Mechanically applied the same
treatment the 5 done pages already had: link `/tokens.css`, strip the per-page
`:root`, swap `'IBM Plex Mono'`→`var(--font-mono)` and `'Playfair Display'`→
`var(--font-sans)`. Included `intelligence.html` (= `/theses` **and**
`/intelligence`), which was an **in-scope miss** of the original pass. Every CSS
var these pages use was already aliased in `tokens.css`, so this is pure
consolidation — **no token/color/font value changed**. App-wide inconsistency gone.

**Cache fix — template-version-aware flagship brief.**
- `scripts/morning_brief.py`: added `TEMPLATE_VERSION = "fey-slash-1"` + an
  embedded HTML marker `<!--scope-brief-template:...-->`. `generate()` now treats
  a cached-but-version-stale brief as a **miss** (regenerates). Added
  `brief_is_current(html)` and a de-duplicated `regenerate_if_stale_async(date)`
  (daemon thread, one in-flight regen per date).
- `api/main.py` `home()`: when today's cached brief is template-stale, serve it
  immediately **and** fire a non-blocking background rebuild so the next load is
  fresh. Never blocks the landing page; the yesterday-fallback path keeps the
  existing never-generate-on-load policy untouched.
- `api/landing.py`: tokenized the injected sticky brief bar (was hard-coded amber
  `#c8922a` + IBM Plex) → `var(--accent)` / `var(--font-mono)` / `var(--surface-2)`.

## Verification (Phase 4 — live Playwright + tests)

- `/theses` computed styles: accent = copper `#c89664`, font-mono = JetBrains
  Mono, **0** elements on legacy fonts, `tokens.css` loaded.
- All 23 page files serve with `/tokens.css` linked, **0** `IBM Plex` / `Playfair`
  / `:root` remaining.
- Cache fix round-trip: seeded a template-stale brief → `/` served it instantly →
  async regen rebuilt it to the current marker (`fey-slash-1`) → next load fresh.
- Mobile 380px on `/` and `/feed`: **no horizontal overflow**, copper accent,
  nav intact, brief bar tokenized. Screenshots in `diagnostic/` (untracked).
- **133 tests pass.**

## Branches

| Branch | Description | Status |
|--------|-------------|--------|
| `fix/design-pass-regressions` | 23-page token coverage + version-aware brief cache | Pushed, **awaiting review** — not merged, not deployed |

## Blockers Encountered

None that stalled the work. Note: the throwaway diagnostic uvicorn died once
mid-verify (leftover-process cleanup); restarted cleanly and re-verified.

## Decision Log

See [[2026-07-23-design-pass-regression-postmortem]] — why the design pass
shipped with partial coverage and the process fix (Phase-0 route inventory as an
acceptance gate for any future design pass).

## Pre-existing observations (NOT fixed — out of scope)

- **`/digest` route shadow.** The `/digest` JSON router (registered at module
  load) shadows the `/digest` HTML page route, so `/digest` returns JSON. The
  `digest.html` file itself is tokenized. This is pre-existing routing, unrelated
  to the design pass — flagged, not changed.
- **0 active themes** is a data gap, tracked in [[Current Blockers]].

## Next Session Should

- Get `fix/design-pass-regressions` reviewed + merged (with the design pass).
- Adopt the Phase-0 route-inventory acceptance gate for the next design pass.
- Optionally fix the `/digest` route shadow as a small standalone routing PR.

---

### Related

[[Roadmap Tracking]], [[Current Blockers]], [[Fey-Slash Design System]],
[[2026-07-23-design-pass-regression-postmortem]]
