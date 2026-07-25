---
type: session-summary
stage: iPhone-1
status: completed
priority: critical
tags: [session, work-log, rule10, convergence, gate-design, read-only]
related: [[Master Plan]], [[Current Blockers]], [[RULE Design Decisions]], [[Competitive_Positioning]]
date-created: 2026-07-25
---

# Session: Is the convergence gate even reachable?

**Date:** 2026-07-25
**Duration:** ~1 hour
**Branches:** `trace/rule10-convergence` (findings file only — no code, no data changes)
**Status:** Completed

## Goal

One question: can 4 distinct *eligible* rules co-occur on the same ticker within
24h **at all**, or is the RULE_10 gate structurally unsatisfiable? Code-first, so
the answer does not depend on the untrustworthy local DB.

## What changed

**Nothing. Read-only.** No code, data, schema, or migration changes. Two
sub-agents read rule source only; no rule script was executed (they write to the
live DB).

---

## VERDICT: **(B) Structurally unreachable**

The gate design is the bug. **No data-quality or key-normalization fix will make
convergence fire.**

The gate needs **4 distinct eligible rules**. Of the **14** eligible rules
registered in the scheduler, only **6** can actually emit a single-symbol
HIGH/CRITICAL alert — and **3 of those 6 read the same table**
(`transactions`, congressional PTRs). So the system has effectively **4 working
independent instruments**, and the only realistic path to 4 *rules* is the
congressional trio plus exactly one other rule, landing within 24h **of each
other's ingestion time**, across sources whose disclosure lags differ by 0–45 days.

Critically, **fragmentation is not the blocker** — see Step 4 below. Splitting
every multi-symbol composite moves the best-ever distinct-rule count from **3 to
3**. Even removing the HIGH/CRITICAL severity gate entirely *and* splitting
composites still yields **zero** tickers at 4.

This voids queue item #2 (ticker normalization) *as a convergence fix*. It
remains worth doing for RULE_08's sake, but it will not make the gate fire.

---

## Evidence table — the 14 eligible rules

Cadence from `api/main.py:44-113`. Severity/ticker/universe from rule source.
**Can contribute** = can emit a HIGH/CRITICAL alert carrying a *single-symbol*
ticker, which is what `_candidate_alerts` (`scripts/rule_10_corroboration.py:85-97`)
requires and what the `GROUP BY ticker` in `find_corroborated_tickers` (`:114-128`)
counts.

| Rule | Cadence | HIGH/CRIT possible? | Single-symbol ticker? | Ticker universe | Contributes? |
|---|---|---|---|---|---|
| RULE_01B | 120 min | HIGH (`rule_01b_first_touch.py:80`) | yes | arbitrary — congressional PTRs | **YES** |
| RULE_02 | 240 min | HIGH (`rule_02_cluster.py:138`) | yes (can be `''`) | arbitrary — congressional PTRs | **YES** |
| RULE_CLUSTER | 240 min | HIGH/CRITICAL (`rule_cluster.py:204`) | yes — explicitly skips composites (`:129-131`) | arbitrary — congressional PTRs | **YES** |
| RULE_06 | 120 min | CRITICAL/HIGH only (`rule_06_form4.py:351-355`) | yes | any Form-4 issuer — **broadest** | **YES** (but see F1) |
| RULE_09 | cron daily 03:00 | HIGH (`rule_09_lobbying.py:302-306`) | yes, but **often NULL** (`:150-157`) | ~10k issuers via difflib 0.7 | **YES** (partial) |
| RULE_11 | 360 min | CRITICAL/HIGH (`rule_11_contracts.py:77-86`) | yes | **~26 govcon symbols** | **YES** (narrow) |
| RULE_08 | 240 min | HIGH only if FR `significant` flag (`:188`) | **NEVER — always ≥3 symbols** | 37 hardcoded | **NO — C1** |
| RULE_OPTIONS | 15 min | n/a | n/a | n/a | **NO — C2** |
| RULE_13 | cron daily 05:00 | HIGH (`rule_13_fec.py:124`) | yes, often `''` | **4 symbols** | **NO — C3** |
| RULE_ADSB | 5 min | HIGH/CRITICAL (`rule_adsb.py:142`) | yes | **6 symbols** | **NO — C4** |
| RULE_12 | cron Mon 04:00 | HIGH/CRITICAL (`rule_12_fara.py:248`) | yes, often `''` | **5 effective symbols** | **NO — C5** |
| RULE_14 | cron Tue/Fri 04:30 | HIGH (`rule_14_patents.py:205,264`) | yes, often `''` | 18 symbols | **NO — C6** |
| RULE_15 | 360 min | HIGH (`rule_15_earnings_nlp.py:196`) | yes, always set | 21 hardcoded | **MARGINAL — F2** |
| RULE_TELEGRAM_OSINT | 60 min | HIGH (`rule_telegram_osint.py:65`) | yes | **3 symbols** (`USO`/`LMT`/`TSM`) | **NO — C7** |

