---
type: decision
status: accepted
priority: critical
tags: [decision, convergence, rule10, gate-design, human-gated]
related: [[Master Plan]], [[RULE Design Decisions]], [[Current Blockers]], [[Competitive_Positioning]]
date-created: 2026-07-25
decided-by: human
applies-in: WS4
---

# Decision: RULE_10 gate redesign

**Decided:** 2026-07-25 (human decision)
**Status:** accepted, **not yet applied**
**Applies in:** WS4 — the gate-redesign session. This note is the authority for
that work. Nothing here is implemented by recording it.

## Context

[[SESSION-2026-07-25-gate-reachability]] returned **verdict (B): the current gate
is structurally unreachable.** The gate requires 4 distinct *eligible rules* on
one ticker within 24h. Of the 14 eligible rules registered in the scheduler, only
6 can emit a single-symbol HIGH/CRITICAL alert, and 3 of those 6 read the same
`transactions` table. Convergence has never fired on real data; the best-ever
count is 3, achieved by three rules reading one instrument.

Crucially, key normalization does **not** fix this — splitting every multi-symbol
composite moves the ceiling from 3 to 3.

## The decision

### D1 — Count mechanisms, not rules

The gate counts distinct **instruments**, not distinct rule names. The North Star
in [[Master Plan]] specifies "4+ *distinct mechanisms*"; the implementation
counted rules, so three views of the congressional feed could satisfy it while
representing a single source.

Rule → instrument map:

| Instrument | Rules |
|---|---|
| `congressional` | RULE_01B, RULE_02, RULE_CLUSTER |
| `insider` | RULE_06 |
| `lobbying` | RULE_09 |
| `contracts` | RULE_11 |
| `fed-register` | RULE_08 |
| `fec` | RULE_13 |
| `patents` | RULE_14 |
| `foreign-agents` | RULE_12 |
| `flight` | RULE_ADSB |
| `earnings` | RULE_15 |
| `telegram` | RULE_TELEGRAM_OSINT |

Excluded from corroboration entirely, unchanged: RULE_07, RULE_OSINT,
RULE_REDDIT, RULE_ANOMALY, RULE_10. RULE_OPTIONS is an enricher and emits no
alerts, so it maps to no instrument.

### D2 — Threshold 3 now, tiered 3/4 later

Fire at **3 distinct instruments**. Once the repair set (D3) has restored more
working instruments, add a tier: **3 = candidate**, **4 = strong**. The tier
distinction is a surfacing/labelling concern, not a second gate.

### D3 — Repair set

- **Fix:** RULE_06 (insider — the third leg the threshold-3 gate needs) and
  RULE_08 (composite ticker split).
- **Harden:** RULE_09 (lobbying — ticker is NULL on ~60% of its alerts).
- **Defer:** RULE_13 (FEC — wrong ID type passed to the API, plus a run that
  cannot finish inside the 300s subprocess timeout).
- **Defer or retire:** RULE_ADSB, RULE_12, RULE_14, RULE_TELEGRAM_OSINT — closed
  sources, empty backing tables, or ticker universes of 3–18 hardcoded symbols.

### D4 — Window

Keep the window on **ingestion time**, widened from 24h to **~10–14 days**. The
instruments have structurally different disclosure lags (congressional PTRs
30–45 days, LDA quarterly, USASpending on award, Form 4 within 2 business days),
so a 24h ingestion window demands a coincidence rather than detecting one.

**Event-time windowing is the correct long-term basis** and is deferred to a
later correctness upgrade, gated on an `event_date` backfill. Today `event_date`
is populated only for RULE_01B (106/192) and RULE_11 (84/102) and is **0** for
RULE_02, RULE_06, RULE_08, RULE_09 and RULE_CLUSTER, so the field cannot yet
carry the window.

## Invariant restated

**The redesign changes future firing only.**

- No historical re-scoring. Detection-time scores stay immutable — `enrich_scores`
  backfills missing values only, and `enrich_alert_scores(only_unscored=False)`
  is never run over history.
- RULE_10's outcome track **restarts** under the new definition. Any forward
  performance accumulated under the old gate is not comparable and must not be
  pooled with the new. In practice this costs nothing: the old gate never fired,
  so there is no real RULE_10 outcome history to lose.
- Evidence Confidence and Opportunity remain two independent scores and are never
  merged.

## Sequencing

This decision is applied only in **WS4**, and only after:

1. **WS1** — test isolation + untrack `jpt.db` (nothing downstream is verifiable
   until the DB is trustworthy).
2. **WS2** — RULE_06 reliability.
3. **WS3** — RULE_08 composite split.

Applying the gate change before the instruments are repaired would lower the
threshold onto a pool that still has too few working legs.

## Consequences

- **Accepted:** a threshold of 3 instruments is a weaker corroboration claim than
  4. This is deliberate and temporary; D2's tiering restores the stronger claim
  once D3 lands. Surfacing must label 3-instrument convergences as *candidates*,
  consistent with the honesty posture in [[Current Blockers]].
- **Accepted:** a 10–14 day ingestion window will admit pairings that are further
  apart in real-world time than a 24h window implies. Event-time windowing (D4,
  deferred) is the fix.
- **Unchanged:** no new data sources. Per [[Competitive_Positioning]], breadth is
  not the lever — the engine has four working instruments and a gate that could
  not be satisfied. New feeds would not have converged either.

---

### Related

[[SESSION-2026-07-25-gate-reachability]], [[Master Plan]],
[[RULE Design Decisions]], [[Current Blockers]]
