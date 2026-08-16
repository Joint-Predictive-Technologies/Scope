---
aliases: [Roadmap Tracking]
type: roadmap
stage: iPhone-5
status: active
priority: high
tags: [roadmap, planning]
related: [[Master Plan]], [[Make It Real campaign]], [[iPhone Stage Progress]], [[Known Issues]]
---

# Active Roadmap Tracking

> 🎯 **The live priority is the [[Make It Real campaign]]** — the ordered push to make the
> instruments honest before anything else is built on top of them. **This note is the
> short-horizon tracker feeding that campaign**, not an independent queue: the "Queued"
> section below *is* the campaign order.
>
> ⚠️ `[[Make It Real campaign]]` **does not yet exist as a note in this vault** — same
> forward-reference handling as in [[Product Identity]] and
> [[instrument-definitions-and-tiers]]. Until it is written, the campaign order lives here
> and in [[SESSION-2026-07-30-state-reconciliation]]. Note also that [[Master Plan]]'s own
> "Phase 3" is a **different** phase (structural reasoning & multi-asset, iPhone 12) from
> the campaign's Phase 3 rule repairs below; do not conflate them.

What's in flight, what's up next, dependencies. This is the short-horizon view;
the long-term phased plan it eventually feeds into lives in [[Master Plan]].

*Last reconciled: **2026-08-01**, against the 2026-07-30 state-reconciliation ledger
([[SESSION-2026-07-30-state-reconciliation]], anchored at `main` = `ce1c7be`, prod proven
to be running the same SHA), plus [[SESSION-2026-08-01-rule15-severity-mechanism]] for
campaign item #2. **All branch / merge / deploy status below is quoted from that ledger,
not independently re-derived.***

## In Flight / Awaiting Review

Per the ledger's Stage 1 — **46 local branches *and* all remote refs**, of which exactly
**eight** are not ancestors of `main` — almost nothing listed here is still pending. The
RTX-class work is merged **and** deployed.

**Merged + deployed (ledger items 1–11, every one PROVEN; prod is running `ce1c7be`):**
basket-rule exclusion (RULE_08 / ADSB / TELEGRAM_OSINT), the direction rework (signed
insider leg + cap-relative contract weight), the RULE_15 `ingested_at` predicate, insider
clusters closeout, the RULE_11 contracts repair (**code only**), market-cap plausibility,
RULE_06 reliability, both surfacing fixes, win-rate placeholder honesty, and OSINT/ADSB
stop-bleeding. **Do not re-merge or re-deploy any of it** — everything still outstanding on
this class is *data and verification*, not code (see Queued).

⚠️ **"Merged + deployed" is not "in effect."** At reconciliation the signed insider leg and
the RULE_15 epoch filter had **zero effect on data**, purely because RULE_06 and RULE_15 had
not run since the deploy. Their first genuine test is each rule's next emission — that check
is campaign-adjacent, not a merge.

**Genuinely pending — one branch:**

- 🔴 **`feat/llm-fallback`** (`9f77654`, **remote-only**, 1 commit, 8 code files, last
  touched 2026-07-21) — Groq primary/fallback narrative generation. Still unshipped:
  `main` builds `Groq(api_key=…)` inline at `api/main.py:1186` with a single key and no
  retry. ⚠️ **Rebase and re-read before merging** — do not fast-forward week-old work into
  a tree this changed. Its prod follow-up (`GROQ_API_KEY_FALLBACK` in Railway) stays parked
  until it lands.

**Deletable, not pending:**

- **`fix/rule06-incremental-window`** (`ff37420`) — **already on `main`.**
  `rule_06_form4.py` there has `TIME_BUDGET_SECONDS = 240` and watermark-based incremental
  scanning, and prod's activity log shows it running that way. Delete the remote ref.
- `origin/fix/remove-dead-generate-brief-job` (`d3687eb`) — **SUPERSEDED.** The dead cron
  entry is already gone from `main` (asserted by `test_cleanup_pass`).
- `chore/retire-rule12` (`c827c2c`) — superseded; RULE_12 retirement is in `main` via
  `chore/cleanup-pass`.
- `feat/insider-cluster-discovery` (`5ada121`) — superseded; `main` is **+2,288 / −12,713**
  against it.

**The 2026-07-23 design branches — real status:**

- `feat/alert-provenance`, `feat/brief-as-landing`, `design/fey-slash-synthesis` —
  **no longer pending.** None of the three appears in the ledger's complete unmerged
  enumeration (local *and* remote), so each is an ancestor of `main`. The open
  `--text-tertiary` contrast question (2.90:1, below WCAG AA) was never a merge decision
  and survives as a design item, not a branch.
- `origin/design/refero-inspired-pass` (`9de130a`, 10 commits, 2026-07-23) —
  **unmerged and stale; a human keep-or-drop call.** Outside the campaign.
