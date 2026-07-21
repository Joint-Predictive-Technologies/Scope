---
aliases: [Master Plan]
type: roadmap
kind: master-plan
stage: iPhone-5
status: living
priority: critical
tags: [roadmap, vision, master-plan, north-star]
related: [[iPhone Stage Progress]], [[Roadmap Tracking]], [[Current Blockers]], [[Outcome Tracking Status]], [[Scoring System]], [[RULE Design Decisions]]
last-reviewed: 2026-07-21
---

# Master Plan — Scope

> **This is the living long-term plan.** It defines what Scope *is at the end*,
> the path from here to there, and the disciplines that path must respect. We
> act on it and we update it as we progress. It is the source-of-truth roadmap;
> [[Roadmap Tracking]] holds the short-horizon "what's in flight this week" view,
> and each phase below links down to it.

## How to use this document

- **North Star** and **Maturity Ladder** change rarely — only on a genuine
  strategy shift. Treat edits there as decisions (log them in `05_Decisions/`).
- **Phases** are the working plan. Each has an objective, a task checklist,
  explicit **gates** (what must be true to start), and **exit criteria** (what
  must be true to call it done). Check boxes as work lands; don't delete
  completed items — they are the audit trail.
- **Update cadence:** at the end of any session that moves a phase, tick the
  relevant boxes, update `last-reviewed`, and add a line to the **Change Log**
  at the bottom. Re-derive the "You Are Here" marker whenever a gate opens.
- **One rule:** the plan bends to reality, never the reverse. If the codebase
  and this doc disagree, the codebase wins and this doc gets corrected.

---

## North Star — Scope as the end project

**Scope is a political-market intelligence terminal that detects
non-obvious, structurally-meaningful convergences across the machinery of
government and money — and, at maturity, reasons about them against its own
compounding record of what actually happened next.**

The thesis in one line: *when independent instruments of power move on the same
target at the same time, that convergence is a signal the market has not yet
priced — and the edge is being early, calibrated, and honest about it.*

Three things define "Scope, finished" — none of which a competitor can buy or
copy on day one:

1. **A convergence engine.** Congressional trades (House + Senate PTRs),
   lobbying (LDA), federal contracts (USASpending), FEC finance, patents,
   FARA, prediction markets, and OSINT are watched by independent rules. When
   4+ *distinct* mechanisms converge on one ticker, that is corroboration, not
   coincidence — and it becomes a scored, evidenced Market Thesis.

2. **Two honest, independent scores.** *Evidence Confidence* (how
   well-supported) and *Opportunity* (how much edge remains after absorption)
   are **never merged and never retroactively rewritten**. A score is a
   permanent record of what Scope believed the moment it spoke.

3. **A reasoning layer standing on a proprietary outcome dataset.** Every
   thesis is followed forward (+1/+5/+20 trading days, SPY-relative) into
   `alert_outcomes`. That dataset — thousands of resolved theses with verified
   returns — is the moat. On top of it Scope eventually *reasons*: recognizes
   market regimes, retrieves historical analogues, models how permanent a
   structural advantage is, and publishes a track record it has actually earned.

**What Scope is NOT, by design:** not a trade-execution bot, not a data
reseller (the outcome dataset is worth more kept in house), not a black box
(every alert decomposes to its evidence), and not an autonomous scorer — see
the **Constraints** section below.

---

## The Maturity Ladder (iPhone 1 → 15)

The "iPhone stage" metaphor is a capability ladder, not a calendar. Each rung
is a *class* of capability; the product is defensible only from rung 8 up,
because that is where the outcome dataset starts doing work no newcomer can
replicate.

