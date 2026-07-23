---
aliases: [Competitive Positioning]
type: roadmap
stage: iPhone-5
status: active
priority: high
tags: [roadmap, strategy, positioning, moat]
related: [[Master Plan]], [[Roadmap Tracking]], [[Outcome Tracking Status]]
date-created: 2026-07-23
---

# Competitive Positioning — Scope vs. QuiverQuant

An honest read of where Scope competes, and where it must **not** try to.

## What QuiverQuant is

A **data aggregator**: many alternative-data datasets (congressional trading,
lobbying, government contracts, insider trades, WallStreetBets, patents, corporate
flights, and more), a multi-year head start, paid data licenses, and the brand /
distribution that comes with being early. Their moat is **dataset breadth** and
the operational machinery to license and maintain it.

## What Scope is

A **cross-source signal-convergence engine**. Scope's unit of value is not "here
is another dataset" — it's **"these N independent source types are pointing at the
same ticker at the same time, and here is the receipt for each."** The corroboration
engine (RULE_10), the cluster detection (RULE_CLUSTER), the convergence hero, and
the per-signal provenance are the product. The dataset is raw material; the
**convergence is the signal**.

## Why Scope must NOT match Quiver dataset-for-dataset

- It's a race Scope **loses on resources** — Quiver has years and paid licenses.
  Chasing parity means permanently playing catch-up on someone else's axis.
- It **dilutes the thesis.** Every dataset added purely for breadth (not because
  it strengthens convergence) makes Scope a worse aggregator than Quiver and a
  less focused convergence engine than it should be. Breadth-for-breadth is
  strategically backwards for a smaller team.
- **The moat is calibration, not coverage.** Scope's defensible asset is
  `alert_outcomes` — the forward-return record of which convergence patterns
  actually paid off (see [[Outcome Tracking Status]]). That compounds with time
  and can't be licensed. Coverage can.

## Datasets that WOULD strengthen convergence (candidates, not commitments)

Each adds a NEW independent source type that can corroborate an existing signal:

- **13F institutional holdings** (quarterly, EDGAR, free) — "whales" moving into a
  ticker congress is also buying is real convergence.
- **Form 8-K material events** (EDGAR, free) — a material corporate event landing
  on a ticker with congressional/contract activity is corroboration.
- **10-K Item 1A risk factors** (EDGAR, free) — thematic linkage (e.g. a company
  flagging a regulatory risk that lobbying/FARA data also lights up).
- **Expanded USASpending contract coverage** — deeper contract flow strengthens
  the Contracts source type already in the convergence set.

## Datasets with standalone user value but NOT convergence

Worth considering only as retention/utility features, explicitly labeled as such —
they do not feed the convergence thesis:

- Stock splits, ETF holdings, analyst ratings.
- **Analyst ratings** are the one genuinely **gated** dataset here (licensed, not
  freely available) — so it's also the worst fit: cost + no convergence value.

## Explicit position

**Scope competes on SIGNAL QUALITY and CONVERGENCE, not dataset breadth.** New
data sources earn their place by making convergence stronger or better-calibrated
— not by lengthening a feature-parity checklist against Quiver.

## Related

[[Master Plan]] · [[Roadmap Tracking]] (Feature Candidates) · [[Outcome Tracking Status]]
