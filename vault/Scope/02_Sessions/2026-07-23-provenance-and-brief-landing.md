---
type: session-summary
stage: iPhone-5
status: completed
priority: high
tags: [session, work-log, provenance, brief, ui]
related: [[Roadmap Tracking]], [[Current Blockers]], [[2026-07-23-brief-as-default-landing]]
date-created: 2026-07-23
---

# Session: Alert provenance ("receipts") + brief as default landing

**Date:** 2026-07-23
**Branches:** `feat/alert-provenance`, `feat/brief-as-landing` (both off
`origin/main`, pushed for review, **not merged**)
**Status:** Completed — both features implemented, tested, awaiting review.

## What Was Done

### Phase 1 — Alert card provenance ("show the receipts")

Added an additive, **server-assembled, factual** receipts block to alert cards
(never LLM-generated). New `api/receipts.py:build_receipts()` normalizes each
rule's heterogeneous provenance into a compact summary + expandable items +
primary source link, degrading gracefully and flagging honest data gaps. Wired
into the feed (`/api/alerts`), ticker (`/tickers/{sym}/alerts`) and thesis
(`/themes/{id}`) endpoints; rendered client-side by a shared `renderReceipts()`
(native `<details>` expand/collapse) on the feed, ticker, and thesis pages. The
**cluster war room already renders member receipts natively** (name / direction /
size / date / PTR PDF) — left unchanged as the design reference.

**STEP 1 — receipts audit (per-rule `detail`/`tags`/URL fields actually stored):**

| Rule | Provenance available | Receipt shown | Gap |
|---|---|---|---|
| RULE_CLUSTER | `detail` JSON: members[name,dir,dates,sizes,doc_id,**filing_url**]; verify_url | N members, each buy/sell·size·date + **PTR PDF** | — (gold standard) |
| RULE_10 | tags JSON(rules); theme_id → **theme_signals** | N contributing signals, each rule+headline, linked | detail is LLM narrative (not used); no per-alert permalink page |
| RULE_01B | member_id, full_name; tags CSV `name\|action\|size\|date` | member, action, size, date | **no PTR filing_url** (RULE_CLUSTER has it) |
| RULE_06 | tags CSV `name,action,multiple` | insider name, action, ×avg | **no SEC Form 4 URL; no shares/price/date** |
| RULE_11 | tags CSV `contractor\|date\|award_id\|company\|amount`; detail | contractor, amount, date, description | **no USASpending URL** |
| RULE_07 | tags JSON(slug); detail empty | market question + Polymarket link (from slug) | detail empty |
| RULE_OSINT | **source_url** (Google News); detail (GDELT event) | event + source link | — |
| RULE_REDDIT | tags JSON(**url**); detail (post) | post excerpt + Reddit link | link in tags.url, not source_url |
| RULE_12 (FARA) | **source_url**, why_matters, detail | foreign principal + FARA link | — |
| RULE_13 (FEC) | member_id, **source_url**, detail | member + FEC link | — |
| RULE_14 / RULE_15 | detail, why_matters | patent / earnings signal + why | no source_url |
| RULE_02 | tags CSV member names; **detail empty** | member names (raw) | detail empty; names comma-joined→unparseable; no links |
| RULE_08 | tags CSV `agency,docket,date`; **detail empty** | agency, docket, date | no Federal Register URL |
| RULE_09 | tags CSV `registrant,firm,issue,spend`; **detail empty** | registrant, firm, issue, spend | no source_url |
| RULE_ANOMALY | detail text | anomaly description | meta-signal; thin by nature |
| RULE_TELEGRAM_OSINT / RULE_ADSB | detail, tags | message / movement | no source_url |

**Implemented:** builder + 3 endpoint wirings + shared client renderer on feed /
ticker / thesis. Tested: 5 unit + a TestClient integration (verified `/api/alerts`
returns real PTR links and honest gaps).

**Data gaps flagged for follow-up** (ingestion changes — out of scope this
session; logged in [[Current Blockers]]): RULE_06 (Form 4 URL + txn detail),
RULE_01B (PTR link), RULE_02/08/09 (empty detail / missing source URLs),
RULE_11 (USASpending URL), RULE_14/15/Telegram/ADSB (source URLs).

### Phase 2 — Morning brief as default landing

`/` now serves **today's cached morning brief** (was the dashboard). Feed stays at
`/feed` (+ a "Brief" nav link); old dashboard preserved at `/home`. Cache-only —
**never generates on page load**. Fallbacks: today missing → yesterday + notice;
none → `/feed?notice=nobrief` + banner. Logic in `api/landing.py`; decision logged
in [[2026-07-23-brief-as-default-landing]]. Tested: 6 unit + routing integration.

## Branches Created

| Branch | Description | Status |
|--------|-------------|--------|
| feat/alert-provenance | Receipts block on alert cards | Pushed, **awaiting review** |
| feat/brief-as-landing | Brief as `/` landing + fallbacks | Pushed, **awaiting review** |

## Blockers Encountered

- **Vault topology** (resolved with the user up front): `origin/main` already has
  the reorganized `vault/Scope/` vault (PR #1 merged); the task referenced the old
  flat paths. Confirmed: branch off `origin/main`, write vault docs under
  `vault/Scope/`.
- Full test suite not runnable in this sandbox (Python 3.14, heavy deps absent).
  New tests pass under a minimal venv; **run the full suite in CI before merge.**

## Decision Log

- Brief-as-default-landing — see [[2026-07-23-brief-as-default-landing]].
- Receipts are **server-assembled factual data + thin deterministic client
  render** (the app is client-rendered; "server-side rendering" honored as
  server-computed provenance, never LLM).
- Vault docs for this session committed on `feat/brief-as-landing`.

## Next Session Should

- Review + merge the two feature branches.
- Address the highest-value data gap: capture a **SEC Form 4 URL** for RULE_06 at
  ingestion (and a PTR link for RULE_01B) — see [[Current Blockers]].
- Optional: migrate `/brief/{date}` to cache-only (stop on-demand generation).

---

### Related
[[Roadmap Tracking]], [[Current Blockers]], [[2026-07-23-brief-as-default-landing]]