| Rung | Capability class | What it means concretely |
|---|---|---|
| **iPhone 1** | Foundation | Rules engine, dual scoring, novelty decay, activity log, scheduler safety net. |
| **iPhone 5** | Convergence + pulse | Multi-member cluster detection (RULE_CLUSTER), 4-family corroboration (RULE_10), daily Morning Brief, sector specialization. |
| **iPhone 8** | Self-measurement | Outcome tracking live; per-rule *realized* win rates; the win-rate placeholder becomes real. First reasoning features (regime, analogues) built on calibrated data. |
| **iPhone 12** | Structural reasoning | Structural-permanence scoring, conflict/absorption decay curves, multi-asset beyond equities. |
| **iPhone 15** | Earned authority | Pattern memory, published verified track record, short-side theses, team/collaboration features. The defensible end state. |

**Reading the ladder:** rungs 1–5 are *mechanism* (build detectors, score
them). Rung 8 is the *pivot* — the product stops guessing at quality and starts
measuring it. Rungs 12–15 are *reasoning* — capability that only exists because
the measurement below it exists.

---

## You Are Here (snapshot — 2026-07-21)

**Position: iPhone-5, with an iPhone-8 foothold. Ahead of the original
schedule, but at the pivot's threshold, not through it.**

- ✅ **Done:** foundation (rung 1); RULE_CLUSTER, RULE_10, Morning Brief (rung
  5); outcome-tracking *plumbing* live (rung 8 foothold — labeling job runs
  daily, SPY-alpha computed).
- 🟡 **Maturing (passive):** the calibration clock. ~324 outcomes labeled,
  ~2,687 pending their +20d window *(verify live before acting — the db was
  not readable at last review)*. This is the gate for almost everything above.
- 🔴 **Not started:** the entire reasoning layer (regime recognition,
  historical analogues, structural permanence, conflict decay) and the
  track-record surface. Theme Temperature and RULE_PHARMA also unstarted.
