---
type: architecture
stage: iPhone-1
status: implemented
priority: critical
tags: [rules, architecture, scoring]
related: [[Scoring System]], [[Data Hierarchy]]
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

See also: [[Scoring System]], CLAUDE.md