**Cadence is a red herring.** The work-order hypothesised cadence mismatch as a
binding constraint. It is not: every eligible rule except RULE_12 (weekly) and
RULE_14 (twice-weekly) fires at least daily, and a rule need only contribute
*once* inside a 24h window. The binding constraints are emission capability,
source independence, and the window's time basis.

---

## Findings — CONFIRMED (from code)

### C1. RULE_08 is structurally incapable of contributing — it can never write a single symbol

`SECTOR_MAP` (`rule_08_federal_register.py:26-43`) has 16 keywords, and **every
value has ≥3 symbols** (minimum 3, verified by parsing the literal). The write is
`ticker_str = " ".join(tickers)` (`:183`), and `normalize_ticker`
(`jpt_common.py:988-996`) explicitly preserves multi-token strings rather than
splitting them. Corroboration groups by exact ticker string, so RULE_08's ticker
can never equal any other rule's single-symbol ticker.

Empirically consistent: **72 of 72** RULE_08 alerts in the snapshot are composites,
0 single. RULE_08 is the only rule in the eligible set that writes composites at all.

### C2. RULE_OPTIONS contains zero `INSERT INTO alerts`

It is UPDATE-only (`rule_options_correlation.py:138-142`, `:152-157`) and purely
parasitic — it requires another rule to have already produced a HIGH/CRITICAL
alert on the same ticker within 48h (`:94-96`). It can never be one of the 4
distinct rules. Its `activity_log.emitted` counts *enrichments*, not alerts
(`:167`) — misleading if read as output.

### C3. RULE_13 cannot complete a run under the scheduler

Two independent blockers. (a) It passes `member_funding.candidate_id` as the
`committee_id` parameter to the FEC Schedule A endpoint (`rule_13_fec.py:57`), but
those values are candidate IDs (`S0SD00054`, `H6HI01121`), not committee IDs
(`C…`) — the query cannot match. (b) `time.sleep(1.0)` per member (`:97`) × 931
funded members ≥ 931s against the scheduler's 300s subprocess timeout
(`api/main.py:173`), with `conn.commit()` only after the whole loop (`:157`) — so a
timeout discards every pending insert.

### C4. RULE_ADSB's source is closed

`fetch_military_flights` hits `https://opensky-network.org/api/states/all`
unauthenticated (`rule_adsb.py:25`). OpenSky withdrew anonymous access to that
endpoint; the failure is swallowed (`:74-76`) returning `[]`, then `run()` exits
early (`:118-123`). Ticker universe is 6 symbols regardless.

### C5. RULE_12 cannot pass its own gate with current data

`:233` requires both a current-year and a prior-year receipts total for the same
foreign principal. `fara_filings` holds **3 rows, all `period_start 2026-07-07`**
(current year only) → `prior_amount <= 0` → `continue`, always. Its country is
`Netherlands`, which is not a key in `PRINCIPAL_SECTOR_MAP` (`:32-49`), so the
ticker would be `''` even if it fired.

### C6. RULE_14's YoY branch reads an empty table

`:247-260` reads `patent_filings`, which has **0 rows**, so `prior_count <= 0` →
`continue`. The cluster branch needs live PatentsView (`search.patentsview.org`),
which returns `None` on DNS failure and skips all categories (`:90-98`, `:143-147`).

### C7. RULE_TELEGRAM_OSINT's universe is 3 symbols, and its source is blocked

It uses `REGION_TICKERS[region][0]` only, so the entire universe is `USO`, `LMT`,
`TSM` (`:43-51`). Its six sources are all `rsshub.app/telegram/channel/…`
(`:33-40`); `feedparser.parse` returns an empty entry list on block without
raising, so failure is silent.

### C8. Three of the six working rules are one instrument

RULE_01B (`rule_01b_first_touch.py:42-43`), RULE_02 (`rule_02_cluster.py:31-32`)
and RULE_CLUSTER (`rule_cluster.py:110-124`) all read `transactions` — the
congressional PTR feed. They are three views of a single instrument, not three
independent corroborating mechanisms.

