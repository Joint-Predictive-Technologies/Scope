---
aliases: [Design Pass Regression Postmortem]
type: decision
stage: iPhone-5
status: accepted
priority: high
tags: [decision, postmortem, design, tokens, process]
related: [[2026-07-23 Fey-Slash Design Pass]], [[Fey-Slash Design System]], [[2026-07-23 Design Pass Regression Repair]], [[Roadmap Tracking]]
date-created: 2026-07-23
---

# Postmortem: fey-slash design pass shipped with partial coverage

## What happened

The fey-slash design system pass introduced a single token source
(`/tokens.css`) and converted pages to it — but only **5 pages + the morning
brief** were actually tokenized. The other **23 nav-reachable pages** kept their
old per-page `:root` (amber `#c8922a`, IBM Plex Mono). Result once merged to
`main`: the app looked inconsistent page-to-page, `/theses` (an in-scope page
that was simply missed) still used the old palette, and the `/` landing served a
**pre-deploy cached brief** rendered by the old template. To a user this read as
"regressions — nav broken, page empty, artifacts everywhere."

None of it was a removed feature. It was **incomplete coverage** + a **cache with
no template-version awareness**.

## Why it happened

1. **No route-level acceptance test.** The design pass verified the pages it
   touched, but nothing checked *every navigable route* for token coverage, so
   the 23 untouched pages passed unnoticed. Coverage was assumed, not measured.
2. **`intelligence.html` served two routes** (`/theses` + `/intelligence`) and
   was on the intended list but slipped through — no inventory caught the miss.
3. **The brief cache had no version marker.** A cached brief is served verbatim;
   there was no way for the app to know the cached HTML predated a template
   change, so a design change couldn't invalidate already-cached briefs.

## The fixes (this session — [[2026-07-23 Design Pass Regression Repair]])

- Tokenized all 23 remaining pages (pure consolidation; no value changes).
- Added `TEMPLATE_VERSION` + embedded marker to the brief; `generate()` now
  treats a version-stale cache as a miss, and `/` triggers a non-blocking rebuild.

## Decision / process change (accepted)

**Any future design/token pass MUST begin with a Phase-0 route inventory that
becomes the acceptance gate.** Concretely:

1. Enumerate **every navigable route** (nav links + linked sub-pages), not just
   the files you plan to touch.
2. Run a scripted audit over all of them (the Playwright `fetch`-each-route check
   used this session works well): assert `tokens.css` linked, **0** legacy
   `:root` / old font-family strings, **0** new console errors.
3. That audit passing on **100% of routes** — not a spot-check — is the
   definition of done. A design pass is not complete until every route clears it.
4. For any server-rendered/cached HTML (the flagship brief), a design change
   requires a **template-version bump** so stale caches invalidate automatically.

**Lesson in one line:** measure coverage across the whole route surface, don't
assume it — and make cached, server-rendered HTML version-aware so a redesign
can invalidate it.

---

### Related

[[2026-07-23 Design Pass Regression Repair]], [[Fey-Slash Design System]],
[[2026-07-23 Fey-Slash Design Pass]], [[Roadmap Tracking]]
