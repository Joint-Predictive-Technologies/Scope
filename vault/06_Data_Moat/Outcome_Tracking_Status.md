---
type: architecture
stage: iPhone-8
status: in-progress
priority: high
tags: [data-moat, calibration, outcomes]
related: [[Scoring System]], [[iPhone Stage Progress]]
---

# Outcome Tracking Status

The calibration dataset that becomes the product's defensible moat.

## Current State

- **Labeled:** 324 alerts (price data fetched, returns computed)
- **Unavailable:** 327 (baskets/multi-ticker, no ticker, delisted)
- **Pending:** ~2,687 (clock ticking on their +20d window)

## How It Works

Daily job (LABEL_OUTCOMES, 02:00 UTC):
- Walks alerts where +20d has elapsed
- Fetches Yahoo price data at detection and +1d/+5d/+20d
- Computes returns and SPY-alpha
- Writes to alert_outcomes table

## Calibration Discipline

Never use this data to recompute alert scores retroactively. The labeled
outcome is a historical record of what the market did *after* Scope called
it, not a reason to change what Scope said before.

## Moat Strategy

By year 2–3, Scope will have logged thousands of resolved theses with
verified outcomes. That dataset:
- Cannot be purchased
- Cannot be replicated by a competitor starting today
- Compounds daily
- Is the raw material for all iPhone-8+ features

**Do not sell this data. It's worth more kept in house.**

---

See also: [[Scoring System]], SCOPE_IPHONE15_VISION.md (Layer 4)