This is visible in the best real case in the data: on 2026-07-20, `SPCX` reached
3 distinct eligible rules — and they were **RULE_01B, RULE_02 and RULE_CLUSTER**.
Three rules, one source. Meanwhile RULE_11 (contracts) *did* fire on SPCX, but on
2026-07-10/11 — nine days outside the 24h window.

The North Star ([[Master Plan]]) specifies "4+ *distinct mechanisms*". The gate
counts **rules**, not mechanisms — so the congressional trio would satisfy it
while representing a single instrument. That is a second, independent defect in
the gate's design.

### C9. The 24h window is on ingestion time, not event time

`_candidate_alerts` filters `created_at >= datetime('now','-24 hours')`
(`rule_10_corroboration.py:94`). `created_at` is when Scope ingested the alert,
not when the underlying event happened. The instruments have structurally
different disclosure lags — PTRs 30–45 days, LDA quarterly, USASpending on award,
Form 4 within 2 business days — so requiring four to land within 24h of one
another is requiring an *ingestion coincidence*, not a real-world convergence.

Re-basing the window on `event_date` is not currently possible: it is populated
only for RULE_01B (106/192) and RULE_11 (84/102), and is **0** for RULE_02,
RULE_06, RULE_08, RULE_09 and RULE_CLUSTER.

### C10. Step 4 — key fragmentation is NOT the blocker

Replaying the exact candidate set (eligible rules, HIGH/CRITICAL, non-empty
ticker; 551 alerts) over every 24h window, counting distinct rules per ticker
as-stored versus after splitting every composite:

| | tickers reaching ≥4 | tickers reaching ≥3 | best |
|---|---|---|---|
| As stored | **0** | 1 | SPCX = 3 |
| After splitting composites | **0** | 1 | SPCX = 3 |
| Splitting **and** dropping the severity gate | **0** | — | 3 (AAPL, MSFT, LMT, SPCX) |

Only **9** composites are in the candidate set at all, every one from RULE_08.
Splitting lifts 15 tickers by one rule each (AAPL 1→2, MSFT 1→2, GS 1→2,
META 1→2, and 11 tickers 0→1) — and moves the maximum not at all.

---

## Findings — HYPOTHESIS

- **H1.** Fixing RULE_08's composites would add a 7th contributing rule and a 5th
  instrument, which is the single highest-leverage normalization change — but on
  the snapshot it does not by itself reach 4 (C10). Whether it does on a complete
  prod dataset is untested.
- **H2.** The most plausible *real* convergence shape given the working
  instruments is `congressional cluster + Form 4 insider buy` on the same ticker.
  That path is currently blocked by RULE_06's reliability (F1), not by the gate
  arithmetic. Untested.

## Findings — FLAGGED FOR LATER

- **F1. RULE_06 emits but never logs.** It has 237 alerts (all HIGH/CRITICAL, all
  tickered) yet **zero** `activity_log` rows — consistent with the documented
  300s timeout killing it before `record_activity`. Its `EDGAR_SEARCH` endpoint
  (`rule_06_form4.py:23`) is also suspect, and a non-200 silently `break`s to zero
  results (`:108-109`). It is the best candidate for the 4th rule and the least
  reliable. Belongs to the scheduler-reliability lane.
- **F2. RULE_15** can re-evaluate off 73 stored `earnings_sentiment` rows even
  when its EDGAR fetch fails, so it is marginal rather than dead. It also runs an
  **unbounded** `UPDATE alerts SET lifecycle_stage='corroborated'` with no time
  window (`rule_15_earnings_nlp.py:240-244`).
- **F3. Zero `SCHEDULER_JOB_FAILURE` rows exist** in the snapshot's `activity_log`
  despite RULE_06 timing out on every run. `Scope/CLAUDE.md` claims the safety net
  is universal. Either the net is not firing or the snapshot's log is truncated —
  needs the scheduler-reliability lane.
- **F4. Seven eligible rules have no `activity_log` rows at all** across the
  snapshot's 11-day window (RULE_06, RULE_12, RULE_13, RULE_14, RULE_15,
  RULE_ADSB, RULE_OPTIONS, RULE_TELEGRAM_OSINT). RULE_ADSB is on a 5-minute
  cadence and should have hundreds. DB-derived and therefore weak, but it points
  the same way as C3–C7.