- ⚠️ **Residual risk holding the floor down:** no off-volume database backup
  (local snapshots share the primary DB's failure domain); no end-to-end
  restore ever tested. This is the single biggest hazard and it gates nothing —
  it just has to be fixed. See [[Current Blockers]].

The honest read: *the mechanism is strong and ahead of plan; the thing that
makes Scope defensible rather than a well-built scraper — the reasoning layer —
hasn't begun, and it can't begin credibly until the outcome dataset matures.
Our job now is to protect the floor, feed the clock, and be ready to build the
moment the gate opens.*

---

## The Plan — phases from here to iPhone 15

Phases are sequenced by dependency, not calendar. Phase 0 and Phase 1 run
**in parallel** (one is active work, one is a passive clock). Phases 2+ unlock
as gates open.

### Phase 0 — Harden the floor *(active now — infra hygiene)*

**Objective:** eliminate the residual risks that could erase the moat or take
the product down, so that everything built later stands on solid ground.

- [ ] Review + merge `feat/llm-fallback` (`9f77654`) — Groq primary/fallback narrative generation
- [ ] Review + merge `fix/remove-dead-generate-brief-job` (`d3687eb`)
- [ ] Add `GROQ_API_KEY_FALLBACK` to Railway prod env (after the merge — fallback is inert in prod without it)
- [ ] Provision off-volume backup storage (`BACKUP_S3_*` creds + `boto3`); `db_backup.py:upload_remote()` activates with no code change
- [x] **Restore procedure verified end-to-end (2026-07-21)** — both the preferred `snapshot_*.db.gz` and the fallback raw hourly copy restore to a complete DB (`integrity_check=ok`, 37 tables, 11 migrations, row counts match source). `RESTORE.md` steps 1–4 exercised against a scratch copy; the live DB was never touched. **Still owed:** a restore from an *off-volume* snapshot (blocked on remote storage above), and a rehearsal of the production swap (steps 5–8).
- [ ] Confirm disk-usage fix (resized to 5GB) is real — flagged unverified in [[Current Blockers]]

**Gate:** none — this is always-safe hygiene work.
**Exit criteria:** an off-volume backup exists, a restore has been demonstrated,
the two review-ready branches are resolved, and no CRITICAL item sits in
[[Current Blockers]] infrastructure.
**Discipline:** merges to `main` require explicit human approval (project rule);
scoring/ingestion changes stay human-gated (see the Constraints section).

### Phase 1 — Feed and read the calibration clock *(active now — passive + one build)*

**Objective:** turn accumulating `alert_outcomes` rows into a *usable calibration
signal*, and replace the win-rate placeholder with truth. This phase is what
converts the iPhone-8 *foothold* into iPhone-8 *proper*.

- [ ] Let the +20d clock run — do **not** rush feature work while data maturation is the real lever
- [ ] Build the **calibration report**: per-rule realized win rate, mean/median SPY-alpha, sample size, confidence interval, by horizon bucket
- [ ] Define a **minimum-sample threshold** per rule before its stats are trusted (avoid reading noise from small n — see [[Outcome Tracking Status]])
- [ ] Replace the `win_rate*10` fixed 0.5 placeholder in `calculate_opportunity_score` with per-rule *realized* win rate, **for rules over the threshold only** (human-gated scoring change)
- [ ] Surface calibration in the UI (per-rule track record on the war-room / decomposition view)

**Gate:** enough resolved outcomes per rule to exceed the minimum-sample
threshold. Until then this phase is *mostly waiting*, and that is correct.
**Exit criteria:** a calibration report exists and is trusted; opportunity
scoring uses real win rates for qualifying rules; the placeholder is retired
for those rules.
**Discipline:** **never** recompute historical scores against today's population
(`enrich --all` / `only_unscored=False` is forbidden on history — it destroys the
detection-time record the whole moat depends on). New calibration informs *future*
scores only.

### Phase 2 — First reasoning + remaining rung-5 specialization

**Objective:** build the first capabilities that *reason* over the calibrated
dataset, plus finish the sector specialization that rung 5 still owes.

- [ ] **Theme Temperature** — needs the circularity-guard design session first (a theme's temperature must not feed back into the scores that created it). Design → decision note → build. Deferred pending that session.
- [ ] **RULE_PHARMA** — pharmaceutical-sector specialization (FDA calendar / clinical-trial / approval signals as a distinct detection family)
- [ ] **Regime recognition (v1)** — classify the current market regime and condition opportunity/expectations on it; gated on calibration having enough per-regime samples
- [ ] **Historical analogues (v1)** — given a live thesis, retrieve the most similar *resolved* theses from `alert_outcomes` and show what happened next

**Gate:** Phase 1 exit (a trusted calibration dataset). Regime recognition and
analogues are meaningless without it; Theme Temperature is gated on its design
session, not on data.
**Exit criteria:** Theme Temperature live with a proven circularity guard;
RULE_PHARMA in production and outcome-tracked; a working analogue-retrieval
surface; a v1 regime classifier feeding the opportunity view.

### Phase 3 — Structural reasoning & multi-asset (iPhone 12)

**Objective:** move from "what converged" to "how durable is this advantage,
and where else does it apply."

- [ ] **Structural-permanence scoring** — distinguish a one-off catalyst from a durable structural edge (e.g., a permanent regulatory moat vs. a single contract)
- [ ] **Conflict & absorption decay curves** — model how corroboration strength and market absorption evolve over time, replacing point-in-time absorption with a curve
- [ ] **Multi-asset expansion** — extend beyond single-name equities (sectors/ETFs, and the currently-`unavailable` basket/multi-ticker outcomes that today can't be labeled)

**Gate:** Phase 2 reasoning primitives proven; enough outcome history to fit
decay curves rather than assert them.
**Exit criteria:** permanence and decay are scored, not assumed; at least one
non-single-equity asset class is detected, scored, and outcome-tracked.

### Phase 4 — Earned authority (iPhone 15, end state)

**Objective:** the defensible product — Scope reasons from memory and can prove
its record.

- [ ] **Pattern memory** — the system recognizes recurring convergence patterns across time and carries them forward as priors
- [ ] **Published verified track record** — the outcome dataset, surfaced as a credible, honest public record (the ultimate proof-of-edge)
- [ ] **Short-side theses** — detect and score negative/short convergences, not just long
- [ ] **Team / collaboration features** — shared war rooms, annotations, and workflow for more than one operator

**Gate:** a multi-year outcome dataset deep enough that pattern memory and a
published record are *earned*, not asserted.
**Exit criteria:** Scope reasons from its own history by default, and can defend
every claim it publishes with logged, forward-tested outcomes.

---

## Constraints — the one-way doors this plan must respect

These are not tasks; they are invariants. Every phase above is designed to stay
inside them. Violating one is how the moat gets silently destroyed.

- **Detection-time scores are immutable.** `enrich_scores` backfills *missing*
  scores and never overwrites. Never run enrichment with `only_unscored=False`
  on history. (See [[RULE Design Decisions]], [[Scoring System]].)
- **The two scores never merge.** Evidence Confidence and Opportunity measure
  different things and are always presented as such.
- **Scoring is human-gated, not agent-automated.** Anything touching
  `insert_alert`, `enrich_scores`, novelty/opportunity math, corroboration,
  `rule_*.py`, ingestion, or schema migrations stays a manual session with a
  human in the loop — this is why there is deliberately *no* scoring subagent
  (CLAUDE.md). These are the DATA-LOSS-class systems.
- **The outcome dataset is not for sale.** It compounds daily and cannot be
  repurchased. Its value is in being kept in house. (See [[Outcome Tracking Status]].)
- **Migrations are additive-only**, tracked in `scope_migrations`; never drop a
  table.
- **`main` merges require explicit approval.** Feature work happens on branches.

---

## Risk register (living)

| Risk | Severity | Status / mitigation |
|---|---|---|
| No off-volume DB backup | **Critical** | Phase 0. Local snapshots share the primary failure domain. `upload_remote()` is storage-ready — needs creds only. *(Restore **procedure** verified 2026-07-21 — see Phase 0; the residual risk is now purely the missing off-volume copy, not an unproven restore.)* |
| Reasoning layer unstarted while it's the whole defensibility story | High | Accepted for now — correctly gated on calibration (Phase 1). Risk is *delay*, not *direction*. |
| `enrich_scores` is a single point of failure (~15 path-(b) rules) | Medium | Guarded by hourly `MONITOR_ENRICH_STALL`. Not migrating working scripts. |
| Reading noise from small per-rule outcome samples | Medium | Phase 1 minimum-sample threshold before any rule's stats are trusted. |
| Calibration can't be accelerated | Low (inherent) | Structural — let the clock run; don't rush feature work to compensate. |

---

## Where the detail lives

- **This session / this week:** [[Roadmap Tracking]] (short-horizon), session
  notes in `02_Sessions/`.
- **Stage-by-stage status:** [[iPhone Stage Progress]].
- **Blockers & pending human decisions:** [[Current Blockers]].
- **The moat:** [[Outcome Tracking Status]].
- **How scoring works / why it's immutable:** [[Scoring System]],
  [[RULE Design Decisions]].
- **Engineering ground truth:** `Scope/CLAUDE.md` (always wins over this doc on
  facts).

---

## Change Log

*One line per update. Newest first. This is how the plan proves it's alive.*

- **2026-07-21** — Restore procedure verified end-to-end against a scratch copy
  (preferred snapshot + fallback raw copy; `integrity_check=ok`, counts match
  source). Phase 0 restore box checked. Critical backup risk narrowed to "no
  off-volume copy" only — the restore is no longer unproven.
- **2026-07-21** — Master Plan created. Defined North Star, maturity ladder,
  4-phase path (Phase 0 harden / Phase 1 calibrate / Phase 2 first-reasoning /
  Phase 3 structural / Phase 4 authority), constraints, and risk register.
  Positioned "You Are Here" at iPhone-5 with an iPhone-8 foothold, at the
  calibration pivot's threshold.