- `origin/docs/vault-ideas-and-reading-list` (`4cf1197`),
  `origin/docs/vault-session-2026-07-21` (`fa37eec`) — vault-only, **0 code files**.
  Housekeeping, not roadmap.

⚠️ **Standing lesson from the ledger:** `feat/llm-fallback` and
`fix/rule06-incremental-window` exist **only as `origin/…` refs**. A local-only sweep
(`git branch --merged main`) reports the tree as fully merged and misses them. Any future
"is everything merged?" check must enumerate remote refs.

## Recently Completed (this bulk-session arc — all merged to main)

- RULE_10 `--emit-alerts` fix — merged (`6ea6a7a`), confirmed live in prod
- RULE_02 scheduling + ingest_senate hardening — merged (`b55e88c`)
- Scheduler-level failure safety net + pdfplumber/pillow deps — merged (`445e3ad`)
- Database backup automation (local interim) — merged (`83b3213`); local
  snapshot verified running; remote upload storage-ready, pending credentials
- Congressional digest standalone view (`/congress/digest/<date>`) — merged (`1647655`)
- Obsidian vault scaffold — merged (`dedd6f5`)
- Production audit sweep (argparse contract, silent failures, scheduler
  reconciliation) — completed, findings documented in [[Current Blockers]]

## Queued (Next Priority) — the [[Make It Real campaign]] order

Ordered by **live user-visible harm**, not by tidiness — the ledger's ordering, kept.
Every item touches rule scripts, scoring, migrations or prod data, so every item is
**human-gated: one per session.**

1. 🛑 **RULE_11 prod-data repair — campaign item #1. STOPPED at Stage 0, awaiting the
   human.** Preparation and enumeration are complete; execution needs prod access.
   Nothing has been mutated. See [[SESSION-2026-07-30-rule11-prod-data-repair]].
   - **The blocker:** `SELECT COUNT(*), SUM(verified_at IS NULL) FROM contracts`.
     `/contracts/data` does not project `verified_at` (zero hits anywhere in
     `Scope/api/`), and `--clear-unverifiable` blanks *exactly* that set — its blast
     radius is unknowable from outside prod. The duplicate-`award_id` preflight is
     already **CLEAR** (436 ids, 436 distinct, zero duplicates).
   - **Why it leads:** the code repair is merged and the forward fix works, but the
     historical rows were never remediated and they are **live** — 11 LMT alerts at
     $48,063,737,196 (6 CRITICAL), 7 BA at $31.9B, 3 HII at $12.7B, all one collapsed
     aggregate re-emitted per ingest day. Scope is **31 pre-boundary HIGH+ alerts** and
     **220 of 501 collapsed contract rows** (113 of them with no ticker at all).
   - **It is not display-only:** 155 of the 220 carry an `award_id` and are read by
     `jpt_common::contract_leg_weight`, so inflated amounts are entering the signed
     corroboration gate now. Theme 1's own contract legs are two such rows.
   - **Hazards, before running anything:** key the repair boundary on `verified_at`,
     **never `ingested_at`** (`upsert_award` never rewrites it), and **never pass
     `--clear-unverifiable` in the same run as Phase 1**. A ready-to-run packet and the
     full prior-state tables are in the session note — prod `alerts` has no `updated_at`,
     so that note is the only reversal record.

2. **RULE_15 severity mechanism — diagnosis done, one cell awaiting the human.**
   See [[SESSION-2026-08-01-rule15-severity-mechanism]]. Verdict: alert `34435` (GD,
   +1978%) was **downgraded after insertion by an out-of-band write** — severity is
   `f(trend_pct)` at every version of the file, and the only HIGH→MEDIUM writer needs
   `>14d` where GD is 2.5d. **Scope and timing of that write are UNVERIFIED** and settle
   on one sentence of the human's recollection or two prod queries.
   - ⚠️ **The end state is not self-maintaining.** All 11 RULE_15 alerts read MEDIUM, but
     nothing recurring holds them there: **the next HIGH RULE_15 alert will surface HIGH.**
   - The real follow-up is a product question, not a patch: decide what *should* happen to
     that next HIGH. The 10 downgraded rows are fabricated-denominator artefacts from
     before the epoch fix; with `ingested_at >= REPAIR_EPOCH` deployed, a future HIGH may
     be legitimate, and suppressing it by reflex would hide a real signal.
   - Independent of #1 — it can proceed while #1 is blocked.

