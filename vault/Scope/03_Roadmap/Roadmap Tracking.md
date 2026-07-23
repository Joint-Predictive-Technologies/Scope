---
aliases: [Roadmap Tracking]
type: roadmap
stage: iPhone-5
status: active
priority: high
tags: [roadmap, planning]
related: [[Master Plan]], [[iPhone Stage Progress]], [[Known Issues]]
---

# Active Roadmap Tracking

What's in flight, what's up next, dependencies. This is the short-horizon view;
the long-term phased plan it feeds into lives in [[Master Plan]] (the queued
items below map to **Phase 0** and **Phase 1** there).

*Last reconciled: 2026-07-23 (two feature branches added; base = `origin/main`).*

## In Flight / Awaiting Review

Four branches are complete and pushed, awaiting a review/merge decision.

**New this session (2026-07-23, both off `origin/main`):**

- `feat/alert-provenance` — factual "receipts" block on alert cards (feed, ticker,
  thesis; cluster war room already native). Server-assembled, additive, tested.
- `feat/brief-as-landing` — `/` lands on today's cached morning brief with
  yesterday/feed fallbacks (never generates on load); feed preserved at `/feed`.
  See [[2026-07-23-brief-as-default-landing]].
- `design/fey-slash-synthesis` — **design system pass (in review).** Single token
  source (`/tokens.css`); copper accent, JetBrains Mono + Fraunces (serif = brief
  hero only), two-register system (terminal / editorial brief), no shadows,
  functional-only motion. Builds on the two feature branches above (merged into
  it, not into main). See [[2026-07-23 Fey-Slash Design Pass]] +
  [[Fey-Slash Design System]]. **Open decision:** `--text-tertiary` contrast
  (2.90:1 — below WCAG AA).

**Carried over (2026-07-21):**

- `feat/llm-fallback` (`9f77654`) — Groq primary/fallback narrative generation
- `fix/remove-dead-generate-brief-job` (`d3687eb`) — dead cron entry removal

## Recently Completed (this bulk-session arc — all merged to main)

- RULE_10 `--emit-alerts` fix — merged (`6ea6a7a`), confirmed live in prod
- RULE_02 scheduling + ingest_senate hardening — merged (`b55e88c`)
- Scheduler-level failure safety net + pdfplumber/pillow deps — merged (`445e3ad`)
- Database backup automation (local interim) — merged (`83b3213`); local
  snapshot verified running; remote upload storage-ready, pending credentials
- Congressional digest standalone view (`/congress/digest/<date>`) — merged (`1647655`)
- Obsidian vault scaffold — merged (`dedd6f5`)
- Production audit sweep (argparse contract, silent failures, scheduler
  reconciliation) — completed, findings documented in [[Current Blockers]]

## Queued (Next Priority)

1. Review + merge the two branches in "In Flight / Awaiting Review" (none
   merged to main without explicit approval per this project's convention)
2. Add `GROQ_API_KEY_FALLBACK` to Railway production env (after #1)
3. Get remote backup storage credentials, wire up `boto3` + `upload_remote()`
4. Theme Temperature design session (pending)
5. Continue outcome tracking calibration (passive, clock-ticking)
6. RULE_PHARMA design and implementation

## Feature Candidates (backlog)

Prospective features, each tagged by whether it serves **convergence** (Scope's
differentiation) or **standalone value** (utility/retention only). See
[[Competitive Positioning]] for the framing — convergence features are prioritized.

- **Politician search page** — *standalone value.* A per-member directory/search
  surface; utility, not convergence (the data already feeds Congress signals).
- **Whale moves (13F institutional holdings)** — *convergence.* New independent
  source type; institutional accumulation on a ticker congress/contracts also
  touch is real corroboration. Free (EDGAR). Highest-value candidate.
- **Stock splits (Prompt B)** — *standalone value.* Retention/utility feature; no
  convergence contribution.
- **Risk factors (10-K Item 1A)** — *convergence.* Thematic linkage to
  lobbying/FARA/regulatory signals; free (EDGAR).
- **ETF holdings** — *standalone value.* Useful context, but does not strengthen
  the convergence thesis.

## Deferred / Waiting On

- Overwatch vs Scanner mode navigation split (design-first, not code-first)
- Regime recognition layer (needs more outcome data)
- Historical analogues (ditto)

## Risks

- **Database backup — remote storage:** Local interim backup closes most of
  the gap, but no off-volume copy exists yet (same failure domain as the
  primary DB). No production restore has been tested end-to-end. This is
  still the single biggest residual risk.
- **Outcome data calibration:** Takes time. Can't accelerate meaningfully.

---

See also: [[Master Plan]], [[iPhone Stage Progress]], [[Current Blockers]]
