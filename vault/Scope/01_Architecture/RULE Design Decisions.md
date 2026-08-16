---
aliases: [RULE Design Decisions]
type: architecture
stage: iPhone-1
status: implemented
priority: critical
tags: [rules, architecture, scoring]
related: [[Scoring System]], [[Master Plan]]
---

# RULE Design Decisions

Core architectural decisions about how rules work and get scored.

## Detection-Time Score Immutability

**Status:** Implemented, enforced

**Rationale:** Scores are assigned at the moment a rule fires, based on
the state of the system *at that time*. Novelty, opportunity, and
evidence confidence are never retroactively recomputed — this preserves
calibration data and prevents score drift.

**Implementation:**
- Path (a) rules: scores computed inline in insert_alert()
- Path (b) rules: scores backfilled by enrich_scores(), which NEVER
  overwrites existing scores
- enrich_scores() always runs with only_unscored=True in production

**Why it matters:** Every alert's score is a historical record of what
Scope thought at that moment. If we rewrote scores based on later events,
calibration data becomes meaningless.

## Ticker Normalization (Dual Path)

**Status:** Implemented

**Two paths:**
- Path (a): insert_alert() normalizes ticker at write time
- Path (b): 15 legacy scripts do direct INSERT; enrich_scores normalizes
  during backfill

Both paths now produce identical normalized tickers (strips $, uppercase,
handles BRK.B/BRK-B variants). Corroboration counts work correctly.

## RULE_10 Four-Family Threshold

**Status:** Implemented, in production

**Rule:** 4+ distinct rule families converging on same ticker within 24h.

**Rationale:** Random noise (two rules firing on the same day) is common.
Independent convergence of four different detection mechanisms (e.g.,
congressional trade + contract award + lobbying spike + geopolitical
event) is structurally meaningful.

**Tuning history:**
- Started at 3-family threshold
- Moved to 4-family based on false-positive analysis
- Verified via alert_outcomes: RULE_10 alerts show measurable alpha

## RULE_CLUSTER Design

**Status:** Implemented, live in production

**Trigger:** 3+ distinct congressional members, same ticker, within 72h
trade-proximity window (not wall-clock time).

**Why 72h?** PTR disclosure lag is 30-45 days by law. 72h trade window
captures actual coordination without false positives from overlapping
disclosure periods.

**Scoring:**
- HIGH: 3-4 members
- CRITICAL: 5+ members
- Novelty computed on (sorted_member_set, ticker, direction) tuple, not
  ticker alone (prevents novelty depression from prior fires)

**Acceptance tested:** SPCX (3 members, consensus_buy) — passed all tests.

---

## ⚠️ Instrument definitions have been REFINED — read before repairing a rule

These decisions describe how rules **work and get scored**. What each instrument is
**for** has since been redefined in a design conversation, and several existing rules
are now defined differently from how they were built:

- **RULE_01B** — from "a member traded" to "a member **whose background makes this
  trade plausibly informed** traded" (a consumer of the person-provenance layer)
- **RULE_02** — from cluster size to **cross-partisan agreement** (a Democrat *and* a
  Republican buying the same ticker)
- **RULE_16** — **dual role**: confirmation/regime for big names, and **small-cap
  discovery**, where 13F staleness stops mattering
- **RULE_09** — **demoted to context**; lobbying measures influence on government, not
  tickers, so it leaves the gate-instrument set
- **RULE_08** — **context, door open** to a narrow instrument only under
  document-names-a-company attribution ⚠️ *provisional*

Full definitions, the person-provenance through-line, the convergence-value principle
(the gate decides **IF**, ticker-relative weighting decides **HOW LOUD**), and the
Tier 1–3 + context architecture: **[[Instrument Definitions]]**.

> **When repairing a rule, target the definition there, not the rule as originally
> built.** A repair that counts RULE_02's clusters more accurately has fixed the wrong
> thing.

See also: [[Instrument Definitions]], [[signed-signal-engine]],
[[The Whale and Provenance Layer]], [[Scoring System]], CLAUDE.md
