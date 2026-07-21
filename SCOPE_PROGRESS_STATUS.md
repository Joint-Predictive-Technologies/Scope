# Scope — Progress Status
*Where we actually are vs. the iPhone-stage roadmap.*
*Pairs with: SCOPE_PRODUCT_SPEC.md, SCOPE_IPHONE15_VISION.md*

---

## Note on SCOPE_PRODUCT_SPEC.md

That document's Phase 2/3 checklist is stale. It shows "Evidence Confidence
vs Opportunity Score (two separate scores)" and "Congressional cluster
detection" as unchecked, and its rules table doesn't list RULE_CLUSTER at
all. Both are real, live, production-verified features. This doc is the
current source of truth; SCOPE_PRODUCT_SPEC.md's checklist section should
be treated as historical until it's updated to match.

---

## Status vs. SCOPE_IPHONE15_VISION.md stages

| Stage | Target item | Status |
|---|---|---|
| iPhone 1 (baseline) | Rules engine, scores, novelty decay | Done |
| iPhone 5 (3–6mo target) | RULE_CLUSTER | **Done** — live in prod, SPCX acceptance test passed |
| | Daily pulse layer | **Done** — Morning Brief, 7 sections, scheduled 06:30 UTC |
| | Theme Temperature | Not started — deferred pending architecture discussion (circularity guard) |
| | RULE_PHARMA | Not started |
| iPhone 8 (12–18mo target) | Outcome tracking live | **Done** — alert_outcomes table, daily labeling job, SPY-alpha calculated |
| | Regime recognition | Not started |
| | Historical analogues | Not started |
| iPhone 12 (24–30mo target) | Structural permanence, conflict decay curves, multi-asset | Not started |
| iPhone 15 (36–48mo target) | Pattern memory, team features, short-side, published track record | Not started (outcome data is the raw material, already accumulating) |

**Headline:** written the same month as SCOPE_IPHONE15_VISION.md, which
dates "iPhone 1 = Now." Two iPhone-5-stage items and one iPhone-8-stage item
are already live — ahead of the doc's own projected schedule. The reasoning
layer (regime recognition, historical analogues, structural permanence,
conflict decay) — the part that makes the product genuinely defensible
rather than a well-built rules engine — hasn't started.

---

## Infrastructure and resilience work (not itemized in the vision doc,
## but supports it)

- Production hardening: pillow/pdfplumber dependency fixes, universal
  scheduler-level failure safety net (catches import-time failures)
- RULE_10 and RULE_02 argparse contract fix (--emit-alerts)
- Member-name normalization (diacritic folding), ticker normalization
  across both ingestion paths
- Disk usage monitoring, stall monitor for scoring pipeline
- Thesis and cluster war rooms, user annotations (three levels)
- Database backup automation (in progress)
- Groq multi-provider fallback (in progress)
- Standalone congressional digest view (in progress)

---

*Last updated: 2026-07-21*
*Maintained by Maksim / Joint Predictive Technologies*