- **F5. Permanent dedups suppress re-firing.** RULE_01B dedups per
  `(ticker, member_id)` with **no time bound** (`:73-78`); RULE_11 dedups by
  `award_id`/`ticker+award_date` with no time bound (`:101-107`);
  RULE_TELEGRAM_OSINT dedups per link with no time bound (`:116-121`). Each rule
  therefore fires on a given ticker roughly once, ever — which further reduces the
  chance of four coinciding.

---

## Provenance

**From code (authoritative, snapshot-independent):** the entire evidence table,
C1–C9, F1–F2, F5. Cadences from `api/main.py:44-113`. Rule behaviour from the
rule sources cited inline. No rule script was executed.

**From the local DB (weak secondary evidence, explicitly labelled):** C10's
counts, the 72/72 RULE_08 composite cross-check, the SPCX example in C8, and
F3–F4. The snapshot `Scope/data/jpt.db` is a committed git artifact whose last
alert is `2026-07-20 13:25:14` UTC; the test suite writes to and deletes from it,
and git operations restore it to older states. Every DB figure here is
corroborative only — **no verdict rests on one**. The verdict follows from C1–C9,
which are code facts.

**Needs a prod re-run to confirm:** whether any ticker has *ever* reached 4
distinct eligible rules in production. The query is the Step-4 replay in this
note, run against the production DB. Note that C1–C7 predict the answer is no
regardless, since those rules cannot contribute anywhere.

**Not verified per §7:** the work-order requires verification via a `verifier`
subagent. **No such subagent exists** — see the reality-check section below.

---

## Next thread — what #1 hands forward

The verdict **reorders the queue**:

- **Queue #2 (ticker/key normalization) is voided as a convergence fix.** C10 is
  decisive: splitting composites does not move the ceiling. It should be
  re-scoped as a data-quality fix (it would make RULE_08 able to participate at
  all, C1/H1) and **de-prioritized** below the gate-design decision.
- **A new item becomes #1: a human gate-design decision.** Per the work-order this
  session must not propose or apply one. The decision needs to address four
  separable defects, in this order of leverage: (i) the gate counts *rules* where
  the North Star specifies *mechanisms* (C8); (ii) the window is on ingestion time
  across sources with 0–45 day lags, and `event_date` is too sparse to re-base it
  (C9); (iii) 8 of 14 eligible rules cannot contribute at all (C1–C7); (iv) the
  threshold of 4 was set against an instrument pool that does not exist.
- **Queue #1 (test isolation + untrack `jpt.db`) still stands**, and gains
  urgency: the verdict rests on code precisely *because* the DB cannot be trusted.
- **F1 (RULE_06) escalates.** It is simultaneously the broadest-universe rule, the
  most likely 4th contributor (H2), and the least reliable. It belongs to the
  scheduler-reliability lane and is now on the critical path to convergence.

Nothing in [[Competitive_Positioning]] changes: adding sources remains the wrong
lever. This session strengthens that — the engine has 4 working instruments and a
gate that needs 4 *rules*, so new feeds would not converge either until the gate
design is settled.

## Reality checks against the work-order

Three points where the work-order and the repo disagree, per its own §0
("if reality disagrees with this document, say so and stop"):

1. **`vault/LONG_TERM_PLAN.md` does not exist** — not at that path nor at
   `vault/Scope/LONG_TERM_PLAN.md`. It was untracked earlier today and has since
   been removed. There is nothing left to propose deleting.
2. **There is no `verifier` subagent** (§7). The five agents scaffolded on
   2026-07-25 are `data-integrity`, `signal-scoring`, `scheduler-reliability`,
   `provenance-guardian` and `diff-gatekeeper` — none is a verifier, and file-based
   agents only load at session start, so none was available this session. Findings
   here were cross-checked by reading code directly and by an independent DB
   replay, **not** by a verifier subagent.
3. **Cadence is not a binding constraint** (§5 method step 3 assumed it may be).
   Recorded above so the assumption is not carried forward.

## Human-gated

Per `Scope/CLAUDE.md`, corroboration logic, rule scripts, scoring, ingestion and
migrations are manual human-in-the-loop work. This pass changed nothing. The gate
redesign implied by verdict (B) is **explicitly deferred** — no threshold, window,
eligibility-set or severity change was proposed or applied here.

---

### Related

[[Master Plan]], [[Current Blockers]],
[[SESSION-2026-07-25-rule10-convergence-trace]], [[RULE Design Decisions]]
