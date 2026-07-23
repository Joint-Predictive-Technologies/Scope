---
type: decision
stage: iPhone-5
status: in-review
priority: high
tags: [decision, routing, ux, brief]
related: [[Roadmap Tracking]], [[Current Blockers]]
date-created: 2026-07-23
---

# Decision: Morning brief as the default landing (`/`)

**Branch:** `feat/brief-as-landing` (in review, not merged)

## What changed

`/` now lands on **today's cached morning brief** instead of the dashboard
(`index.html`). Concretely:

- `GET /` reads the `briefs` cache and serves today's brief HTML, with a sticky
  header bar carrying a **"See raw feed →"** link.
- The **raw feed stays at `/feed`** (unchanged) and gains a **"Brief"** nav link,
  so the two are cross-linked and nothing is orphaned.
- The **original dashboard is preserved at `/home`** (was the `/` page).

## Why

Intelligence-briefing UX over monitoring-dashboard UX. The brief is the curated
"here is what matters and why" experience the product is meant to open with; the
raw feed is the firehose you opt into. Landing on the brief makes the default
experience the synthesized one, not the unfiltered stream.

## Fallback logic (never generates on load)

Briefs are produced only by the scheduled **DAILY_BRIEF** job (06:30 UTC). The
landing route **only reads the `briefs` cache — it never calls `generate()`**, so
a page load can never trigger slow, out-of-band generation.

1. **Today's brief cached** → serve it at `/`.
2. **Today missing** (before 06:30 UTC, or the job failed) → serve **yesterday's**
   brief with a notice: *"Today's brief runs at 06:30 UTC — showing yesterday's."*
3. **No brief at all** (fresh DB) → redirect to `/feed?notice=nobrief`; the feed
   shows a banner explaining why.

Logic lives in `api/landing.py` (`resolve_landing`, `inject_brief_header`) and is
unit + integration tested (`tests/test_landing.py`).

## Reversibility

Fully reversible via routing config — restore `home()` in `api/main.py` to
`return FileResponse(STATIC_DIR / "index.html")` and the old dashboard-at-`/`
behavior returns. `/feed`, `/home`, and the `briefs` cache are untouched by a
revert. If the briefing-first pattern doesn't land with users, we flip it back
without data migration.

## Notes / caveats

- The task framed `/` as "the raw feed," but `/` was actually a **dashboard**
  (`index.html`); the raw feed was already at `/feed`. The intent (brief as entry
  point, feed reachable) is fully met; the old dashboard is preserved at `/home`.
- The existing `/brief/{date}` route still calls `generate()` (generates if a
  date isn't cached). The landing route deliberately **bypasses** it and reads the
  cache directly. Migrating `/brief/{date}` to cache-only is a possible follow-up,
  out of scope here.
