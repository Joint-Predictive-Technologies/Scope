---
type: architecture
stage: iPhone-1
status: implemented
priority: critical
tags: [scoring, rules, calibration]
related: [[RULE Design Decisions]], [[Outcome Tracking Status]]
---

# Scoring System

How Scope scores alerts for Evidence Confidence and Opportunity.

## Evidence Confidence (EC)

Map rule family count to confidence level:
- 1 family: 20%
- 2 families: 35%
- 3 families: 55%
- 4 families: 70%
- 5 families: 82%
- 6+ families: 90%

Conflict between families: multiply by 0.7

(See CLAUDE.md for full details.)

## Opportunity Score (OS)

Formula: `novelty×40 − absorption×30 + horizon×20 + win_rate×10 + liquidity_adj`

**Components:**
- **Novelty** (0.1–1.0): 1/(1+log(count_in_30_days+1)), floor 0.1
- **Absorption** (0–1.0): has the market already priced in this signal?
  Checked via recent price moves.
- **Horizon** (5–20): IMMEDIATE(+20), SHORT(+15), MEDIUM(+10), LONG(+5)
- **Win rate** (+10): currently 0.5 placeholder, reserved for per-rule
  empirical win rate from alert_outcomes

**Note:** War room decomposition shows this formula. Code is source of truth.

## Calibration Strategy

Opportunity scoring will be continuously refined as alert_outcomes accumulates
real price data. Current weights are reasonable defaults; future tuning will
be data-driven.

---

See also: [[RULE Design Decisions]], alert_outcomes table