3. **Phase 3 rule repairs, in this order: central LIKE → RULE_09 → RULE_01B → RULE_02 →
   RULE_08.** The backlog and per-rule evidence live in [[Current Blockers]]
   ("Rule-repair backlog"); the target shape each repair aims at is
   [[instrument-definitions-and-tiers]].
   - **Central LIKE first — the cross-cutting free win.** `calculate_novelty_score`
     matches `headline LIKE '%<anchor>%'` for *every* rule that anchors on a ticker, so
     any short ticker is exposed. Fix once, several rules clean up together.
   - **RULE_09** — ticker attribution by difflib fuzzy name-match, **wrong 42.6%**
     (92/216); the method was already banned for RULE_11 and remap-migrated (m003/m004),
     and RULE_09 never got either. Plus it cannot finish inside the 300s scheduler cap.
   - **RULE_01B** — first-touch by insertion order (20.3% falsely claim "no prior trade"),
     the 90-day window filters `transaction_date` not `filing_date`, and direction is
     hardcoded "opens new position" while **45.8% are sales or exchanges**.
   - **RULE_02** — exchanges counted as directional, identity keyed on the headline
     string, novelty anchored on a LIKE substring.
   - **RULE_08** — restore `fed-register` as an honest instrument under
     **document-names-a-company** attribution. It is in `RULE_10_EXCLUDED` until then, and
     that exclusion has a real accepted cost: convergences that would have counted a
     `fed-register` leg no longer fire.
   - **RULE_11's code half is already merged** — item #1 above is its data half.
     **RULE_01 stays dormant** (no action). The signed-signal follow-ups in
     [[signed-signal-engine]] are **blocked behind these repairs by design**: signing a leg
     whose attribution is broken makes a future false convergence look *more* credible.

4. **The 2-vs-3 threshold decision.** The gate is `RULE_10_MIN_INSTRUMENTS = 3`. The
   evidence cuts both ways and the decision has not been taken:
   - **For lowering:** on a gate basis **zero** tickers reach 3 in prod; the maximum is 2
     (SPCX, GE, RTX, MSTR), and prod has produced **exactly one theme ever**.
   - **Against lowering:** that one theme fired at exactly 3 with **zero margin**, and one
     of its three legs was an exercise-and-sell — a false convergence at the current bar.
   - **This is why it sits after #3, deliberately.** Lowering the bar on instruments whose
     attribution is still broken multiplies false convergences rather than surfacing real
     ones. Decide it on repaired data, not on today's.
   - Not yet written up as its own note — it should become one when taken.

5. **Offsite backup — the untested restore path.** `upload=skipped`; snapshots live in
   `dirname(db_file)/backups` on the **same Railway volume** as the primary DB, so there is
   still no off-volume copy. `upload_remote()` is storage-ready and activates the moment
   the `BACKUP_S3_*` credentials are set and `boto3` is added — no code change, just the
   credentials, which the human has opted to provision.
   - ⚠️ **Name the gap precisely:** a 0.6h-old `integrity=ok` snapshot with 24h hourly
     retention *is* a complete rollback for a bad write. The restore procedure was verified
     2026-07-21 against a **scratch copy** — but **the restore path has never been
     exercised against prod**. That, not the missing remote copy, is the weaker leg the
     RULE_11 repair kept running into.

**Displaced from the pre-campaign queue** (not dropped — parked until the campaign
closes): the Theme Temperature design session (tracked in [[Current Blockers]] →
"Decisions Pending"), outcome-tracking calibration (passive, clock-ticking — see Risks
below), and RULE_PHARMA design + implementation. `GROQ_API_KEY_FALLBACK` in Railway rides
along with `feat/llm-fallback` and is tracked in "In Flight" above.

## Feature Candidates (backlog)

Prospective features, each tagged by whether it serves **convergence** (Scope's
differentiation) or **standalone value** (utility/retention only). See
[[Competitive Positioning]] for the framing — convergence features are prioritized.

- **Politician search page** — *standalone value.* A per-member directory/search
  surface; utility, not convergence (the data already feeds Congress signals).
- **Whale moves (13F institutional holdings)** — *convergence.* New independent
  source type; institutional accumulation on a ticker congress/contracts also
  touch is real corroboration. Free (EDGAR). Highest-value candidate.
- **Stock splits (Prompt B)** — *standalone value.* Retention/utility feature; no
  convergence contribution.
- **Risk factors (10-K Item 1A)** — *convergence.* Thematic linkage to
  lobbying/FARA/regulatory signals; free (EDGAR).
- **ETF holdings** — *standalone value.* Useful context, but does not strengthen
  the convergence thesis.

## Deferred / Waiting On

- Overwatch vs Scanner mode navigation split (design-first, not code-first)
- Regime recognition layer (needs more outcome data)
- Historical analogues (ditto)

## Risks

- **Database backup — remote storage:** Local interim backup closes most of
  the gap, but no off-volume copy exists yet (same failure domain as the
  primary DB). No production restore has been tested end-to-end. This is
  still the single biggest residual risk.
- **Outcome data calibration:** Takes time. Can't accelerate meaningfully.

---

See also: [[Master Plan]], [[iPhone Stage Progress]], [[Current Blockers]]
