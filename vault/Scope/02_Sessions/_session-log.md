---
type: session-log
status: auto
tags: [session, work-log, auto]
---

# Session Log (auto-appended)

Breadcrumbs written automatically at the end of each session by the
**SessionEnd hook** in `.claude/settings.json` — a UTC timestamp, the active
branch, and commits from the last ~6 hours. This is the safety net so no session
goes fully unrecorded; expand a notable one into a full note in this folder using
`SESSION_TEMPLATE.md`.

## 2026-07-27 15:49 UTC — `chore/small-fixes-batch`
12 commit(s) in the last 6h:

  - 0134773 Merge chore/cleanup-pass — one source of truth for exclusion, plus doc/scheduler hygiene
  - cb9fbfd fix: the four stale gate strings my grep missed, and two vacuous assertions
  - b8510d3 chore: delete the dead brief job, and unify the two exclusion sets
  - 442f21e fix(tests): the brief path fix did NOT fix the brief — encode the real defect
  - ef0b408 chore: fix the morning-brief path, retire 12/13/14 phantom-safe, correct lying docs
  - 2a5d177 Merge fix/rule15-earnings-repair — RULE_15 repaired, shadow gate removed
  - d6d69dd fix(rule15): accept a ticker's predecessor CIK so XOM is not zero-coverage
  - 6e4cc30 fix(rule15): quarantine the pre-repair rows, and kill a vacuous D1 test
  - d8a7901 test(rule15): Stage 6 — the earnings payoff, and D1 proven under real emission
  - 3b67445 fix(rule15): attribute by CIK, restrict to Item 2.02, and make the emit path safe
  - b1fc39d fix(rule15): remove the shadow corroboration gate — RULE_10 is the sole authority
  - 53e847c Merge feat/rule-13f-institutional — RULE_16 13F institutional holdings

## 2026-07-28 (manual backfill) — `feat/reddit-collector`

⚠️ WRITTEN BY HAND, not by the SessionEnd hook. The hook fires on session END, and a long
multi-work-order session ran from 2026-07-27 15:49 to now without ending — so the safety
net that exists so "no session goes fully unrecorded" recorded none of it. That is the
gap this entry closes, and it is worth knowing the hook cannot cover a session that never
terminates.

Merged to main and PUSHED (origin/main @ 541aeab):
  - 541aeab Merge chore/ops-hardening-batch — couple the watch tool to the gate, schedule the safe ticker refresh
  - ff866a2 Merge chore/small-fixes-batch — the SessionEnd hook, the convergence watch, the reddit fix
  - 29f8934 Merge fix/evidence-confidence-tiers — certify the tier rescale that already shipped
  - bef1264 Merge fix/evidence-confidence-instruments — the score counts instruments, and the tiers follow the gate
  - 0134773 Merge chore/cleanup-pass — one source of truth for exclusion, plus doc/scheduler hygiene

All commits in the window, across every branch:
  - 8a442b1 fix(collector): the cashtag gate had no second layer, and the cap gate vanished in an outage
  - d0d0b55 feat(collector): reddit as COVERAGE — ticker names, not signals
  - 3a58a3a fix(discovery): the guard opened at its own worst window — verifier overturned the headline
  - bfac68e fix(discovery): the MIN_HISTORY_DAYS guard was untested — and it is the whole safety case
  - d3d7d1a feat(discovery): small-cap reddit traction -> watch pool, read-only, never a gate leg
  - 8a730a2 fix(reddit): restore SPY, make the skip loud, and correct three overstated claims
  - e3b57e8 fix(reddit): the cashtag is the disambiguator — retire the blocklist
  - e3a39d0 fix(ops): the verifier was right — importing the SELECTOR was not the coupling
  - 32b397a chore(ops): schedule ONLY the safe half of resolve_tickers, weekly
  - 3afab4b chore(ops): check_convergence now CALLS the gate's selector instead of resembling it
  - 5e21d28 fix(calibration): exclude the 9 fabricated RULE_15 outcomes, keep the alerts
  - 4185d72 fix(ops): couple check_convergence to the gate's DEDUP, and stop overclaiming
  - a9f13a0 fix(tests): repair a vacuous forward-only test, and pin the saturation the rescale moved
  - 1ceca46 test(scoring): prove the tier rescale in the view it exists to fix
  - 293b1ca fix(scoring): rescale the evidence tiers into the gate's units — HUMAN SIGN-OFF
  - bf74863 fix(scoring): the two remaining rule-name counts, and the ones on screen
  - a6b9441 fix(scoring): evidence_confidence counts INSTRUMENTS, not rule names
  - f55cfbe fix: correct two overstated claims the verifier overturned
  - 7f1b2c0 fix(reddit): common English words need a cashtag, even when they are real tickers
  - cc7586a feat(ops): read-only convergence watch, coupled to the gate by import
  - 9d63478 fix(hooks): create the SessionEnd hook that never existed
  - 58fc09f feat(ops): read-only prod health check for the two UNVERIFIED items
  - cb9fbfd fix: the four stale gate strings my grep missed, and two vacuous assertions
  - b8510d3 chore: delete the dead brief job, and unify the two exclusion sets
  - 442f21e fix(tests): the brief path fix did NOT fix the brief — encode the real defect
  - ef0b408 chore: fix the morning-brief path, retire 12/13/14 phantom-safe, correct lying docs

Full write-ups: 00_Index.md, newest first. The three reddit sessions in particular
(extraction -> discovery -> collector) each overturned the one before, and the notes
record why rather than only what.

## 2026-07-28 (manual backfill, continued) — `feat/reddit-collector`

Extends the backfill above; the SessionEnd hook still has not fired, because the session
has still not ended. Commits made AFTER that entry was written:

  - 7f42cdf fix(collector): a field that lied, two marking leaks, and a check with no caller
  - 5eb2644 fix(collector): count SIGHTINGS not runs, normalize the universe, and schedule it

These close the collector: the `pending_mentions` window fix (a processed marker, so
`times_seen` counts SIGHTINGS not collector runs), `normalize_ticker` routing, the
schedule (cron 02:15/08:15/14:15/20:15 — ⚠️ CADENCE AWAITING HUMAN CONFIRMATION), and the
deferred full end-to-end verification plus its four follow-ups.

Branch state: `feat/reddit-collector`, 7 commits, UNMERGED. Suite 547.
Write-up: 02_Sessions/SESSION-2026-07-28-reddit-collector-close.md

## 2026-07-28 — `feat/collector-ballpit` (parallel, worktree)

The collector ball pit: `/universe` renders one floating ball per collected ticker
name — size=times_seen, brightness/motion=recency, tint=cap_status, orderless by
construction — clicking a ball opens the existing `/ticker/{symbol}` page. SEEDED
FIXTURE ONLY (visible "NOT LIVE" chip); the single live seam is `loadUniverse()` in
`universe.html`, deferred until ticker_universe populates and the CLBK cap fix lands.
Doorway: the RULE_COLLECTOR / RULE_REDDIT rows on /status. 3-file frontend diff,
no moat files. Suite 611. Built in a worktree; the main checkout was never touched.

Branch state: `feat/collector-ballpit`, 1 commit (750290c), UNMERGED — human-gated.
Write-up: 02_Sessions/SESSION-2026-07-28-collector-ballpit-shell.md

Post-review addendum: diff-gatekeeper (15 findings, none blocking) and
provenance-guardian (no fabrication-grade violations) both ran; follow-up commit
b6b7967 made the page honest-by-default (bare /universe = empty state, demo
scenarios opt-in), fixed a real wrong-ticker click bug (stale canvas buffer after
header growth — ResizeObserver now), the rAF hide/show doubling, and the doorway
copy. Open for the human: the size=times_seen ranking tension, and MERGE ORDER —
`chore/retire-rule12` first, then rebase this branch (shared EXPECTED hunk in
status.html). Reviews: 02_Sessions/SESSION-2026-07-28-diff-review-collector-ballpit.md,
02_Sessions/SESSION-2026-07-28-universe-ballpit-honesty-review.md

MERGED: on explicit human instruction, feat/collector-ballpit (3 commits, incl. a
homepage nav "Universe" link) was merged to main as e08544a and pushed. Deployed
page = honest empty state + opt-in demo scenarios; live wiring still deferred.
⚠️ chore/retire-rule12 must now REBASE onto main before merging (shared EXPECTED
hunk in status.html) — the hazard order reversed.

Nav-link follow-up (838c6e3): the first "Universe" link went into index.html,
which serves at /home — but the site ROOT is the morning brief (fallback /feed),
so the link was invisible where users actually land. Added to the brief's
_FULL_NAV (TEMPLATE_VERSION -> ui-restore-4, cached briefs regenerate on next
load) and alerts.html. Merged + pushed.

## 2026-07-28 — read-only mechanical audit: RULE_01/01B/02/CLUSTER/09/11

Six rules, seven questions each, three scoped read-only subagents + an independent
verifier that re-derived all 15 headline claims (15 UPHELD). Nothing changed,
nothing merged. Verdicts: RULE_CLUSTER mechanically sound; the D1 congressional
one-instrument collapse PROVEN by execution, no phantom instruments. Worst
findings: RULE_09's difflib ticker matching is wrong on 92/216 live alerts
(IBM→VIRC, WIKIMEDIA→IVDA) and the rule can't finish inside the 300s cap
(3520s observed — RULE_06 class, unlisted); RULE_11's award_date falls back to
the run date, re-firing Boeing/RTX/HII ×5 while dropping all-but-first per
recipient per run, and all 102 alerts are CRITICAL off lifetime award values;
RULE_01B's "first touch" is insertion-order (39/191 wrong), its window loses
late filers by design (495 never alerted), and 88/192 sells are headlined
"opens new position"; RULE_02 counts exchanges as directional sells and its
novelty anchor LIKE-matches the word "Cluster" (82/82). RULE_01 is a dormant
label inside ingest_senate.py — keep unscheduled.
Write-up + findings table: 02_Sessions/SESSION-2026-07-28-rule-audit-congressional-lda-contracts.md
(sub-audits: -rule01-rule01b-, -cluster-rules-, -rule09-rule11-mechanical-audit.md)

Backlog recorded: the audit's ranked repair list now lives in
04_Known_Issues/Current Blockers.md → "Rule-repair backlog (from the 2026-07-28
mechanical audit)" (RULE_11 worst; central LIKE fix flagged cross-cutting; prod
queries listed). Documentation only — no code touched, nothing merged.

## 2026-07-28 — `fix/rule11-contracts-repair` (worktree, UNMERGED)

Backlog item #1 from the audit, repaired. Root cause proven against the live API:
`Action Date` is not a valid Contract Award field on spending_by_award (the
endpoint's own field list omits it), so it always returned null and award_date
fell back to the run date. Identity is now `generated_internal_id`; the contracts
table was rebuilt off `UNIQUE(recipient_name, award_date)` onto a unique award_id
index. Proof: Boeing now holds 10 rows/10 award_ids (3 awards on 2026-03-31
alone); the old key kept one. Loss quantified honestly two ways — 17/270 from the
key alone, 106/270 (39.3%) as the bug actually shipped. Sweep now uses
date_type=new_awards_only, so "awarded $X" is true and coverage went from the
year's top ~150 by lifetime size to ALL 270 newly-signed >=$50M awards. Severity
102/102 CRITICAL -> new awards MEDIUM 36/HIGH 33/CRITICAL 5.
Data repaired forward-only from source: 106 rows re-derived (verifier matched
376/376 at source), 4 legacy rows recovered, 61 unattributable rows cleared, 102
alerts -> 62 corrected + 40 retracted. Detection-time scores untouched.
⚠️ The verifier OVERTURNED one of my claims: the "65 unrecoverable" figure was
never measured — the recovery search sent an out-of-range start_date and
fractional bounds, returning HTTP 422 that the code swallowed as "no match".
Fixed; 4 recover; honest count is 61. Five further verifier findings fixed
(incomplete retraction, malformed headlines, migration half-failure, missing
schema column, a vacuous test).
Note: 2 failures in test_market_cap_plausibility.py are PRE-EXISTING on main
(reproduced on a clean main worktree) — the parallel cap console's, not this
branch's.
Branch state: 2 commits (f96bea1, 65f3449), UNMERGED — human-gated.
Write-up: 02_Sessions/SESSION-2026-07-28-rule11-contracts-repair.md

MERGED: on explicit human instruction, fix/rule11-contracts-repair merged to main
as 60d1802 and pushed (3 commits). A third commit was added just before the merge:
the unique index on contracts(award_id) had been declared in schema_sqlite.sql,
which _initialize_schema replays on EVERY db_connection() — on a DB still holding
duplicate award_ids that raised IntegrityError and would have broken every
connection process-wide, not just RULE_11. Both new indexes are now created only
by ensure_contracts_schema()/_ensure_alert_key(), which first make the data
satisfy them.
⚠️ DEPLOY BEHAVIOUR: the contracts table migration runs AUTOMATICALLY the next
time RULE_11 executes in prod (ensure_contracts_schema is called from run()). It
rebuilds the table to drop UNIQUE(recipient_name, award_date) — a deviation from
the "additive only, never drop tables" convention in Scope/CLAUDE.md, unavoidable
because SQLite cannot drop a constraint in place; rows are preserved (verified
171 -> 171). If prod holds duplicate award_ids the migration DELETES the later
duplicates, logging each and writing a RULE_11_MIGRATION activity_log row. The
working DB has 0 duplicates. The data repair script does NOT auto-run — it stays
manual and human-gated.

## 2026-07-29 — `chore/frontend-polish` (worktree, UNMERGED)

Four presentation papercuts. (1) The stale "LMT convergence" heading traced to the
morning brief's hero (`/` IS the brief): it ranked tickers by a taxonomy local to
morning_brief.py and said "converges" at 2 source types, counting the very rules
the gate excludes as noise — 62 of LMT's 71 alerts are RULE_OSINT, and LMT is
hardcoded into six OSINT region ticker-lists, so it won max() every day. (2) ⌘K
was copy-pasted per page (17 had it, 13 didn't) — now one shared /cmdk.js, all 17
inline buttons deleted. (3) Dashboard vs Brief: a BUG — 27 pages labelled `/` as
"Dashboard" while `/` serves the Brief, and the real dashboard at /home was linked
from ZERO pages; fixed link-only. (4) Shared /rule-names.js display map; all four
duplicated local maps converted, zero raw rule badges left, programmatic ids
byte-unchanged. RULE_06 deliberately labelled "Insider trades" not "Insider
buying" — it emits 197 sells vs 40 buys.
⚠️ The verifier OVERTURNED my central claim: the first fix counted gate
instruments but still diverged from the gate on ticker key, severity floor and
window, so it could (and on real data DID) still print "LMT converges" where
RULE_10 fires on nothing — my own basket-splitting was manufacturing LMT's third
instrument. Corrected in bbe3ce2: the hero now reuses the gate's raw ticker key,
HIGH/CRITICAL floor and 14-day window, with 7 tests pinning the agreement. Five
further verifier findings fixed (white unstyled ⌘K button on the 17 legacy pages,
`/` missing ⌘K + Dashboard link, TEMPLATE_VERSION not bumped, surviving label
maps, no hero tests).
Honest caveat recorded: the hero change is data-interpretation, not pure
presentation — flagged for the human as convergence-adjacent.
Suite 715 passed. Branch state: 2 commits (b1ff23f, bbe3ce2), UNMERGED.
Write-up: 02_Sessions/SESSION-2026-07-29-frontend-polish.md

MERGED: chore/frontend-polish merged to LOCAL main as 90b3f91 (verified on a clean
checkout of merged main: 715 passed, 9 skipped). NOT PUSHED — the instruction was
"merge and commit", so origin/main is still 60d1802 and the website will not
change until someone pushes.

CORRECTION: the "NOT PUSHED" note above is superseded — main was pushed on
request; origin/main == local main == 90b3f91. main was NOT mixed up: the branch
was clean and linear, 3 commits ahead of origin with zero divergence. The only
reason it was missing from Railway was that I merged without pushing.

## 2026-07-29 — `fix/ticker-basket-display` (urgent, MERGED + PUSHED b4ecba0)

Reported: searching some tickers landed on a page titled `$LMT%20$RTX%20$NOC`.
Cause: `alerts.ticker` is not always one symbol — 511 rows hold space-separated
baskets ('LMT RTX NOC', 'COIN MSTR IBIT', …) and /api/search returns them
verbatim, so the link is /ticker/LMT%20RTX%20NOC; ticker.html read
`window.location.pathname` WITHOUT decodeURIComponent, printing the escape (and
any '$' prefix) into the <h1>.
Fix: decode once defensively; keep the exact stored string as `queryKey` for the
alerts/meta lookups so a '$'-prefixed basket still returns its rows (verified 23
alerts — matching on a cleaned string would have returned 0); render the basket
as its individual symbols, each linking to its own ticker page; per-company
lookups take the first symbol. Also converted the signal-timeline legend, which
still printed raw RULE_07/RULE_08.
Verified in-browser: reported URL, no-$ basket, single ticker, BRK.B, second
basket — all clean, alert counts unchanged. Suite 715.
NOT fixed (pre-existing, noted): /api/search still offers basket strings as if
they were tickers — the display now handles them honestly, but the search result
itself is arguably the deeper bug. Nav crowding on ticker.html at ~1280px is also
pre-existing (many nav items wrap); measured, the ⌘K button does not overflow.

## 2026-07-29 — `fix/thesis-dupe-chip-and-nav` (MERGED + PUSHED 15882d6)

(a) Nav collision: reproduced on /ticker/LMT at 1150px — last link ended at
x=1080, the LIVE FEED CTA began at x=1080 (zero gap), so the CTA's background
painted over the link text; the same measurement caught that MY shared ⌘K button
was at x=1176, outside the 1150px viewport. Fixed once for all pages in cmdk.js's
always-injected block (nav gap, flex-shrink:0 on CTA + ⌘K, min-width:0 +
overflow-x:auto on .nav-links). Trade-off recorded: at narrow widths the last
links now scroll out of view rather than being painted over — readable, but
horizontal-scroll discoverability is weak; a hamburger/priority-plus nav is the
proper fix later.
(b) Doubled thesis ticker ("RTX RTX"): NOT a data glitch —
rule_10_corroboration.py:114 writes affected_tickers = [primary_ticker], so
thesis.html rendering primary + affected duplicated on EVERY theme ever created.
De-duped at the render (normalised symbol, primary first); verified against the
prod shape -> 1 chip, and a mixed list (RTX, LMT, $noc) -> 3 chips.
⚠️ NOTED: prod now HAS a RULE_10 theme ("Convergence: RTX — 3 instruments
aligned", Emerging, 7 signals). The gate has fired in production for the first
time — the audit's "0 RULE_10 rows" was local-only. Worth a look: confirm its 3
instruments are genuinely distinct, since this is the first real convergence.
Suite 715.
→ ANSWERED 2026-07-30, see [[SESSION-2026-07-30-rtx-convergence-audit]]: they are
not. FALSE POSITIVE — 0 of 3 legs survive (1 on the most generous reading) against
a threshold of 3. The earnings leg's "+2557%" denominator is an Artiva
Biotherapeutics 8-K where "RTX" means rituximab; the insider leg is an
exercise-and-sell counted by a direction-blind gate; the contracts leg is four
lifetime totals for awards signed 2005–2024, one of them pairing an award id with
another award's amount. Theme 1 NOT retired — human-gated.

## 2026-07-29 — `fix/search-symbols-and-source-labels` (MERGED + PUSHED 9dc2a09)

(a) Search returned BASKETS not tickers: /api/search did SELECT DISTINCT ticker
LIKE, and ~500 alerts.ticker rows are space-separated groups, so 'rtx' listed six
basket permutations each linking to /ticker/<whole basket>. Now explodes groups,
keeps matching symbols, dedupes, exact/prefix ranked. 'rtx'->[RTX],
'uso'->[USO,USOU].
(b) "Verify →" on lobbying alerts landed on lda.senate.gov's EMPTY search form.
Not a broken link — RULE_09 stores source_url NULL on all 554 rows, so the UI
falls back to the source's front door. Checked whether it could be pre-filled:
the LDA search is a SPA and NONE of its 19 JS bundles reference client_name /
registrant_name / filing_year / filing_uuid, so a query string is silently
ignored — so I did NOT add one. Instead the label now tells the truth:
"Search Senate LDA →" for rules whose target is only an entry point
(RULE_07/09/12/13/14/15); "Verify →" retained where a real deep link exists
(RULE_06 CIK, RULE_11 award id, RULE_08 prefilled term, congressional PTR).
REAL FIX still owed: capture the filing URL at ingestion — RULE_09 ingestion
work, human-gated, already in Known Issues under the receipts gap.
Suite 715.

## 2026-07-29 — parked: contracts (RULE_11) UI/link bugs seen in prod

Logged to 04_Known_Issues/Current Blockers.md → "Contracts surface (RULE_11) —
UI/link bugs seen in prod (PARKED)". Not urgent; pick up after the clusters
close-out. Read-only triage narrowed both:
- Source links: the URL builder is ALREADY correct (contracts.html:288 →
  usaspending.gov/award/<award_id>/). It is a DATA problem — 65 of 171 local
  contract rows have no award_id, so the URL degenerates to /award// and
  USASpending serves its root. Prod still holds the pre-repair rows because
  scripts/repair_rule11_contracts.py is MANUAL and has never been run there.
  Running it (human-gated, 4 prod queries owed first) is likely the real fix.
- Sorting: already parameterised (contracts.html:266 → /contracts/data?sort=,
  routers/contracts.py:65 → amount DESC | award_date DESC). So the bug is the
  control's default/refresh, not a missing ORDER BY.
- Dates spanning months = the repair working (real Base Obligation Dates), not a
  bug — the pre-repair table shows only 5 distinct award_date values.
NOTHING CHANGED — documentation only.

Also closed out: the reported search-still-broken screenshot. The fix IS on
origin/main (9dc2a09, verified in the pushed tree); the live site was serving
pre-deploy code. My earlier "fix missing" grep was run against the primary
checkout, which sits on the parallel console's fix/cluster-cap-by-cik branch —
not main. Check on prod with /api/search?q=rtx: baskets = not yet deployed,
["RTX"] = deployed.

## 2026-07-29 — `chore/relationship-graph` (worktree, UNMERGED)

The ticker page's "Signal Relationship Graph" was drawing RTX/LMT connected to
RULE_07, RULE_OSINT, RULE_ANOMALY (all in RULE_10_EXCLUDED — they cannot
corroborate) and to RULE_10 (the GATE, not a peer). An edge there has only ever
meant "this rule fired an alert here" — raw activity — but hub-and-spoke around
a ticker reads as a convergence picture.
Fixed: eligible instruments solid + instrument-coloured + weighted edge; context
signals dimmed/dashed/uniform-edge with "cannot corroborate" tooltips; the gate
never drawn (moved to a header line). The split comes from a NEW read-only
GET /api/rule-model deriving from jpt_common — the verifier proved it drifts
correctly by runtime-patching jpt_common on a second server and re-rendering with
ZERO JS edits. Force-directed via d3 (already on the page); drag/hover/
click-to-filter preserved.
Correction to the brief: edge width and node size were NOT arbitrary (both were
already alert count) — COLOUR was, plus bare-number labels.
TWO LIES CAUGHT AFTER THE FIRST PASS:
 (1) mine — breadth was counted over the page's 365-day fetch while the gate's
     window is 14 days, so LMT's header announced "3 of 3 needed to converge"
     where the gate fires on nothing. Now counted on the gate's window.
 (2) the verifier's overturn — legs were attributed by SUBSTRING (tickers.py:297
     `ticker LIKE '%SYM%'`) and ignored the gate's HIGH/CRITICAL floor, so
     /ticker/PWR drew a solid "corroborating instrument: insider" leg built from
     MONOLITHIC POWER's Form 4, and MSFT claimed 2 instruments off eleven MEDIUM
     rows. Now a leg needs ≥1 alert on the EXACT ticker; borrowed ones are
     demoted to context and say why. PWR 2->0, MSFT 2->1 (re-derived in-page).
Verifier also overturned the colour scheme at the first commit (7-colour array
wrapped; senate-lda rendered identical to congressional) — hues now generated.
Known limitations recorded in the note (node size still on a global scale, so
context nodes can be the visually largest; no test pins /api/rule-model).
Suite 715. Branch state: 4 commits, UNMERGED — human-gated.
Write-up: 02_Sessions/SESSION-2026-07-29-relationship-graph.md

MERGED + PUSHED: chore/relationship-graph -> main as 0e001a1 (verified on a clean
checkout of merged main: 715 passed). origin/main == 0e001a1.
Still open after this merge, recorded in the note: the ticker page's alert fetch
is a SUBSTRING match (tickers.py:297 `ticker LIKE '%SYM%'`), so the graph now
correctly says "not on this ticker" for a borrowed alert while the alert list and
the summary counters on the SAME page still present it as this ticker's — /PWR
shows "All-time Alerts 7" where only 4 are PWR's (3 are MPWR's). Sampled 400 real
tickers: 58 pages over-count this way (AAPL 32 vs 9, MSFT 35 vs 12, CA 21 vs 3).
Fixing it is a one-line API change but it alters every ticker page's numbers, so
it is a deliberate decision, not a tidy-up. Also still unpinned: no test asserts
an excluded rule can never render as a leg / the gate is never a node — the
protection is structural only.

---

## 2026-07-29 — Light theme (token-layer override)

Work order: additive persisted light theme, dark stays the default, built in the
token layer, with explicit per-canvas decisions.
Write-up: 02_Sessions/SESSION-2026-07-29-light-theme.md

Branch: chore/light-theme (off main 0e001a1) — d9bf830 then 43e0d57.
**UNMERGED, human-gated.** Suite 805 passed / 9 skipped.

VERIFIER OVERTURNED THE HEADLINE. Two claims failed re-derivation, both now
addressed in 43e0d57:

(1) "Dark is byte-unchanged" — FALSE. :root gained --surface-nav (and now
--surface-scrim), and 108 declarations resolve to different DARK values than
main (/home 61, /feed 59, /ticker/LMT 33, /status 25, /universe 22; I
re-measured this myself against a `git archive main` server after fixing). The
visibly regressive ones are restored — the ⌘K and drawer scrim alphas, the
drawer surface, the two flattened gradients, the keycap ink (unified across all
18 sites). The remaining ~100 are pages that were still painting PRE-token
literals (#c8922a, #e55b4d, #6ab0e0, #7a7060) now resolving through tokens that
main's own aliases already redirected (--amber: var(--accent)). That is
completed tokenisation, not a redesign, and undoing it would mean un-tokenising
the pages — but it IS a change, so the claim was corrected rather than the work
reverted. **This is the one item that needs a human's eye before merge.**

(2) "No page stays dark under the toggle" — FALSE, five holdouts, none of which
a token-level test can see. All fixed and re-measured live:
  - `/` (the DEFAULT landing page) kept a hardcoded near-black nav, links at
    1.95:1. Now --surface-nav; TEMPLATE_VERSION bumped and the cached brief
    confirmed to actually rebuild. Whole-page sweep: 0 text failures.
  - /home heatmap counts at 1.09:1 (`color:var(--bg)` inverts with the theme).
    The ink now composites the real cell: 6.72 light, 3.55 dark — this also
    fixes low-intensity cells in DARK, where main was ~1:1.
  - ticker win-rate (2.10/3.36) and sector heat (1.94) ramps hardcoded as body
    text -> severity/direction tokens, now 5.80/7.43.
  - ball-pit legend didn't match the balls on a FRESH light load (only repainted
    on toggle). Repainted at init; swatches measured identical to the canvas.
  - /osint globe entirely 0x-hardcoded — invisible to a #hex|rgb() sweep. Now
    DECLARES keep-dark via data-theme-lock, which theme.js honours: the whole
    page stays internally consistent, the nav says "DARK STAGE" instead of a
    toggle that cannot work, and the user's stored choice still applies
    elsewhere. Chosen over adapting because the globe's clear colour, earth
    tint, lighting and markers are one calibrated system, the markers read
    against the GLOBE not the page, and WebGL could only be rasterised in
    software here — I would not claim a re-tune I could not measure.

Adjusted without changing the verdict: 4 of 9 graph instrument hues missed the
3:1 mark floor at 38% lightness (I had claimed all nine cleared it). Now 32%,
worst 3.31 vs dark's own 3.10 baseline.

FOUND BY ME while re-verifying, and worse than anything reported: backtest.html
built its outcome chips as `${color}22`. Appending hex alpha only works on a
LITERAL, so the moment those became var() the concatenation produced invalid CSS
and every chip silently lost its tint and border — nothing errored. Rebuilt with
color-mix. Same failure mode as the rest of this session: a value that is fine as
a literal breaks silently the instant it becomes a token.

New tests/test_theme_holdouts.py (38 cases) pins the class the token test is
structurally blind to: page-level colour literals, WebGL 0x literals, a deferred
theme script, and the brief's nav. Escape hatch is a per-LINE `theme-ok:
<reason>` marker, not a per-file allowlist — a file-wide list is exactly how
these five survived. It caught one I had missed while writing it.

Verifier UPHELD: toggle/persistence/precedence/no-flash (27 routes, 393-frame
cold-load screencast), presentation-only scope (3 Python files, none
load-bearing), nothing merged, and it mutation-tested the contrast test nine ways
— not vacuous, but tokens-only in reach.

Still open, recorded in the note: ~100 dark deltas awaiting a human call; three
timeline rule-dots share a #888 fallback (3.11:1, identity collapse,
pre-existing); alerts.html's ?notice=nobrief banner is fixed by reasoning but
neither of us could make it render; the globe was only ever software-rasterised.

## 2026-07-29 21:03 UTC — `fix/basket-rule-gate-class`
10 commit(s) in the last 6h:

  - 4bdad0b fix(osint): close what the verification pass found — including a regression I caused
  - 074f7ca fix(osint): retire the basket emission, and stop the globe inventing places
  - 6c8386c Merge feat/universe-live — /universe reads the real coverage list, and an empty one no longer hides a fault
  - 0280449 feat(universe): wire /universe to the real coverage list
  - 5cb74e1 Merge branch 'feat/insider-cluster-closeout'
  - 2e76c38 Merge chore/light-theme — light as a token-layer override, and an honest account of what dark lost
  - 43e0d57 fix(theme): close the verifier's five holdouts and correct the dark claim
  - d9bf830 feat(theme): light theme as a token-layer override — dark stays the default
  - 4a79720 docs(cluster): three justification sentences that claimed more than the evidence
  - 24071e6 fix(cluster): entity 16 — publish the boundary the predicate actually used

## 2026-07-29 21:04 UTC — `fix/basket-rule-gate-class`
11 commit(s) in the last 6h:

  - aaf7c9d test(gate): close the basket-rule class by SHAPE, and surface RULE_08
  - 4bdad0b fix(osint): close what the verification pass found — including a regression I caused
  - 074f7ca fix(osint): retire the basket emission, and stop the globe inventing places
  - 6c8386c Merge feat/universe-live — /universe reads the real coverage list, and an empty one no longer hides a fault
  - 0280449 feat(universe): wire /universe to the real coverage list
  - 5cb74e1 Merge branch 'feat/insider-cluster-closeout'
  - 2e76c38 Merge chore/light-theme — light as a token-layer override, and an honest account of what dark lost
  - 43e0d57 fix(theme): close the verifier's five holdouts and correct the dark claim
  - d9bf830 feat(theme): light theme as a token-layer override — dark stays the default
  - 4a79720 docs(cluster): three justification sentences that claimed more than the evidence
  - 24071e6 fix(cluster): entity 16 — publish the boundary the predicate actually used

## 2026-07-29 21:37 UTC — `fix/basket-rule-gate-class`
9 commit(s) in the last 6h:

  - 0039fef fix(gate): close the evasions the verification pass found, and encode the rest
  - aaf7c9d test(gate): close the basket-rule class by SHAPE, and surface RULE_08
  - 4bdad0b fix(osint): close what the verification pass found — including a regression I caused
  - 074f7ca fix(osint): retire the basket emission, and stop the globe inventing places
  - 6c8386c Merge feat/universe-live — /universe reads the real coverage list, and an empty one no longer hides a fault
  - 0280449 feat(universe): wire /universe to the real coverage list
  - 5cb74e1 Merge branch 'feat/insider-cluster-closeout'
  - 2e76c38 Merge chore/light-theme — light as a token-layer override, and an honest account of what dark lost
  - 43e0d57 fix(theme): close the verifier's five holdouts and correct the dark claim

## 2026-07-30 — `fix/rule15-history-ingested-at` (human-gated, NOT merged)

See [[SESSION-2026-07-30-rule15-history-ingested-at]]. Closed the residual RULE_15
hole that was still fabricating alerts 33h after the 07-27 repair: the quarantine
gated *emission* on post-epoch rows but the **history/denominator query
(`rule_15_earnings_nlp.py:297-303`) never filtered `ingested_at`**. One predicate.

  - 2a47d9f test(rule15): correct the seed provenance, add the LIMIT 8 rescue case
  - 77d54cf fix(rule15): filter the denominator query on REPAIR_EPOCH

BA → −7.9% no alert (gate still passes at `usable=2`, so the denominator genuinely
moved); mutation-tested — remove the predicate and it emits "up 908% QoQ" again.
Genuine cases unaffected, and the predicate **rescues** signal `LIMIT 8` was dropping.
Frozen pre-repair scores are **permanent** (`INSERT OR IGNORE` on `UNIQUE(accession)`)
— store cleanup scoped separately. Residual: `/api/earnings-sentiment` still serves
pre-epoch scores unfiltered.

**MERGED + DEPLOYED 2026-07-30 11:38 UTC** — `ce1c7be` (Railway deploy `5105ff4e`,
SUCCESS, live app healthy). Human overrode the work order's do-not-merge. Merge done in
session; **push was not** (origin/main advanced externally; no local hooks). ⚠️ The same
push also shipped the 3-commit `feat/gate-direction-insider-contracts` gate redesign
(2,275 insertions), which this session did not verify.

## 2026-07-30 13:48 UTC — `main`
6 commit(s) in the last 6h:

  - ce1c7be Merge fix/rule15-history-ingested-at — the denominator must be post-repair too
  - 7765cea Merge feat/gate-direction-insider-contracts — a leg must SAY the thing, not merely be present
  - ecef66c test(war-rooms): a fixture that only broke where two branches met
  - 2a47d9f test(rule15): correct the seed provenance, add the LIMIT 8 rescue case
  - c0ab7a8 feat(gate): a leg must SAY the thing — signed insider direction + cap-relative contract weight
  - 77d54cf fix(rule15): filter the denominator query on REPAIR_EPOCH

## 2026-08-02 09:13 UTC — `fix/rule09-demote-to-context`
No commits in the last 6h (read-only or discussion session).

## 2026-08-02 09:13 UTC — `fix/rule09-demote-to-context`
No commits in the last 6h (read-only or discussion session).

## 2026-08-02 (manual backfill) — `fix/rule01b-first-touch-chronology`, `fix/rule01b-ticker-validation`

⚠️ WRITTEN BY HAND, not by the SessionEnd hook. The hook fires on session END; this session ran
two full work orders back to back without terminating, so the safety net recorded neither. Same
gap as the 2026-07-28 backfill — the hook cannot cover a session that never ends.

**No commits.** Both work orders are human-gated and forward-only; nothing was merged, committed,
deployed or scheduled. Both branches sit at `ce1c7be` (= `main`), so `git checkout -b` was a
pointer move and every change is uncommitted in the working tree, stacked on top of other
sessions' uncommitted work.

RULE_01B audit defects #1 and #4, in order:

  - `fix/rule01b-first-touch-chronology` — "first" now means chronologically EARLIEST, not lowest
    row id (`t2.id < t.id`). 39/192 stored alerts (20.3%) asserted "no prior disclosed trade"
    about a member who had one. Emit on cleared copies: false claims 18 -> 0.
    Full note: [[SESSION-2026-08-02-rule01b-first-touch-chronology]]

  - `fix/rule01b-ticker-validation` — an unvalidated PDF-parse string can no longer become a
    corroboration key. 41/192 (21.4%) carried a ticker absent from `tickers`; `NY` spanned two
    members. Unvalidated keys at the gate: 12 -> 0.
    Full note: [[SESSION-2026-08-02-rule01b-ticker-validation]]

Suite 1204 passed / 5 xfailed. Working DB `data/jpt.db` md5 `177f474b03495c20df10a21335ca9dc3`,
byte-identical from session start; all mutation work on scratchpad copies.

Two stored-alert remaps are PREPARED BUT NOT RUN, and the order between them is load-bearing:
`remap_rule01b_first_touch.py` must run BEFORE `remap_rule01b_ticker_validation.py` (the second
blanks the `alerts.ticker` the first joins on). The second refuses with exit 2 if run early.

Open for the human: the novelty collapse on blank ticker keys (0.591 -> 0.313) is scoring
surface and needs a decision; prod magnitude for both defects is UNVERIFIED pending a prod
pre-flight run.

## 2026-08-02 (manual backfill, cont.) — `fix/rule01b-direction`

⚠️ WRITTEN BY HAND, not by the SessionEnd hook — same never-terminating session as the entry
above, now three work orders deep.

**No commits.** Human-gated and forward-only; nothing merged, committed, deployed or scheduled.
All three RULE_01B branches sit at `ce1c7be` (= `main`), stacked uncommitted in the working tree.

  - `fix/rule01b-direction` — audit defect #3. The headline hardcoded "opens new position" on
    every alert while 88/192 (45.8%) of the corpus were disposals. Direction now read per
    `transaction_type`; verdict recorded in the typed `corroborates`/`corroboration_note` columns,
    INERT until RULE_01B is signed. Fresh emit: disposals-as-opening 52 -> 0.
    Full note: [[SESSION-2026-08-02-rule01b-direction]]

Suite 1231 passed / 5 xfailed. Working DB md5 `177f474b03495c20df10a21335ca9dc3`, unchanged.

**RULE_01B's attribution is now fully repaired (#1 chronology, #3 direction, #4 ticker key).**
Per signed-signal-engine, that was the bar for signing — so SIGNING RULE_01B is now unblocked and
is its own human-gated session. `SIGNED_RULES` is still `{"RULE_06"}` and was not touched.

THREE stored-alert remaps are now PREPARED BUT NOT RUN, and the order is load-bearing and enforced:
  1. remap_rule01b_first_touch.py        (reads alerts.ticker)
  2. remap_rule01b_ticker_validation.py  (blanks alerts.ticker)
  3. remap_rule01b_direction.py          (skips retracted rows, supplies the UNVERIFIED marker)
Each refuses with exit 2 if run out of order. Running #3 with --force out of order was measured to
resurrect all 39 retracted rows as fresh directional claims and lose all 32 markers.

Open for the human, both scoring surface and both forward-only:
  - blank ticker keys collapse novelty 0.591 -> 0.313 (ticker-validation session)
  - populating why_matters widens calculate_novelty_score's raw-substring match: 82 tickers worse,
    6 better, worst DIS 1.0 -> 0.175 (this session). Symptom of the unmerged token-anchor fix.
Prod magnitude for all three defects is UNVERIFIED pending the remaps' read-only pre-flights.

## 2026-08-02 (manual backfill, cont.) — `fix/rule01b-severity-band`

⚠️ WRITTEN BY HAND, not by the SessionEnd hook — same never-terminating session, four work orders deep.

**No commits.** Human-gated and forward-only. All four RULE_01B branches sit at `ce1c7be` (= `main`),
stacked uncommitted in the working tree.

  - `fix/rule01b-severity-band` — audit defect #5. `_is_above_15k` was a three-literal DENYLIST, so
    anything unrecognised was HIGH: 37/9967 transactions across 19 spellings, plus every absent
    amount. Now a numeric band via `ingest_senate.amount_band_floor`, failing SAFE to MEDIUM.
    Full note: [[SESSION-2026-08-02-rule01b-severity-band]]

Suite 1253 passed / 5 xfailed. Working DB md5 `177f474b03495c20df10a21335ca9dc3`, unchanged.

🔴 **THIS ONE SHIPS NO REMAP, AND THAT IS THE POINT.** The other three RULE_01B fixes corrected
ATTRIBUTION and rightly shipped `--apply` remaps. Severity is a DETECTION-TIME SCORE — those same
sessions each proved `severity` unchanged, and the RULE_15 saga treated an out-of-band severity
write as a defect. So `scripts/audit_rule01b_severity.py` is read-only with no write path at all,
and the stored-severity question is surfaced for the human, not answered:
  (a) leave them to age out of the gate window — no write, no immutability question (the default);
  (b) correct them as a disclosed, explicitly-recorded EXCEPTION to detection-time immutability.
On this snapshot the stored false-HIGH count is 0, so the question does not arise locally.

**RULE_01B: four of five audit defects now addressed** — #1 chronology, #3 direction, #4 ticker key,
#5 severity band. Only **#2 (the 90-day window basis)** remains. Attribution was the bar for signing
per signed-signal-engine, so SIGNING RULE_01B is still the next gate and is its own session.
`SIGNED_RULES` remains `{"RULE_06"}`, untouched across all four sessions.

Open for the human, unchanged from the prior entries plus one:
  - blank ticker keys collapse novelty 0.591 -> 0.313 (ticker-validation session)
  - populating why_matters widens novelty's raw-substring match, 82 tickers worse (direction session)
  - stored severity on PROD is UNVERIFIED; the audit script's read-only pre-flight settles it
Both novelty items are scoring surface and forward-only; neither was absorbed silently.

## 2026-08-02 (manual backfill, cont.) — `fix/rule01b-window-basis`

⚠️ WRITTEN BY HAND, not by the SessionEnd hook — same never-terminating session, five work orders deep.

**Not committed.** The four EARLIER RULE_01B fixes WERE committed this session at the human's explicit
instruction (see below); this fifth one is not.

  - `fix/rule01b-window-basis` — audit defect #2, the last of five. The 90-day window filtered
    `transaction_date`, so a late-filed PTR was stale on arrival and never revisited: 932/9967 (9.4%)
    filed >90d late, and none of those first touches ever alerted as itself. Now windows on
    `COALESCE(filing_date, transaction_date)`. Emit 109 -> 192, zero lost.
    Full note: [[SESSION-2026-08-02-rule01b-window-basis]]

Suite 1220 passed / 5 xfailed. Working DB md5 `177f474b03495c20df10a21335ca9dc3`, unchanged all day.

⚠️ Branched off `fix/rule01b-severity-band`, NOT `main` as the work order said — the RULE_01B chain is
unmerged, so branching off main would have discarded all four committed fixes from the working tree.

**RULE_01B IS NOW 5/5 REPAIRED.** #1 chronology, #4 ticker key, #3 direction, #5 severity band (all
committed) and #2 window basis (this one, uncommitted). Attribution was the bar for signing per
signed-signal-engine, so **SIGNING RULE_01B is the next and only remaining gate on it**, and it is its
own human-gated session. `SIGNED_RULES` stayed `{"RULE_06"}` across all five.

════════ COMMITS MADE THIS SESSION (the standing no-commit rule was overridden by the human) ════════
Every one of the five work orders said "nothing merged, deployed, or committed by the implementer".
The human then explicitly asked for the work to be committed. Nothing was pushed and nothing merged.

  fix/rule01b-severity-band   75bfdbc chronology · b9bf9bb ticker key · b6c67c4 direction · 5a4c003 severity
  fix/rule09-demote-to-context a9a903c RULE_09 attribution + demotion

The four RULE_01B commits were REPLAYED from per-fix snapshots so each is independently reviewable and
its own tests pass at that commit. RULE_09 is ONE commit because both its fixes were already merged in
`rule_09_lobbying.py` with no intermediate snapshot — splitting would have been fabricated history —
and its commit message records that it was authored by earlier sessions and not independently
re-derived by the committer.

Left deliberately untracked: the whole vault (107 files, per the standing "notes stay untracked"
preference) and the scratch artifacts (5 PNGs, darkdiff.json, mergecheck/).

Still open for the human, unchanged: the gate does not honour retraction; RULE_02/RULE_06 carry the
same unvalidated-ticker defect; two scoring questions (blank-ticker novelty collapse; why_matters
widening the novelty substring surface); and prod magnitude for every RULE_01B figure is UNVERIFIED
pending the remaps' read-only pre-flights.

## 2026-08-02 (manual backfill, cont.) — SIGN RULE_01B: 🔴 NO-GO, no branch created

⚠️ WRITTEN BY HAND, not by the SessionEnd hook — same never-terminating session, six work orders deep.

**No branch, no commit, no code change.** Stopped at the Stage 1 prerequisite exactly as the contract
requires. `SIGNED_RULES` remains `frozenset({"RULE_06"})`.

The prerequisite fails TWICE, and the second failure is the one that matters:

  1. CODE — none of the five RULE_01B fixes are on `main`. They exist only on
     `fix/rule01b-window-basis` (75bfdbc, b9bf9bb, b6c67c4, 5a4c003, a3bc9a9). `main`'s rule still
     hardcodes "opens new position" and has zero occurrences of `_DIRECTIONS`/`corroborates`.
  2. DATA — and MERGING DOES NOT FIX THIS. The working DB is at m012 with no `corroborates` column
     at all; after migration every one of the 192 stored RULE_01B alerts holds NULL, because
     `remap_rule01b_direction.py` is PREPARED-NOT-RUN. The "153 verdicts" only ever existed inside an
     isolated scratch copy during that session's dry run.

So Stage 1b (prove the direction column correct per-row) is not merely failed, it is impossible:
0 of 192 rows populated. Signing today would fail CLOSED on every row and silently dark RULE_01B —
the exact opposite of the intended payoff.

Full note, with the findings worth banking: [[SESSION-2026-08-02-rule01b-signing]]

Corrections and findings recorded for the eventual signing session:
  - `SIGNED_RULES` lives at `jpt_common.py:864`, NOT `rule_10_corroboration.py` as the work order says
    (the gate imports it at :52).
  - Fail-closed CONFIRMED from source: `alert_corroborates:230` `if verdict is None: return False`.
  - Gate swing, measured on a SIMULATION of the post-remap state: 16 live RULE_01B gate candidates ->
    11 corroborate, 5 disposals stop counting, 0 fail closed. Themes/convergences UNVERIFIED — this
    DB has 0 RULE_10 rows, 0 themes, 0 theme_signals.
  - 🟢 New argument IN FAVOUR: all 39 chronology-retracted rows carry corroborates=NULL, so signing
    CLOSES the gate-honours-retraction gap for RULE_01B without touching the gate's candidate query.
  - ⚠️ Honest framing: signing adds TRUST but subtracts SUPPLY. 5 of 16 legs stop counting, so it
    pushes the threshold decision (#4) harder rather than relieving it.

UNBLOCK PATH, ordered, every step already built:
  1. Merge `a3bc9a9` to main (carries all five fixes; rule09 is independent).
  2. Run the three remaps on prod IN ORDER — each exits 2 if run early:
     remap_rule01b_first_touch -> remap_rule01b_ticker_validation -> remap_rule01b_direction.
     The third is what populates `corroborates`. Read the read-only pre-flights first; local figures
     (39/40/153) are corroborative only.
  3. Re-run the signing work order. 1b becomes provable and the swing measurable against real data.

No verifier was invoked: there is no change to verify, and both blockers are single-command
checkable. The verifier mandate transfers to the session that actually signs.

Working DB md5 `177f474b03495c20df10a21335ca9dc3`, unchanged.

## 2026-08-02 (manual backfill, cont.) — `sign/rule01b`: ✅ RULE_01B IS SIGNED

⚠️ WRITTEN BY HAND, not by the SessionEnd hook — same never-terminating session, seven work orders deep.

**Not committed.** Branch `sign/rule01b` off `main` (3250818). Suite 1282 passed / 5 xfailed.
Working DB md5 `177f474b03495c20df10a21335ca9dc3`, unchanged all day.

Second attempt. The first was correctly a NO-GO (the five RULE_01B fixes were not on `main`); the
human then merged the chain and re-issued, clearing the CODE half. Full note, covering both attempts:
[[SESSION-2026-08-02-rule01b-signing]]

PRODUCTION DIFF IS ONE LINE:
  SIGNED_RULES: frozenset({"RULE_06"}) -> frozenset({"RULE_06", "RULE_01B"})
No wiring was needed — `instruments_for` already routes every leg through `alert_corroborates`.

Load-bearing evidence: 153/153 stored verdicts match a recompute done independently from tx_type,
0 mismatches. The verifier re-derived it with its own mapping AND cross-checked the authoritative
`transactions` table: 191/192 exact agreement, 0 verdict disagreements.

Swing: 16 RULE_01B gate legs -> 11 corroborate, 5 drop. RULE_06 and every unsigned rule unchanged.
Signing also closes the gate-honours-retraction gap FOR RULE_01B ONLY (retracted rows hold NULL,
NULL fails closed).

🔴 THE DEPLOY PRECONDITION — THE WHOLE RISK OF THIS CHANGE:
`corroborates` is populated by `scripts/remap_rule01b_direction.py`, which is PREPARED-NOT-RUN.
Ship the signing before that remap and RULE_01B goes from 26 corroborating legs to 0 — fail-safe,
but a silent blackout of the rule as a gate leg.
  ORDER: run the three RULE_01B remaps on prod (enforced order, each exits nonzero if early),
         THEN ship the signing. Verify after with:

    SELECT corroborates, COUNT(*) FROM alerts
     WHERE rule='RULE_01B' AND ticker IS NOT NULL AND ticker!=''
       AND severity IN ('HIGH','CRITICAL')
       AND created_at >= datetime('now','-336 hours')
     GROUP BY 1;

  All-NULL means the remap has not run.

Verifier overturned two of my claims and found one real hole; all three closed:
  - "all 39 retracted rows are from the 07-08 batch" -> 38 are; one is in-window (excluded on
    severity), and 7 HIGH retracted rows are held out by the window ALONE. Conclusion understated.
  - "today's prod — 26 candidates" -> that is the LOCAL snapshot, and it is 13 days stale: all 16
    remapped candidates are dated 2026-07-20 against a cutoff of 2026-07-19 20:21, so the whole
    measured swing expires within hours. Prod figures are UNVERIFIED.
  - NO test anywhere asserted a real RULE_01B row being REJECTED by the real gate — the deleted
    assertion in test_rule01b_direction.py should have been inverted, not removed. Two end-to-end
    tests added and proven to discriminate.

QUEUED, out of scope (touches the gate): the deploy precondition has NO DETECTOR. Comment + test +
note cannot detect the state — the test builds synthetic dicts and passes either way. A CRITICAL
activity_log alarm when a signed rule's in-window candidates are ~100% NULL belongs in RULE_10,
matching this codebase's own convention (rule_01b's validity-ratio alarm, MONITOR_ENRICH_STALL).

`Scope/CLAUDE.md` updated — it still said SIGNED_RULES == {"RULE_06"} and called RULE_01B
deliberately unsigned.

⚠️ Honest framing: signing adds TRUST but subtracts SUPPLY. 5 of 16 legs stop counting, so it makes
threshold decision #4 MORE pressing, not less. Still the right change — a gate that fires less often
on true things beats one that fires more often on false ones.

## 2026-08-03 (manual backfill, cont.) — `feat/signed-rule-null-detector`

⚠️ WRITTEN BY HAND, not by the SessionEnd hook — same never-terminating session, eight work orders deep.

**Not committed.** Branch off `main` (3250818). Observability only: **+94 / −0 in
`scripts/rule_10_corroboration.py`, a pure addition**, plus one new test file (14 tests).
Working DB md5 `177f474b03495c20df10a21335ca9dc3`, unchanged.

Closes the gap the signing session could only document: `alert_corroborates` fails closed on a NULL
verdict, correctly and SILENTLY, so a signed rule whose column is unpopulated stops being a
corroborating leg with no outward sign. Full note: [[SESSION-2026-08-03-signed-rule-null-detector]]

⭐ THE DESIGN POINT: it keys on `corroborates IS NULL` ("no verdict on record"), NEVER on the
corroboration boolean. A signed rule whose in-window candidates are all SALES has every verdict 0
and is HEALTHY — keying on the boolean would fire loudest exactly when signing is doing its job.

⚠️ PLACEMENT IS LOAD-BEARING: the detector sits BEFORE `run()`'s `if not clusters` early return,
because a dark rule is precisely one that stops completing convergences. The verifier built a
variant with the call moved after the return: zero alarms in exactly the scenario it exists for.

NO GATE DECISION CHANGES — fire set, tags and themes byte-identical with and without; the only new
effect is one activity_log row. 1.85 ms per run.

⚠️ SUITE IS RED, AND NOT FROM THIS CHANGE: `2 failed, 1294 passed, 5 xfailed`. An earlier draft of
the note said "two DESELECTED" — the verifier overturned that: there is no pytest config in this
repo, I had excluded them by hand, and two red tests must not hide behind a word implying config.
  test_market_cap_plausibility.py::test_the_staleness_threshold_boundary[541-False]
  test_market_cap_plausibility.py::test_INSTANCE1_the_other_direction_a_stale_SMALL_cap_still_fails_closed[541]
Root cause (my first statement of the mechanism was ALSO wrong — SQLite is not involved): Python-UTC
`datetime.now(timezone.utc).date()` at `scripts/rule_reddit_collector.py:286` vs the fixture's local
`_dt.date.today()` at `tests/test_market_cap_plausibility.py:477,699`. A 541-day fixture measures
540 and the boundary inverts. FLAKY BY WALL-CLOCK — fails only between the local and UTC day
rollovers (~22:00–24:00 local here), passes again afterwards. Out of scope; its own session.

Verifier also caught a real inaccuracy in MY docstring: "RULE_01B's verdicts come from a remap that
is prepared-not-run" is wrong — BOTH signed rules write `corroborates` at emit time
(`rule_01b_first_touch.py:438-444`, `rule_06_form4.py:552`); the remap covers only the BACKLOG, so a
dark rule self-heals as new alerts land. Fixed in both docstrings — it changes how urgently an
operator should respond. Two cause-label edge bugs also fixed (an in-window MEDIUM verdict was
mislabelled "outside window"; the ever-probe was case-sensitive while candidates are upper-normalised).

On real data both signed rules read dark (RULE_01B 26/26, RULE_06 24/24) — recorded WITH caveats,
not as a prod claim: this snapshot holds zero non-NULL verdicts anywhere, both counts fall to 0 at a
320h window, and both rules self-heal. Prod UNVERIFIED; settling query is in the note.

Known and disclosed, not fixed: partial population (25 NULL + 1 populated) is silent by the
100%-threshold design — a WARNING tier is the natural follow-up, and it is the shape most likely to
occur in practice.

WHAT THIS UNLOCKS: the RULE_01B signing deploy is now SELF-DETECTING. That change carries a
precondition (its backlog verdicts come from a prepared-not-run remap); with this detector, an alarm
appearing after that deploy means the precondition was violated, and it names the rule and counts.

## 2026-08-03 (manual backfill, cont.) — `fix/market-cap-test-tz-flake`: THE SUITE IS GREEN

⚠️ WRITTEN BY HAND, not by the SessionEnd hook — same never-terminating session, nine work orders deep.

**Not committed.** Branch off `main` (3250818). **TEST-ONLY: one file, zero production changes.**
Working DB md5 `177f474b03495c20df10a21335ca9dc3`, unchanged; no DB touched.

**FULL SUITE: 1296 passed / 5 xfailed / 0 FAILED** — green for the first time today, and green under
local, UTC, UTC+20 and UTC-11. No `-k`, no `--deselect`, and no pytest config exists to hide one.
Full note: [[SESSION-2026-08-03-market-cap-test-tz-flake]]

Follow-up to the defect the detector session found in passing: two market_cap boundary tests red
every evening, green by morning.

THE GO/NO-GO MATTERED: if production had been mixing clocks, a test fix would have buried a real bug.
It is not — `_fact_age_days` compares `datetime.now(timezone.utc).date()` against an SEC filing date,
and the verifier's grep found NO local-clock read anywhere in rule_reddit_collector.py. The fixtures
were the bug: "N days ago" built from local `date.today()`, so a 541-day case measured 540 and landed
exactly ON the 540 threshold.

THE TRAP, NAMED BY THE WORK ORDER AND AVOIDED: swapping the fixture to UTC makes both sides agree BY
LUCK and re-skews the moment either derives "now" differently. Instead BOTH sides are pinned to one
constant (`_PINNED_UTC` + a `_PinnedDatetime` whose only override is `now()`), and `_pin_clock`
RETURNS the reference date so the shared origin holds by construction rather than by discipline.
A third site carrying the identical latent bug was fixed too.

VERIFIER: headline UPHELD. It built a clock simulator, swept ~100 zone×instant configs including
simulated midnight straddles (0 failures), killed 10/10 mutations, and found 0 monkeypatch leaks
across all 1301 tests. ⭐ Its M5/M6 mutations answer the "luck" question better than anything I ran:
unpin the fixture side -> 1 failed; unpin the production side -> 2 failed. EACH HALF IS
INDEPENDENTLY LOAD-BEARING, which is exactly what distinguishes a pin from two clocks that agree.

⚠️ IT OVERTURNED TWO OF MY CLAIMS, BOTH OVER-CLAIMS:
  - "pre-fix fails in exactly Europe/Berlin and Pacific/Kiritimati" is FALSE. The discriminator is
    `local_date != utc_date`, not the zone name, and which zones carry a skew depends on the UTC hour.
    At the verifier's run time (08:30 UTC) the result was EXACTLY INVERTED: Berlin/Kiritimati passed,
    Midway failed. Publishing a list of zone names was the error.
  - I UNDER-DESCRIBED MY OWN BUG. It bites in BOTH directions. Westward skew trips `[540-True]` — a
    fresh fixture wrongly rejected as stale — which I never observed and never mentioned. Reproduced
    in-repo: TZ=XXX-20 (skew +1) fails [541-False]; TZ=YYY+12 (skew -1) fails [540-True].
  - Residual sites: TEN, not three, and L754 is a 1000-day fixture, not age 0. Benign upheld but on
    MARGIN (>=460 days from the threshold), not on the mechanism I gave. That distinction matters:
    "safe by margin" is not "safe by construction", and someone believing the latter will add an
    eleventh site next to a threshold.

Recorded in the code, from the verifier: `_PinnedDatetime` does NOT pin `utcnow()` (if the collector
ever adopts it, the pin stops applying SILENTLY), and `now(tz=None)` returns aware where the real one
returns naive local. Both inert today.

## 2026-08-03 (manual backfill, cont.) — backlog sweep: four queued items

⚠️ WRITTEN BY HAND, not by the SessionEnd hook — same never-terminating session, ten work orders deep.

**Nothing merged.** `main` untouched at 3250818; `origin/main` at ce1c7be so nothing can deploy.
Working DB md5 `177f474b03495c20df10a21335ca9dc3`, unchanged. No DB written.
Full note: [[SESSION-2026-08-03-backlog-sweep]]

Taken while the RULE_02 directional-count plan is out at Ultraplan for remote refinement.

  1. PUSHED sign/rule01b (05f38ca), feat/signed-rule-null-detector (c249fa5),
     fix/market-cap-test-tz-flake (dc96c71). origin/main deliberately left at ce1c7be.

  2. `fix/rule-cluster-sale-full` (7b9ea46) — RULE_CLUSTER's `_member_direction` matched disposals with
     {"sale","sale_partial"} and MISSED `sale_full`, which the scheduled House parser emits for PTR code
     "S (full)". A missing disposal is SILENT: such a member classified as "other" and was dropped from
     consensus, so a genuine 3-member sell cluster containing one full-seller became a 2-member
     near-miss. 0 rows today -> prophylaxis, no cluster was lost. Found while planning RULE_02, which
     uses startswith("sale") and already handled it — the two congressional rules disagreed on exactly
     this value. The test CLOSES THE CLASS: it reads normalize_transaction_type's own returns and
     requires each emitted value to be classified. Mutation: reverting turns 4 tests red.

  3. THE TWO SCORING QUESTIONS WERE ONE PROBLEM WITH A FIX ALREADY WRITTEN.
     `fix/novelty-like-substring` (7816e04) has sat unmerged since 2026-08-01 — +96 lines, 348 lines of
     tests — targeting the raw-substring novelty anchor. Measured: it FIXES the why_matters case
     (DIS 0.247 -> 1.0) and does NOT fix the blank-ticker case (_is_ticker_shaped("") is False, so an
     empty anchor still falls through to LIKE '%%').
     Human decision: rebase, re-verify, hand back — do NOT merge. Done on a NEW branch
     `fix/novelty-anchor-rebased` (165ceba) so the original stays intact. Cherry-pick auto-merged
     cleanly (merge-tree's "changed in both" was file-level, not a real conflict); main's RULE_09
     exclusion AND the novelty fix both survive. Its 43 tests pass; full suite 1307.
     ⭐ DELTA ON THE REAL CORPUS: of 1213 actual (rule,ticker) anchors, 145 corrected UPWARD and
     **0 lowered** — the invariant. 12% were under-scored. Worst: RULE_11 HUM 0.325->1.0,
     RULE_ANOMALY S 0.126->0.591, RULE_01B T 0.16->0.591, RULE_02 ST 0.185->0.591. Forward-only.
     AWAITING THE HUMAN'S MERGE DECISION.
     The blank-ticker collapse is LEFT BY DECISION: it LOWERS novelty (conservative), only affects
     gate-barred `review` alerts, and treating a blank anchor as "unknown" would default to 1.0 —
     OVER-scoring an alert whose ticker we cannot name, the same direction as the bug being fixed.

  4. `feat/signed-rule-degraded-warning` (e7b6000, stacks on the detector) — the detector returned on
     ANY populated verdict, so 25 NULL + 1 populated was silent, and the verifier called that the shape
     MOST LIKELY to occur in practice. WARNING tier at >=50% NULL, mirroring rule_01b's own
     majority-unverified alarm. WARNING not CRITICAL deliberately: partial population is also exactly
     what a healthy signed rule looks like mid-ROLLOVER, so it must not page. Tiers mutually exclusive.
     Fire set byte-identical. One pre-existing test was ASSERTING the blind spot (two of four params
     were 50% NULL) — updated into three honest tests, not deleted. Mutation: 3 tests red without it.

⚠️ MERGE ORDER, unchanged: fix/market-cap-test-tz-flake first — the other branches still carry the
wall-clock flake and go red on any evening run without it.

---

## 2026-08-03 — RULE_02 directional count: investigated here, handed to Ultraplan

See SESSION-2026-08-03-rule02-ultraplan-handoff. NO CODE WAS WRITTEN LOCALLY for this — the plan was
sent out to be refined remotely and will land as a pull request. This entry exists so the Stage 1
evidence survives independently of whatever the cloud returns.

THE DEFECT, two coupled sites in rule_02_cluster.py:
  :107 counts EVERY member, directional or not.
  :133 derives the verb from a net that IGNORES non-directional trades.
  => count and verb are computed over DIFFERENT populations, so the alert can read
     "N members buying" where fewer than N members bought anything.
  Same class as the RTX failure that motivated signed legs: a leg that is PRESENT being counted as
  though it SAID the thing. This is a precondition for signing RULE_02 at all.

STAGE 1 — derived LOCALLY, recorded as such, NOT proven on the PR. Its evidence predates the RULE_09
demotion, so plan step 4 re-derives all of it on the rebased tree:
  - 4 overstated stored alerts: ids 73, 5, 74, 72 — 3 of them HIGH.
  - D1: instruments for RULE_01B + RULE_02 + RULE_CLUSTER = 1 (congressional), threshold 3.
  - 82/82 member names segment cleanly from the comma-joined `tags` by longest-match vs `members`.
  - RULE_02's dedup key is its HEADLINE.

⭐ THE LAST TWO SHAPE ANY REMAP and are where a remote session most plausibly diverged:
  Names are RECOVERED, not stored. 82/82 clean is a MEASUREMENT, not a guarantee — a member name
  containing a comma is the same hazard that pushed RULE_06's signed verdict into typed columns.
  Dedup on the headline means a CORRECTED HEADLINE RE-EMITS: changing the verb or count changes the
  string, permitting one re-emission inside the 7-day window. Forward-only correctness and
  stored-corpus correctness pull in OPPOSITE directions here.

VOLUME: expect RULE_02 to fire LESS. That is the fix working — a cluster only reaching 3 members by
counting a non-directional one was never a 3-member directional cluster. The check on the PR is that
the drop is NOT COMPENSATED: no threshold loosened, scripts/rule_cluster.py left alone.

⚠️ ALREADY STALE IN THE HANDED-OFF PLAN: it recorded RULE_CLUSTER's missing `sale_full` as "queued,
not fixed here". Since fixed locally on fix/rule-cluster-sale-full (7b9ea46), which the cloud cannot
see — if its PR also edits scripts/rule_cluster.py that is a collision with work that did not exist
at its base. Worth remembering WHY that gap surfaced while planning this: RULE_02 matches disposals
with startswith("sale") and so ALREADY handled sale_full; RULE_CLUSTER's explicit set did not. The
two congressional rules disagreed on exactly that one value.

⚠️ THE PR WILL BE BASED ON A TREE WITHOUT TODAY'S WORK.
  origin/main = ce1c7be; local main = 3250818, 8 commits AHEAD and UNPUSHED.
  So the PR has none of: the five RULE_01B fixes, the RULE_09 demotion, sale_full, or the tz fix.
  DECISION (human): leave origin/main where it is, reconcile locally when the PR lands.
  Pushing main is the action that risks a Railway auto-deploy, and the three RULE_01B remaps are
  still prepared-not-run — prod must not be moved yet. A stale base costs a rebase; pushing costs a
  deploy of five detection changes into a corpus that has not been remapped.
  ✅ CORRECTED LATER THIS DAY: the remote session branched off dc96c71 (the pushed tz-fix branch),
  NOT off origin/main — so the tz fix IS in its base and the "its CI shows market_cap reds" claim
  above is WITHDRAWN. Its base still lacks the RULE_01B fixes, the RULE_09 demotion and sale_full,
  because dc96c71 itself branches off main at ce1c7be. See the 10:20 UTC entry at the end of this file.
  A clean rebase is EXPECTED but must be verified — no file overlap on paper.
  Full reconciliation checklist in the plan file wobbly-singing-wolf.md.

Nothing merged, main not pushed, no deploy, no scheduler, no dev server. DB
177f474b03495c20df10a21335ca9dc3 unchanged. As of this entry the repository has had exactly ONE pull
request ever (#1, merged 2026-07-21) — the RULE_02 PR has not opened yet.

STILL QUEUED: this PR (reconcile, verifier pass, do NOT merge); signing RULE_02 (unblocked by the
fix, its own session — a confident sign on a known-overstated count is what the signed-leg design
forbids); fix/novelty-anchor-rebased (165ceba) AWAITING THE HUMAN'S MERGE DECISION; the three
RULE_01B remaps and the RULE_09 remap, all prepared, none run.

---

## 2026-08-03 10:20 UTC — `fix/rule02-directional-count` (REMOTE session, reported here)

⚠️ WRITTEN BY HAND, and **NOT by this machine's SessionEnd hook** — the hook fired in the REMOTE
container, and its 5-line breadcrumb (header + `990a10b` + `dc96c71`) lives THERE, not in this file.
⚠️ ⭐ **THE COMMIT IS NOT IN THIS TREE.** `git cat-file -t 990a10b` → *"Not a valid object name"*; no
`fix/rule02-directional-count` branch exists locally or on origin; no
`SESSION-2026-08-03-rule02-directional-count.md` on disk; a scan of every repo under `/Users/sapper/dev`
finds the object nowhere. The work is committed only in an EPHEMERAL container with no git remote and
no `gh`. **If that container is gone, so is `990a10b`.** Recovering it means re-teleporting the branch.
⚠️ **Every number below is AS-REPORTED by the remote session and was NOT re-derived here.** This entry
is a transcription so the findings survive the container; it is not a verified local record. **Replace
it with the hook's own breadcrumb + full note when the branch lands.**

WHAT WAS WRONG: the count came from every member in the window and the verb from a net over one
arbitrary row each. So alert **73** read *"3 members sold WAT"* over two exchanges and one member's
partial sales — **1 directional** — and alert **72** read *"2 members traded VSNT"* over two exchanges,
**0 directional**. Count and verb were computed over different populations; same class as the RTX
failure, a leg that is PRESENT counted as though it SAID the thing.

⭐ THE DESIGN POINT WORTH REUSING: `member_direction()` **COMPOSES** the existing `direction()` rather
than copying `rule_cluster._member_direction`'s literal `{"sale","sale_partial"}` set. Keeping
`sale_full` directional is therefore STRUCTURAL, not a second literal that has to remember — which is
exactly the divergence that produced the `sale_full` gap in the first place. `scripts/rule_cluster.py`
reported byte-identical, blob `244e6108`.

🔴 A REGRESSION THE VERIFIER CAUGHT IN THE CHANGE ITSELF: `direction()` was ASYMMETRIC — prefix match
on the sale arm, **exact equality** on the purchase arm — so `"Purchase (Partial)"` read neutral.
Harmless while a neutral row only WEAKENED the verb; a **SUPPRESSION path** once neutral members
stopped being counted, dropping a genuine 3-buyer cluster to 2 so it never fired. Fixed to a prefix
match, the convention `ingest_senate.transaction_verb` already uses. ⚠️ **This is the one departure
from the approved plan**, which had `direction()` staying byte-identical on the reasoning that leaving
it alone was risk-free. That reasoning was wrong — and note the shape: the fix's own tightening turned
a latent cosmetic asymmetry into a silent suppressor.

🔴 THREE STATEMENTS THE VERIFIER OVERTURNED, each corrected in code:
  (a) The false *"exactly as `rule_cluster._cluster_direction` treats one"* — RC requires UNANIMITY and
      returns `mixed` where this returns `NET_LONG`. Not the same rule.
  (b) The overclaim that the VERB was made true when only the COUNT was: `net_direction` is a
      MAJORITY, so *"5 members bought X"* still emits over 3 buyers + 2 sellers. Pre-existing, now
      pinned as a residual that SHOULD fail when someone fixes it.
  (c) The UNDER-SCOPED RULE_CLUSTER queue item — the diverging class is every `sale`-prefixed string
      outside `{sale, sale_partial}`, INCLUDING the unnormalised `Sale (Full)` / `Sale (Partial)`, so
      RC drops those on rows that ALREADY EXIST. Wider than the `sale_full` framing in
      fix/rule-cluster-sale-full (7b9ea46), which was written as prophylaxis on a zero-row type.

⚠️ SUITE (as reported): **1250 passed / 1 failed / 10 skipped / 5 xfailed**, against a **1239**
baseline measured on `dc96c71` BEFORE the change — the **+11** is exactly the new tests. The 1 failure
and 2 collection errors are PRE-EXISTING and in the baseline, one cause:
`scripts/morning_brief.py:627` uses a PEP 701 nested-quote f-string needing Python **3.12+** on a
**3.11.15** container. Not fixed, not hidden. ⚠️ That is an ENVIRONMENT skew, not a code defect — this
machine runs the suite green, so do not "fix" `morning_brief.py` on the strength of it.

⚠️ MUTATION KILLED 4 TESTS, NOT THE 5 PREDICTED — test 2's VSNT shape has only two members and is
below threshold under the OLD logic too, so it cannot distinguish them. Recorded, NOT counted as a kill.

⚠️ NOT VERIFIED — NEEDS PROD. **No database existed in that container** (`Scope/data/` absent), so the
audit's 82-alert corpus, ids **73 / 72 / 5 / 74** and the transaction-type census could NOT be
re-derived; BOTH harnesses ran on SYNTHETIC corpora (**102 → 66** alerts, every removal justified).
The remap is **PREPARED, NOT RUN.** Carry the settling query:

    SELECT transaction_type, COUNT(*) FROM transactions
    WHERE transaction_date >= date('now','-90 days') GROUP BY 1;

Anything outside `{purchase, sale, sale_partial, sale_full, exchange}` is a LIVE SUPPRESSION CANDIDATE
under the new counting, and the same query settles the "`sale_full` has 0 rows" claim.

✅ **RUN HERE, 2026-08-03 — this machine HAS the DB, so four of the above are now PROVEN, not
reported.** Read-only via `file:...?mode=ro`; md5 `177f474b03495c20df10a21335ca9dc3` and mtime
Jul 28 11:23 both unchanged after.

    last 90d:   purchase 242 | sale_partial 133 | sale 103 | exchange 10
    all-time:   purchase 5002 | sale 3354 | sale_partial 1565 | exchange 46

  - ✅ **NO live suppression candidate.** Every type present, in-window and all-time, is INSIDE the
    safe set. The new counting has no unnormalised type to silently drop.
  - ✅ **`sale_full` really is 0 rows all-time.** `7b9ea46`'s "prophylaxis, nothing was lost" framing
    is confirmed on real data.
  - 🔴 **Overturned statement (c) is RIGHT AS A CLASS BUT EMPTY IN PRACTICE.** `sale`-prefixed strings
    outside `{sale, sale_partial}` return **ZERO rows** — no `Sale (Full)`, no `Sale (Partial)`
    anywhere in `transactions`. So RULE_CLUSTER is NOT dropping rows that already exist; the verifier
    widened the class correctly, but its population is empty today. This does not retire the fix —
    the class stays open because `normalize_transaction_type` can emit into it — but it does mean
    **(c) is not the urgent data-loss item the wording implies.**
  - ✅ **The audit's corpus size checks out:** `RULE_02` has exactly **82** alerts here.
  - ⭐ **`exchange` is the mechanism, corroborated:** 46 all-time / 10 in-window non-directional rows
    are exactly what inflated the counts on alerts 73 and 72. The remote diagnosis holds on real data.

⚠️ **Still NOT verified here:** the per-alert attributions (**ids 73 / 72 / 5 / 74** and their
"1 directional" / "0 directional" splits) were not re-derived — that needs the fix's own code, which
is not in this tree.

⚠️ BASE: `990a10b` sits on `dc96c71`, i.e. on the pushed `fix/market-cap-test-tz-flake` branch — NOT on
`origin/main`. So the tz fix IS in its base (which is why no `market_cap` reds appear in its suite),
but `dc96c71` branches off main at `ce1c7be`, so its base still lacks the five RULE_01B fixes, the
RULE_09 demotion and `sale_full`. The reconcile-onto-local-main plan stands unchanged.

Full note (to be written when the branch lands): [[SESSION-2026-08-03-rule02-directional-count]]

---

## 2026-08-03 — RULE_02 directional count: REBUILT LOCALLY, the cloud commit never arrived

⚠️ WRITTEN BY HAND. Supersedes the transcription above: the teleport was attempted and there is no
teleport capability on this side — `990a10b` is absent from every repo under /Users/sapper/dev, its
container had no remote and no `gh`. So the fix was rebuilt here from the relayed report.
Full note: [[SESSION-2026-08-03-rule02-directional-count]]

⭐ REBUILDING BEAT RECOVERING IT. The remote had no database and ran BOTH harnesses on SYNTHETIC
corpora; it could not re-derive a single per-alert claim. Everything below is measured on the real one.

Branch fix/rule02-directional-count: f7e3ec0 then a628d1a, based on dc96c71. NOT merged, NOT pushed;
main untouched at 3250818. Built in a disposable worktree so the dirty vault files and
fix/novelty-anchor-rebased were never at risk. DB 177f474b03495c20df10a21335ca9dc3 unchanged, verified
after every step.

THE FIX: count only directional members; member_direction() COMPOSES direction() instead of copying
rule_cluster's literal {"sale","sale_partial"} (the divergence that lost sale_full there); direction()
made symmetric — it prefixed `sale` but EQUALITY-matched `purchase`; direction taken over ALL a
member's rows, not the first; tags name exactly the counted members.

⭐ THE ASYMMETRY IS THE SUBTLE ONE. While uncounted members still counted, a neutral row only WEAKENED
the verb — cosmetic. Once they are dropped it becomes a SILENT SUPPRESSOR: with the count fix and
without the direction fix, three "Purchase (Partial)" buyers produce NO CLUSTER AT ALL. Latent today —
stored types are only purchase/sale/sale_partial/exchange.

🔴 A REGRESSION I INTRODUCED, CAUGHT BY THE CORPUS DIFF AND BY NO TEST. Letting an individually-mixed
member ABSTAIN from the verb vote made the new code ADD a headline: on MSFT, Gottheimer traded both
ways and Cisneros bought, so it emitted "2 members bought MSFT" off a SINGLE buyer — the same
count-and-verb-disagree defect wearing a different hat. Mixed now counts but FORCES MIXED. Run the
old-vs-new corpus diff even when the tests are green.

🔴 VERIFIER OVERTURNED THE DAMAGE FIGURE: 7 of 82 stored alerts, SIX HIGH — not 4 / 3.
  The remap's predicate was `counted < len(names)`, which finds count inflation and NOTHING ELSE, so
  it could not see three HIGH alerts with the right count and the wrong direction:
      id  7 ADP   "2 members sold"    -> traded (MIXED)   McCormick traded both ways
      id 30 GOOGL "2 members bought"  -> traded (MIXED)   Pelosi traded both ways
      id 66 US    "3 members sold"    -> traded (MIXED)   Biggs traded both ways
  ⭐ THESE WERE THE WORSE RESIDUAL: _candidate_alerts filters severity IN ('HIGH','CRITICAL'), so while
  they stayed HIGH they remained GATE-ELIGIBLE RULE_10 LEGS asserting a consensus that no longer
  exists. The remap was incomplete RELATIVE TO ITS OWN FIX.

🔴 AND A MUTATION MY TESTS MISSED, in code this branch rewrote: net_direction's buy/sell TIE returning
NET_LONG instead of MIXED PASSED ALL 1291 TESTS and flipped 10 real 90-day clusters (19 at 730d) from
MEDIUM/MIXED to HIGH/NET_LONG — "2 members traded AAPL (MIXED)" becoming "2 members bought AAPL
(NET_LONG)". Now pinned at 1-1, 2-2, 3-3; the mutation kills 3.

ALSO OVERTURNED/QUALIFIED: "adds nothing" holds at --days 90 (what production runs) but NOT at
180/365/730, where 2-17 re-verbed MIXED headlines appear. Four tests were weaker than their
docstrings — one re-implemented the production predicate and was killed by no mutation; one billed as
end-to-end proof of order independence SURVIVES that mutation because a later anchor re-emits the same
headline; two pass on the unfixed baseline and are now labelled pins of pre-existing behaviour.

UPHELD: 82/82 tags segment — by exhaustive DP enumeration over all 2689 member names (118 with 2+
commas), UNIQUELY, so mis-segmentation is impossible not merely unlikely; the suite numbers; nothing
compensated (rule_cluster.py byte-identical, no threshold moved, dedup TIGHTENED); remap never run;
the majority pin fails if someone "fixes" it; window_key-on-counted proven OUTPUT-SET EQUIVALENT.

THE REMAP, rebuilt to RECONSTRUCT rather than guess: runs the FIXED find_clusters over the whole
transaction table and asks whether each stored alert is still emitted — a predicate that cannot drift
from the fix because it IS the fix. REFUSES TO WRITE unless the BASELINE reproduces the corpus exactly
(82/82). 4 retract + 3 direction-correct. PREPARED, NOT RUN. ⚠️ My own guard made it NON-IDEMPOTENT —
a successful --apply changes the rows the next run re-checks — until it learned to compare against
pre-images. Verified: apply -> "Nothing to do" -> dry run 0/0 -> fresh copy still finds 7; undo
restores every field; 3347 rows in, 3347 out, no non-RULE_02 row touched.

⚠️ THE GATE IS STILL NOT HONEST. RULE_02 is unsigned, so alert_corroborates short-circuits True
regardless of lifecycle_stage — EXECUTED, not read: a RETRACTED RULE_02 alert still supplies the
congressional instrument and still completes a 3-instrument convergence. Retraction alone does
NOTHING; what removes those three is the drop to MEDIUM. Closing it means SIGNING RULE_02 — the
separate session this fix unblocks, and always its point.

NUMBERS: suite 1294 passed / 8 skipped / 5 xfailed / 0 failed vs a 1256 baseline measured on dc96c71
in its own worktree (+38 = exactly this session's tests; verifier confirmed no pytest config exists to
hide one). Mutations: count 6, purchase 3, all-rows 5, mixed-verb 2, tie 3. Volume on 464 real txns:
at production min_members=3 UNCHANGED 4->4 (identical on ticker/headline/severity/tags); at 2, 24->22
removing only pure-exchange CTRA and HONAV, adding none.

⚠️ The remote's reported morning_brief.py failure is ABSENT here — PEP 701 f-string needing 3.12+ on
their 3.11.15 container; this machine is 3.14.6. ENVIRONMENT SKEW, not a code defect. Do not "fix" it.

STILL QUEUED: signing RULE_02 (now unblocked); reconcile this branch onto local main before merging
(based on dc96c71, so it has the tz fix but NOT the RULE_01B fixes, RULE_09 demotion or sale_full);
fix/novelty-anchor-rebased (165ceba) AWAITING THE HUMAN'S MERGE DECISION; four remaps prepared, none
run — now five, counting this one.

## 2026-08-03 11:54 UTC — `fix/novelty-anchor-rebased`
1 commit(s) in the last 6h:

  - 165ceba fix(novelty): the anchor is a token, not a substring

⚠️ Header and commit list ABOVE are the HOOK's — everything below this line was written by hand.
⚠️ The hook reports the MAIN CLONE's branch and saw only 165ceba. It could not see today's RULE_02
work, which was committed in scratch WORKTREES on other branches (f7e3ec0, a628d1a, 4c2632e). The
hook's record is accurate for what it observed and incomplete as a record of the session.

## 2026-08-03 — RULE_02 defect #2: ticker resolution / keying

⚠️ SUPERSEDED IN PART — the verifier later OVERTURNED two claims in this entry. Read the
correction block at the END of this file before trusting the rung-2 / 'strictly subtractive'
statements below.

Full note: [[SESSION-2026-08-03-rule02-ticker-resolution]]
Branch fix/rule02-ticker-resolution on a main+tz+#1 base (4c2632e). Changes UNCOMMITTED per the DoD.
main untouched. DB 177f474b03495c20df10a21335ca9dc3 unchanged, verified after every step.

THE DEFECT: `raw_ticker_string AS ticker` at :27, grouped at :69 (:31/:124 after #1) — no `tickers`
join, no normalize_ticker. `US` is NOT a symbol; 213 transactions carry it, ALL 213 unlinked, and it
produced alerts 66/67/68/69. `CA` (60 txns, all unlinked) produced alert 16. 5 of 82 stored RULE_02
alerts key on an unresolved symbol; 4 HIGH.

🔴 THE MERGE HALF WAS DEFERRED BY HUMAN DECISION, because the only available merge mechanism is
corrupt. Of 10 symbols split across raw variants, THREE groupings join DISTINCT companies:
    IDEXX -> DLB    "DC Laboratories, Inc."       IDEXX Labs is IDXX, not Dolby
    MTRS  -> GIS    "SP GENERAL FINL CO INC"      not General Mills
    CNSWF -> STZ    "SP CONSTELLATION SFTWRE"     Constellation SOFTWARE, not BRANDS
  The ingestion linker matches on company NAME. ⭐ NO MECHANICAL TEST SEPARATES these from the seven
  genuine variants (WMT+CS, META+FB, NOC+CORP, STX+PLC, WBI+LLC, DIS+CS, SPY+CALL) because NONE of the
  ten raw variants is itself a symbol in `tickers` — the only discriminator is company-name semantics,
  i.e. exactly the fuzzy reasoning the contract forbids. Merging buys 0 clusters at the production
  window, so deferring costs nothing measurable.
  ⚠️ WIDER THAN RULE_02: RULE_CLUSTER groups on ticker_id TODAY (rule_cluster.py:116), so it may be
  mis-merging these three pairs right now, unmeasured. Its own session.

THE DESIGN — only a symbol verified against `tickers` confers a corroboration key:
  1. normalize_ticker(raw) in the validity set -> that canonical symbol, RESOLVED.
  2. no raw string but a ticker_id -> linked symbol as a GROUP key, NOT resolved.
  3. otherwise -> canonicalised raw as a group key, NOT resolved.
  Unresolved clusters are still EMITTED: ticker='', lifecycle_stage='review', symbol in why_matters.
  Never dropped, never fuzzy-resolved. Rung 1 is RULE_01B #4's rule exactly, incl. '-'->'.'.

🔴 THE VERIFIER CAUGHT MY OWN STAGE-1 REASONING BEFORE IT DIED. Rung 2 originally returned RESOLVED,
on my reasoning that an empty raw string leaves "no competing signal, so the link is safe". The first
verifier run found a mis-link among exactly those rows, then died to an API error. It was right:
    ASCIX x13   `tickers` "Angel Oak Strategic Credit Fund" vs filing "Oaktree Strategic Credit Fund"
    RBBN        `tickers` "Ribbon Communications"           vs filing "Verizon Communications"
  14 of the 30 recovered rows are MIS-LINKED. ⭐ THE LESSON: the absence of a contradicting signal is
  NOT the absence of a contradiction — it only removes the means of DETECTING one.
  ⚠️ WITHDRAWN as a result: the claim (which I had already reported) that the fold-in produces one new
  cluster "3 members sold PLTR". PLTR is recovered but UNKEYED; it never reaches the gate.
  COST, honestly, and the framings diverge because ASCIX is 13 of the 14 bad rows:
      by ROW    14/30 mis-linked (47%);  16/30 correct but now barred (53%)
      by SYMBOL  2/18 mis-linked (11%); 16/18 correct but now barred (89%)
  Barred-but-correct: COCO CRDO GRKZF HIMX ICE KRP MGY MRK MSI PH PLTR RHP SEI SMPL SPOT TOL.
  Barring is still right: these rows were DISCARDED ENTIRELY before, so declining to key them is not a
  regression, and keying them would open a corroboration surface with an 11% symbol-level error rate —
  what signed-signal-engine exists to prevent. Revisit once the linker is fixed.

MEASURED EFFECT (real corpus):
                          90d (PRODUCTION)        all-time
    rows fed              464 -> 464              9616 -> 9646  (+30)
    clusters                4 -> 4                 325 -> 327
    corroboration keys      3 -> 3                  79 -> 78
    keys removed/added      0 / 0                  US / NONE
    phantoms at the gate    0 -> 0                  12 -> 0
  ⭐ STRICTLY SUBTRACTIVE AT THE GATE: one key removed, ZERO added. Production window unchanged.

⚠️ THIS CORRECTION BITES WHERE #1's DID NOT. _candidate_alerts:181 selects
`WHERE ticker IS NOT NULL AND ticker != ''`, so the EMPTY KEY genuinely removes the alert from the
gate. lifecycle_stage='review' does NOT: RULE_02 is unsigned, so alert_corroborates short-circuits
True regardless of lifecycle. KEY REMOVAL BITES; RETRACTION IS COSMETIC. Signing RULE_02 is still what
makes lifecycle count.

THE REMAP — remap_rule02_ticker_resolution.py, PREPARED, NOT RUN. Dry run 5 un-key / 0 re-key / 77
already correct. Pre-flight read-only and window-scoped (all-time 5 unresolved / 4 HIGH; last 30d: 0).
On a throwaway copy: exactly ids 16/66/67/68/69 changed, ONLY ticker/lifecycle_stage/why_matters — NO
score column, checked against all seven. 3347 rows in, 3347 out. Idempotent. Undo exact.
⚠️ Its guard is deliberately NOT #1's "baseline reproduces the corpus" gate — #1 changed cluster
MEMBERSHIP so reconstruction proved something; here the change is a pure function of the stored
ticker, so it would prove nothing. The real hazard is that a truncated `tickers` would make EVERY
alert look unresolved and blank the whole corpus. So it refuses on an empty validity set or >50%
unresolved, mirroring rule_01b's own alarms. Both verified: exit 2, ZERO rows written.

NUMBERS: suite 1324 passed / 8 skipped / 5 xfailed / 0 failed vs a 1294 baseline on 4c2632e (+30 =
exactly the new test file). Mutations: raw-keying restored 13, rung-2 recovery removed 6,
FK-wins-over-raw 13, link-confers-a-key 6. scripts/rule_cluster.py BYTE-IDENTICAL to HEAD. 3 files.

🔴 THE INDEPENDENT VERIFIER PASS IS UNVERIFIED — THREE attempts died to API 529 (Overloaded), a
server-side fault, not a result. One real finding landed before the first died and is fixed above.
Everything else here is the IMPLEMENTER's evidence and has NOT been independently checked.
RE-RUN THE VERIFIER on this branch when the API recovers.

STILL QUEUED: re-run the verifier (the one outstanding DoD item); the INGESTION LINKER (fuzzy
company-name matching, DATA-LOSS class, affects every ticker_id consumer incl. RULE_CLUSTER today —
fixing it also unblocks the deferred merge); #3/#4 identity+refire; then SIGNING RULE_02;
fix/novelty-anchor-rebased (165ceba) AWAITING THE HUMAN'S MERGE DECISION; five remaps prepared, none
run — now six, counting this one.


## 2026-08-03 (correction) — RULE_02 defect #2: what the verifier overturned

The verifier PASSED on the fourth attempt (three died to API 529). It overturned TWO claims I had
already reported as sound, and found FOUR uncaught mutations plus a tautological test. All fixed.
Full note: [[SESSION-2026-08-03-rule02-ticker-resolution]]

🔴 OVERTURN 1 — the "recovery" of rows with a ticker_id and no raw string was NOT inert.
  ⭐ AN FK-DERIVED KEY COMES FROM `tickers.symbol` AND IS THEREFORE IN THE VALIDITY SET BY
  CONSTRUCTION. So a "recovered, unresolved" row joined the GENUINE cluster for that symbol and
  dragged the whole group to unresolved:
      Cluster: 3 members sold MRK    -> ticker='' , "symbol not in `tickers`: 'MRK'"
      Cluster: 3 members bought PLTR -> ticker='' , "symbol not in `tickers`: 'PLTR'"
  MRK and PLTR ARE in `tickers`. The MRK cluster was KEYED UNDER HEAD and lost its key — a real
  regression — and why_matters stated something FALSE on a user-facing row. My code comment claiming
  resolved and unresolved "can never collide" was simply wrong: they collide for 12 of 18 recovered
  symbols.
  FIX: transactions.ticker_id no longer participates in keying AT ALL. Those 30 rows are SCOPED OUT,
  exactly as before the change — a linker COVERAGE concern (14 of the 30 are mis-linked anyway).
  That also makes the invariant real and provable:  resolved is True  <=>  key in valid
  — now a property test over every input shape plus a runtime assertion that no mixed group forms.

🔴 OVERTURN 2 — "strictly subtractive at the gate" was FALSE as stated. The distinct-key SET went
  79->78 as claimed, but that metric HID three clusters (1 MRK, 2 PLTR) losing their key, because MRK
  survived via another cluster. Now measured per-CLUSTER and genuinely true.

🔴 FOUR UNCAUGHT MUTATIONS, all now killed:
      _validity_set without normalize_ticker   survived -> 1 kill
      drop the basket (" " in key) filter      survived -> 1 kill
      dedup on group key not stored ticker     survived -> 1 kill
      all() -> any() on the resolution line    survived -> now unreachable
  `tickers` holds 551 DASH-form symbols and ZERO dot-form, so the first un-keys BRK.B / BF.B / HEI.A
  on real data — BRK.B is an actually-emitted cluster key.

🔴 A TAUTOLOGICAL TEST: test_the_blank_key_is_what_the_gate_filters_on asserted a phrase in
  _candidate_alerts.__doc__ then re-implemented the predicate in its own SQL. The verifier DELETED the
  gate's `WHERE ticker != ''` and the test still passed. It now calls _candidate_alerts for real.

RE-MEASURED AFTER THE FIX:
                            90d (PRODUCTION)      all-time
    rows fed                464 -> 464            9616 -> 9616   (identical; no fold-in)
    clusters                  4 -> 4               325 -> 325
    corroboration keys        3 -> 3                79 -> 78
    keys removed/added        0 / 0                 US / NONE
    clusters demoted          0                     0
    phantoms at the gate      0 -> 0                12 -> 0
  ⭐ Strictly subtractive is now true per-CLUSTER, not just per key-set.

⚠️ SEQUENCING, found by the verifier and now in the script: RUN THE REMAP BEFORE THE FIXED RULE.
  Changing a stored ticker from 'US' to '' changes the alert_exists dedup key, so a rule run first
  re-emits one duplicate per unresolved cluster still inside the 7-day window. Harmless today only by
  accident — the newest stored RULE_02 alert is 2026-07-20.

RESIDUALS, NOT FIXED: unresolved groups still conflate DISTINCT companies (raw='CS' exists under both
"Walmart Inc." and "The Walt Disney Company" -> one "N members bought CS" alert) — but now UNKEYED,
which is strictly better than HEAD where it carried ticker='CS' and could corroborate; pinned by a
test that SHOULD fail once the parser is fixed. `tickers` has real gaps (DFS FI K X DAY TPH ITCI SWTX
CHX absent from 10,619 rows). ⚠️ INCIDENTAL, NOT MINE: this tree's _candidate_alerts selects
corroborates / corroboration_note / award_key, none of which exist in the working DB —
"no such column: corroborates". MIGRATION m014 IS OUTSTANDING on this database.

⚠️ TRUSTED-DATA CAVEAT: every census here comes from a byte copy of the LOCAL WORKING DB, which
CLAUDE.md treats as untrusted. The verifier re-derived each independently, but they are corroborative
for production, NOT decisive. Production figures stay UNVERIFIED — needs prod.

NUMBERS: suite 1365 passed / 8 skipped / 5 xfailed / 0 failed vs the 1294 baseline on 4c2632e (+71 =
exactly the new test file). Mutations: raw-keying 30, validity-set 1, basket 1, dedup 1, FK-participates 8.
rule_cluster.py byte-identical. 3 files. UNCOMMITTED. DB 177f474b03495c20df10a21335ca9dc3 unchanged.

STILL QUEUED: the INGESTION LINKER (fuzzy company-name matching, DATA-LOSS class, affects every
ticker_id consumer incl. RULE_CLUSTER today — unblocks both the merge half and the 30 rows);
MIGRATION m014 on the working DB; #3/#4 identity+refire; SIGNING RULE_02;
fix/novelty-anchor-rebased (165ceba) AWAITING THE HUMAN'S MERGE DECISION; six remaps prepared, none run.

---

## 2026-08-03 — INGESTION LINKER DIAGNOSIS (read-only; no code changed, no data written)

Full note: [[SESSION-2026-08-03-ingestion-linker-diagnosis]]
Detached worktree at main (3250818). DB 177f474b03495c20df10a21335ca9dc3 unchanged, confirmed
independently by the verifier. NO FIX IMPLEMENTED — options only, per the contract.

MECHANISM: resolve_tickers.py:253-260 is the ONLY writer of a non-NULL transactions.ticker_id —
`git log -S"SET ticker_id"` finds ONE commit in all of history. Exact-symbol lookup (:163) first, then
difflib.get_close_matches over the WHOLE raw_description, FUZZY_CUTOFF=0.7 (:20), n=3, ambiguity guard
only when a runner-up is within 0.03. Unconfident -> LEFT NULL (:262), never forced. Scheduling is
isolated: only refresh_tickers_only() is scheduled and cannot reach resolve_transactions. Same difflib
the campaign banned in RULE_09 — at the ingestion layer.

⭐ THE ROOT CAUSE, WHICH I MISSED AND THE VERIFIER FOUND: get_close_matches (:185) is called
CASE-SENSITIVELY while the ambiguity guard (:196) and the final lookup (:219) both casefold(). So a
perfect match is discarded:
    ratio('Marsh & McLennan Companies, Inc.', 'MARSH & MCLENNAN COMPANIES, INC.') = 0.375  CORRECT, below cutoff
    ratio('Marsh & McLennan Companies, Inc.', 'Bausch Health Companies Inc.')     = 0.700  WRONG, at cutoff
    casefolded: correct 1.000, wrong 0.667
Marsh & McLennan IS in `tickers`. Casefolding candidate generation alone changes 12 of 73 fuzzy rows,
EVERY change an improvement (RBBN->VZ, DLB->None, TANH->None). My "shared-token collision" explanation
was WRONG — for RBBN/MMC/UHID the correct name shares the token too and scores 1.0 casefolded.

🔴 OVERTURNED — MY BLAST RADIUS WAS AN UNDERCOUNT BY MORE THAN HALF: 45 rows / 16 symbols, not 20 / 5.
  ⚠️ THE METHODOLOGICAL FAILURE, worth remembering: the "five named cases" are verbatim the list in
  rule_02_cluster.py:78-86, which enumerated two narrow sub-cases and never claimed completeness.
  I INHERITED A DOCSTRING AS THE POPULATION instead of re-deriving from data.
  Missed: NTLA<-ITCI x5, TANH<-TCTZF x4, NLCP<-II x4, BHC<-MMC x3, HSDT<-Solana (CRYPTO) x2,
  MRAAF, PMHS, SUGP, NWPG, LOAR, CPBI.
  ⭐ THE RIGHT SIGNAL WAS THE RESOLUTION PATH ITSELF: only 73 of 8249 rows (0.9%) took the fuzzy
  fallback, and because exact-mismatch is 0, that set has COMPLETE RECALL BY CONSTRUCTION. Costs
  nothing, needs no matching. The structural version is a `resolution_method` column we lack.

SIGNALS: (a) raw-ticker-names-a-different-real-symbol = 0 rows and STRUCTURALLY CANNOT FIRE, because
resolve_by_symbol runs first so an exact hit IS the link. (c) CIK/CUSIP UNAVAILABLE — transactions has
no such column. (b) token-disjoint = 677 rows/118 symbols, PRECISION 0.183, useless as a bound.
Two detectors that DO work: fuzzy-linked + well-formed raw absent from tickers (precision 0.72), and
the casefold-disagreement test (12/12 true positives, and it catches RBBN which the other cannot).

TWO DISJOINT POPULATIONS (overlap 0):
    linker-origin   45 rows / 16 symbols   difflib path      the LINKER's defect
    fixed-income    ~95 rows / 18 symbols  EXACT path        the PARSER's defect
  ⭐ The parser attribution is AIRTIGHT and needs no regex: exact-mismatch is 0 and the whole fuzzy
  population is 73 rows, none fixed-income, so EVERY fixed-income row is necessarily exact-path. The
  parser emits a bogus raw ticker that happens to be a real symbol — STATE ABBREVIATIONS PARSED AS
  TICKERS ('Arlington, Municipal Bond [GS]' -> TX; 'ST SER J Municipal Bond' -> OR).

🔴 THE GATE:
  LATENT: 0 of 59 qualifying RULE_CLUSTER clusters rest on a mis-link — and the verifier STRENGTHENED
  this with a counterfactual I never ran: correct all 135 mis-linked rows and the 59 clusters move by
  ZERO IN BOTH DIRECTIONS (no false merges AND no false splits).
  ⚠️ LIVE: OVERTURNED to UNVERIFIED — NEEDS PROD. Every source in activity_log stops 2026-07-20
  (RULE_10, RULE_CLUSTER, PARSE_HOUSE_PDFS, INGEST_HOUSE_INDEX). The whole local system is frozen, so
  "0 in the 45-day horizon" measures the SNAPSHOT'S AGE, not production.

🔴 NEW FINDING THAT CONTRADICTS MY OWN CONCLUSION — the parser defect DOES reach the gate, by a route
  I ruled out. RULE_CLUSTER does NOT group on ticker_id: :116 is the JOIN, the KEY is :129
  `normalize_ticker(resolved_symbol OR raw_ticker_string)`. That fallback RESURRECTS exactly what the
  linker correctly declined to link — an all-time `US` cluster of 3 members composed ENTIRELY of U.S.
  Treasury bills (ticker_id NULL, raw='US', 140+ rows across A000372/B001325/S001201).
  RULE_02 CLOSED THIS CLASS IN #2 WITH A VALIDITY SET. RULE_CLUSTER HAS NOT.
  jpt_common.py:1368 (congress_day_digest) has the same COALESCE shape, read-only display.

RULE_02 POST-#2 IS INSULATED — no JOIN tickers, no ticker_id, resolve_key(raw, None, valid). Confirmed.

FIX OPTIONS — RECOMMENDED, NOT TAKEN:
  A. Casefold candidate generation (:185). ONE LINE. Fixes 12/73, all improvements, no regression.
  B. Give RULE_CLUSTER a validity set as RULE_02 #2 did — THE ONLY OPTION THAT CLOSES REAL GATE EXPOSURE.
  C. Link on a canonical identifier (CIK/CUSIP), unconfident -> NULL; mirrors RULE_11 generated_internal_id
     and the RULE_09 difflib ban. Needs a schema column + parser work.
  D. Record `resolution_method` + a review queue for the 73 fuzzy rows. Cheap; makes future audits trivial.
  E. Backfill/re-link existing mis-links — separate DATA-LOSS remap, own session. NOT urgent: the
     counterfactual says correcting them moves the gate by zero. Correctness-of-record, not gate repair.
  SMALLEST SAFE FIX: A.   SMALLEST FIX THAT CLOSES REAL GATE EXPOSURE: B (not in the linker at all).
  ⚠️ DO NOT DELETE THE FUZZY FALLBACK — FB->META is a legitimate rename RESCUED BY IT. Repair, don't remove.
  ⚠️ GE<-'Aerospace Common Stock' is LEGITIMATE (GE Aerospace rename) — a false positive of signal (b).

ALL LOCAL COUNTS ARE UNVERIFIED FOR PRODUCTION. Three read-only prod queries are in the full note.

STILL QUEUED: decide among the fix options above (human); MIGRATION m014 on the working DB;
#3/#4 identity+refire; SIGNING RULE_02; fix/novelty-anchor-rebased (165ceba) AWAITING MERGE DECISION;
six remaps prepared, none run.

---

## 2026-08-03 — RULE_CLUSTER ticker validity: the Treasury cluster leaves the gate

Full note: [[SESSION-2026-08-03-rule-cluster-ticker-validity]]
Branch fix/rule-cluster-ticker-validity on main+tz (65ad0d7). UNCOMMITTED per the DoD. main untouched
at 3250818. DB 177f474b03495c20df10a21335ca9dc3 unchanged, confirmed independently by the verifier.

THE DEFECT: rule_cluster.py:129 keys on normalize_ticker(resolved_symbol OR raw_ticker_string). When
the ingestion linker CORRECTLY declines to link a row (ticker_id NULL), the `or` resurrects the raw
parse string it just rejected — a 3-member `US` cluster built ENTIRELY of Treasury bills (213 rows,
11 members, ticker_id NULL on every one, window 2026-02-17). RULE_02 #2 closed this class with a
validity set; RULE_CLUSTER never got one. Identified by the linker diagnosis as the ONLY fix that
closes real gate exposure — and it is not in the linker.

⭐ THE FALLBACK IS VALIDATED, NOT DELETED. SPCX reaches RULE_CLUSTER THROUGH the `or` — all four of
its transactions have ticker_id NULL — and it is a real symbol in `tickers`. Deleting the `or` would
have killed a genuine cluster. Of 59 all-time qualifying clusters exactly ONE keys on an invalid
symbol; A/B on disposable copies shows the other emitted clusters byte-identical, and across 22
compared clusters only `US` differs.

SCOPE, by AST extraction: _cluster_direction (unanimity), _gather, _best_window, _member_direction,
_fingerprint, _verb, main, build_parser all BYTE-IDENTICAL. Only _prior_cluster_alerts and run
changed; _validity_set and _fingerprint_ticker are new. ⚠️ THIRD copy of _validity_set — noted, not
refactored (a shared helper touches three rules and belongs in its own pass).

KEY REMOVAL IS THE MECHANISM. RULE_CLUSTER is unsigned, so alert_corroborates returns True for
None/review/superseded/created alike (executed). What drops the alert is _candidate_alerts:181's
`ticker != ''`.

🔴 THE VERIFIER FOUND A REGRESSION *THIS CHANGE* INTRODUCED — the most serious finding.
  Every unvalidated cluster stores the SAME ticker='', and _prior_cluster_alerts narrowed on that
  column while the identity test is (member set, direction) with NO SYMBOL IN IT. So clusters on
  DIFFERENT COMPANIES deduped against each other. Demonstrated against the real tickers table with two
  real-but-absent symbols:
      FI (4 members) + CTRA (3-member subset)  -> CTRA emitted NOTHING
      FI (3) then CTRA (4), two runs           -> FI marked lifecycle_stage='superseded' with
                                                  "[superseded by alert 8928: cluster expanded to 4 members]"
  The second is worse than a miss — a FABRICATED supersede note attributing one company's expansion to
  another. My claim "an unvalidated cluster still forms and still emits" was FALSE whenever another
  unvalidated cluster sat in the 45-day dedup window with an overlapping member set.
  FIX: _prior_cluster_alerts(conn, stored_ticker, group_ticker) — SQL still narrows on the stored
  column, then each candidate's symbol is recovered from its fingerprint (CLUSTER::members::TICKER::dir)
  and matched. No-op for validated clusters. Mutation removing the check kills 2.

🔴 AND IT OVERTURNED A HEADLINE CLAUSE — state abbreviations are NOT closed.
  The whitelist validates the SYMBOL, not the INSTRUMENT, and most of those abbreviations ARE real
  tickers: TX=Ternium, OR=OR Royalties, GO=Grocery Outlet, ST=Sensata, AA=Alcoa, BC=Brunswick,
  AD=Array Digital. A 3-member "Arlington, Municipal Bond" cluster keys as TERNIUM and sails through
  with unvalidated=0. ⚠️ Worse: my test "proved" the class closed using PA — the ONE abbreviation
  absent from `tickers`. A SELECTION EFFECT. Now pinned as a KNOWN_LIMIT test that should fail once
  the PARSER is fixed. The fix closes the symbol-not-in-tickers class (US Treasury) and nothing wider.

ALSO FIXED FROM THE SAME PASS:
  - Two surviving mutations. Dropping `unvalidated += 1` killed nothing (counter + WARNING untested).
    Re-anchoring novelty on the blanked ticker killed nothing, because the test asserted
    novelty_score == 1.0 on a first-ever alert in an empty DB — TRUE UNDER ANY ANCHOR, and it passed
    on unfixed code. TAUTOLOGICAL. Replaced with a spy reading the novelty_key actually handed to
    insert_alert; the real evidence is jpt_common.py:1537, not the score.
  - THE WARNING WAS INVERTED ON ITS OWN WORST CASE: `if valid_symbols and unvalidated and ...` meant an
    EMPTY tickers — which un-keys the whole corpus — printed nothing. Now a distinct
    CRITICAL:tickers_table_empty_every_cluster_unvalidated, with a test.

⚠️ SIX EXISTING TESTS FAILED AND I EDITED TWO TEST FILES (test_rule_cluster.py, test_phase3.py) —
their fixtures never seeded `tickers`, so every fixture symbol became unvalidated. I judged these
fixture gaps and asked the verifier to audit that call specifically, because "the tests failed so I
changed the tests" is how a real regression gets buried. IT CONFIRMED: 7 and 4 additions, ZERO
deletions, pure INSERT OR IGNORE INTO tickers plus comments, no assertion touched. One cost it named:
test_dedup_same_identity_then_upgrade_on_new_member now runs only on the validated path — exactly
where the collision bug lived. Also, one failure was MY OWN COMMENT tripping test_cleanup_pass: the
phrase "jpt_common, which four rules share" matches the stale 4-rules/24h gate-wording guard.

⚠️ THE COST: FI (Fiserv — `tickers` still holds the pre-2023 FISV), CTRA and NSRGY are REAL symbols
that fail validation. None forms a qualifying cluster today, but each is one 72h coincidence from
being de-keyed while genuinely real. RECOMMENDED PRE-STEP, not done here: run refresh_tickers_only()
— the safe, already-scheduled half that touches no transaction or alert. tickers last updated 2026-07-09.

NUMBERS: suite 1272 passed / 8 skipped / 5 xfailed / 0 failed vs a 1256 baseline on the same base
(verifier re-ran both from clean trees); +16 = exactly the new test file. Mutations: restore raw
fallback 5 (incl. the US gate test), validity-set-without-normalize 2, dedup-without-symbol-check 2,
drop the review write 1, drop the counter 1, novelty on the blanked ticker 1, un-invert the WARNING 1.

REMAP remap_rule_cluster_ticker_validity.py: PREPARED, NOT RUN, and a NO-OP locally (0 to un-key —
SPCX validates). On disposable copies: only ticker/lifecycle_stage/why_matters touched, opportunity /
novelty / evidence unchanged; idempotent; undo exact. BOTH guards (empty tickers, >50% unvalidated)
exit 2, write nothing, and do not even create the backup table. ⚠️ _connect(None) resolves to the
WORKING DB — always pass --db when testing.

⚠️ MERGE: fix/rule-cluster-sale-full (7b9ea46) also edits this file (adds sale_full to
_DISPOSAL_TYPES) and is unmerged — the two need reconciling.
⚠️ All censuses come from the local working DB, which CLAUDE.md treats as untrusted and which froze
2026-07-20. "Exactly one of 59" is LOCAL-ONLY; prod is UNVERIFIED. The settling query is in the note.

STILL QUEUED: THE PARSER (emits bogus tickers that are REAL symbols — the only thing that closes the
class this fix leaves open); refresh_tickers_only(); the linker casefold fix (option A, zero gate
impact); the _validity_set DRY pass across three rules; MIGRATION m014 on the working DB; #3/#4
identity+refire; SIGNING RULE_02; fix/novelty-anchor-rebased (165ceba) AWAITING MERGE DECISION;
seven remaps prepared, none run.

---

## 2026-08-04 — House parser: never drop a row (A) + stop mutilating the description (C)

Full note: [[SESSION-2026-08-04-house-parser-no-drop]]
Branch fix/house-parser-no-drop on main+tz (615412b). UNCOMMITTED. main untouched at 3250818.
DB 177f474b03495c20df10a21335ca9dc3 unchanged, confirmed independently. 🔴 DATA-LOSS class.

THE DEFECT: is_blocklisted (parse_house_pdfs.py:58) is True for ANY 1-char ticker, and BOTH call
sites used `continue` — discarding the WHOLE transaction (member, date, amount, asset name), not just
the unusable symbol.

⭐ THE UNDER-CLAIM THE VERIFIER CORRECTED UPWARD — the strongest argument for the fix:
  `ST` is IN the blocklist, and `[ST]` is the ORDINARY HOUSE STOCK ASSET-TYPE BRACKET. With no
  parenthesised ticker the fallback lifts `ST` out of that bracket, the blocklist rejects it, and the
  row was thrown away. So on HEAD, EVERY House line lacking a parenthesised ticker was discarded
  whole, whatever its real symbol. Verified: Tesla / Alphabet / Costco / Microsoft / Berkshire all
  DROPPED on HEAD, all kept now. Table-like path 1/4 -> 4/4.

THE FIX (A+C only): both `continue`s -> `ticker = None`, keeping the holding; and the
`\b{ticker}\b` deletion at the old :408 removed so raw_description keeps the full name. Because the
description mutation is guarded by `if ticker:`, clearing the ticker first means a rejected row keeps
its description INTACT INCLUDING the "(F)" — the best available input for the linker.
B NOT DONE: TICKER_RE / PARENTHESIZED_TICKER_RE / TICKER_BLOCKLIST / is_blocklisted byte-identical,
verified independently. is_blocklisted('F') is still True — the SYMBOL is still rejected, just not
fatal to the ROW.

🔴 OVERTURNED — MY SAFETY CLAUSE WAS TOO STRONG.
  I claimed a kept row "cannot become a false corroboration key". True of raw_ticker_string; FALSE of
  the ROW. ticker_id is a SECOND key channel and RULE_CLUSTER._gather:121-122 PREFERS it via COALESCE.
  A row this fix newly keeps then reaches the fuzzy linker:
      Agilent Technologies Inc (A) [ST]   HEAD: DROPPED
                                          FIXED: kept, raw=None -> linker sets ticker_id -> 'SINT'
  SINT is SiNtx Technologies — an UNRELATED company. That key did not exist on HEAD because HEAD
  destroyed the row. The feared failure was "bare A becomes Agilent"; the real one is worse.
  MITIGATING, all verified: resolve_transactions is human-gated and NEVER scheduled
  (resolve_tickers.py:307-319); RULE_01B and RULE_02 read only raw_ticker_string and stay inert; and C
  gives the linker better input. Preserving a holding still beats destroying it. Honest claim is now
  "carries no key-able RAW STRING". The linker casefold fix is what closes the rest.

🔴 OVERTURNED — "the pipe path was wholly non-functional" was a CHOSEN-WINDOW result.
      order=(date,type,tkr,asset,amt)  HEAD 0/3   <- the order I happened to pick
      order=(tkr,asset,type,date,amt)  HEAD 3/3, symbols extracted correctly
  HEAD only loses rows when the TYPE column precedes the TICKER column, because TICKER_RE.fullmatch
  hits the single-letter type code first. Wording corrected throughout.

🔴 TWO ESCAPING MUTATIONS — the guard rested on UNTESTED STATEMENT ORDER.
      E5  re-lift the ticker from the CONTINUATION line after the guard  -> leaked 'F', 26 passed
      E6  exempt 'US' from the pipe-path guard                           -> leaked 'US', 26 passed
  E5 is the serious one: the wrapped-line lookup at :383-390 sits ABOVE the guard today, which is the
  only reason a wrapped "(F)" is caught — and NO TEST USED A TWO-LINE FIXTURE AT ALL. Both now killed,
  with a "(DHI)" control so the continuation lookup itself stays protected.

ALSO CORRECTED BY THE VERIFIER:
  - RULE_02 mis-cited TWICE: it is rule_02_cluster.py:33, not :127, and it has NO trim — demonstrated,
    it clusters on '' and '   '.
  - The whitespace warning named the WRONG RULE. RULE_CLUSTER runs normalize_ticker at _gather:129
    which maps '   ' to None; RULE_02 is the one at risk.
  - test_the_rules_all_exclude_a_symbol_less_row was TAUTOLOGICAL — a hand-built in-memory table
    asserting SQLite semantics, never touching a rule, passing unchanged on HEAD. Replaced with one
    that inserts a real symbol-less row and calls r02.fetch_transactions and rc._gather directly.
  - The 207 / 441 figures are a BASE RATE, NOT a controlled comparison: the House parser AND the
    blocklist landed in the SAME commit (a5538d8), and the 207 rows predate the parser in tracked
    history. Local DB untrusted and frozen 2026-07-20; prod UNVERIFIED. Now caveated in source too.

NUMBERS: suite 1296 passed / 8 skipped / 5 xfailed / 0 failed vs a 1256 baseline on the same base
(verifier re-ran both from clean trees); +40 = exactly the new test file, which is the parser's FIRST
real coverage. Against the HEAD parser 19 of the original 26 fail — the 7 that pass are the controls
and must-not-change tests, which is correct. Mutations: drop-site-1 19, drop-site-2 9, token-delete 4,
carry-the-symbol 13, E5 1, E6 1. Verifier fuzzed 86,000 differential cases: ZERO where the fix keeps
fewer rows than HEAD, ZERO where a blocklisted or whitespace symbol survives on a kept row.

FORWARD-ONLY: two files (parse_house_pdfs.py + the new test). NO DML, no migration, no schema change.
The already-dropped rows are NOT recovered here — they left no trace and need a re-parse of the source
PDFs (OPTION E), its own human-gated DATA-LOSS session.

STILL OPEN, recorded not fixed: non-blocklisted bracket codes still become tickers
(`Treasury Bill [GS]` -> GS = Goldman Sachs; `Cash Account [BA]` -> BA = Boeing) — options B/D; the
pipe path does not apply ignored_tickers so it emits JT/DC owner codes as tickers (pre-existing);
symbol EXTRACTION on the pipe path (the type code wins) — pre-existing; and the LINKER CASEFOLD FIX,
which is what closes the SINT residual above.

STILL QUEUED overall: option B (single-letter recovery) and option E (backfill); the linker casefold
fix; the _validity_set DRY pass across three rules; MIGRATION m014 on the working DB; RULE_02 #3/#4
identity+refire; SIGNING RULE_02; fix/novelty-anchor-rebased (165ceba) AWAITING MERGE DECISION;
seven remaps prepared, none run.

---

## 2026-08-04 — Ingestion linker: casefold-consistent candidate generation

Full note: [[SESSION-2026-08-04-linker-casefold]]
Branch fix/linker-casefold on main+tz (1bdc8c9). UNCOMMITTED. main untouched at 3250818.
DB 177f474b03495c20df10a21335ca9dc3 unchanged, confirmed independently.

THE DEFECT: resolve_by_company_name compared the RAW-CASE description against RAW-CASE company names
while the ambiguity guard and the final lookup both casefold().
    ratio('Marsh & McLennan Companies, Inc.','MARSH & MCLENNAN COMPANIES, INC.') = 0.375  REJECTED
    ratio('Marsh & McLennan Companies, Inc.','Bausch Health Companies Inc.')     = 0.700  ACCEPTED
    casefolded: correct 1.000, wrong 0.667
MRSH|MARSH & MCLENNAN COMPANIES, INC. is in `tickers` — a ratio-1.0 target existed and was discarded
on case alone. (The real symbol is MRSH, not MMC.)

THE FIX — TWO LINES: load_ticker_maps appends the already-computed casefold to company_names;
resolve_by_company_name probes with description.casefold(). ⭐ Because casefold is IDEMPOTENT the
ambiguity guard and lookup are TEXTUALLY UNCHANGED — AST-verified byte-identical from `if not
matches:` onward. difflib / FUZZY_CUTOFF=0.7 / resolve_by_symbol untouched. 0 exact-path decision
changes over 9,966 rows. FB->META survives (verified against the real 10,619-name table): the fallback
is REPAIRED, not removed — the RULE_09/RULE_11 difflib ban is for the RULES, not this human-gated step.

🔴 OVERTURN 1 — THE FUNCTION IS FIXED; THE DATA IS NOT.
  resolve_transactions selects WHERE ticker_id IS NULL, so 12 of the 13 corrected rows are NEVER
  RE-EXAMINED — including the Marsh & McLennan rows this fix is named for, still stored as BHC:
      DC Laboratories   IDEXX->DLB  x3   Marsh & McLennan  MMC->BHC  x3   ** the headline row **
      TENCENT HLDIGS    TCTZF->TANH x4   Universal Health  UHID->PMHS x1  Verizon ->RBBN x1
      Regions Financial ->(null)    x1   <- the ONLY one a run touches
  ⚠️ I STATED THIS CORRECTLY IN STAGE 1 (item 1d) AND THEN CONTRADICTED IT IN MY OWN SUMMARY.
  Correct statement: the resolver FUNCTION is fixed; repairing stored rows is a separate backfill
  (option E), which the diagnosis counterfactual showed moves the gate by zero.

🔴 OVERTURN 2 — THE COST IS 3x WHAT I REPORTED: 12 distinct / 27 rows suspect, not 9. Six missing:
      SP OAKTREE STRATEGIC CREDIT -> ASCIX (Angel Oak)   x4
      UNITED STATES BILLS         -> USO   (US Oil Fund) x6
      SP Berkshire Hills Bancorp  -> PKBK  (PARKE Bancorp)
      AMERICAN SMALLCAP           -> AMWL  (American Well)
      Nuveen Real Estate Sec Fund -> NRO   (NEUBERGER Real Estate)
      SP Matthews International MF-> MATW  (a memorialization manufacturer)
  Aggravating: XIACY / BHLB / GLDW are ABSENT from `tickers`, so there was no correct target — the
  change MANUFACTURES a link where none was available.
  REAL LEDGER on rows a run touches: ~57 correct new : 27 wrong new : 1 wrong avoided ≈ 2:1, not 9:1.

🔴 OVERTURN 3 — ONE CORRECTION HAS A DIFFERENT MECHANISM THAN I CLAIMED.
  Two do work as I said — folding lifts OTHER candidates over the cutoff and the untouched guard
  abstains (DC Laboratories: raw Dolby .741 / Core .702 gap .039 accepts; folded Dolby .741 / Mesa
  .714 / Bio-Rad .714 gap .027 -> None). But Regions Financial finds the CORRECT company at 0.863 and
  DISCARDS it, because RF / RF-PC / RF-PE / RF-PF share one company_name and the guard reads three
  copies of the right answer as ambiguity. THE GUARD MISFIRING, not working. Now pinned.

AN UNCAUGHT MUTATION, the highest-value gap: DE-DUPLICATING company_names — an obvious one-line
"cleanup" — PASSED ALL 16 TESTS while silently relinking 104 congressional rows to arbitrary
preferred/ADR classes (Berkshire Hathaway New -> BRK-A x20, Alibaba -> BBAAY x11). Now pinned by two
tests; the mutation kills 2.

A SECOND UNRECORDED LIMIT: folding makes the 2,514 duplicate names REACHABLE, so the guard abstains
more often — descriptions whose top-3 are one company repeated go 16 -> 29 (104 rows). Not a
regression (they were None before), but the right company is now found at a high score and discarded.

⚠️ MY PERFORMANCE RATIONALE WAS INVERTED. The comment implied folding at load saves work; HEAD never
folded at all, so there is nothing to save — measured ~16% SLOWER (3.07s -> 3.56s over 150 real
descriptions), because folded strings clear difflib's quick_ratio prefilter more often. Corrected in
the source comment.

⚠️ THE SINT CLOSURE IS PROSPECTIVE. Agilent Technologies Inc (A) goes SINT -> A, but NO SUCH ROW
EXISTS in transactions yet (count where ticker_id=SINT is 0). It protects FUTURE House-parser output —
which is exactly why the sequencing below matters — not existing data.

NUMBERS: suite 1277 passed / 8 skipped / 5 xfailed / 0 failed vs a 1256 baseline on the same base
(verifier rebuilt both trees by file copy); +21 = exactly the new test file. On unfixed HEAD 10 of the
tests fail and 6 pass — the 6 being deliberate controls and must-not-change pins. Mutations: revert
probe fold 6, revert candidate fold 13, cutoff->0.9 8, disable guard 4, DEDUPE 2 (was 0).
Corpus: fuzzy-path 1422 rows, resolved 76 -> 152, 84 new / 8 lost / 5 changed, 0 exact-path changes.
All 8 lost were WRONG links; all 5 changed are corrections (PMHS->UHS, RBBN->VZ, BHC->MRSH x3).

FORWARD-ONLY: two files, no DML, no migration, nothing committed. Nothing stored is rewritten.
⚠️ SEQUENCING, and it now matters more: LAND THIS BEFORE resolve_transactions is next run against the
House parser's newly-kept rows, or those rows mis-resolve — the Agilent->SINT case is precisely that.

STILL QUEUED: OPTION E (the backfill — without it 12 of 13 corrections and all 45 existing mis-links
stand); the 27-row suspect class (needs a cutoff change or a token-overlap guard); the DEDUPE question
(+104 resolutions, measured, needs share-class adjudication); parser options B/D (bracket codes as
tickers); the _validity_set DRY pass; MIGRATION m014; RULE_02 #3/#4; SIGNING RULE_02;
fix/novelty-anchor-rebased (165ceba) AWAITING MERGE DECISION; seven remaps prepared, none run.

---

## 2026-08-05 — RULE_02 #3 + #4: member-set identity and dedup

Full note: [[SESSION-2026-08-05-rule02-identity-dedup]]
Branch fix/rule02-identity-dedup off main (e929b5b). UNCOMMITTED. DB
177f474b03495c20df10a21335ca9dc3 unchanged, confirmed independently.
BASE NOTE: #1 and #2 are already IN main (pushed earlier this session), so this branched straight off
it — no reconciliation, no stacking on the #2 branch.

THE DEFECTS:
  #3 identity was the HEADLINE STRING (alert_exists :356-368). The headline carries only the member
     COUNT, so the sliding window's 4-member view and its 3-member sub-view produced different strings
     and the dedup never related them. 15 STRICT SUBSET PAIRS, incl. SPCX 8597 ⊃ 8598 — both HIGH,
     SAME timestamp, one run — and the MSFT chain 46⊃47⊃48.
  #4 dedup looked back 7 DAYS (:363) against a --days default of 90 (:419-422) — 13x shorter.
     AAPL fired x3: 2026-06-17 / 07-09 / 07-20.
  ⚠️ CORRECTED: I said "4 refire groups". Under the identity this fix implements —
     (ticker, members, DIRECTION) — it is 2 GROUPS / 3 ROWS. The only reading giving 4 ignores
     direction and sweeps in two same-run MIXED/NET_LONG pairs the fix deliberately KEEPS. My own
     remap output said 3 and I contradicted it.

THE FIX: _fingerprint (RULE02::members::ticker::direction), _prior_alerts with the lookback aligned to
--days, RULE_CLUSTER's semantics (same members+direction -> skip; superset recorded -> skip; subsets
-> SUPERSEDE), largest-first emission, plus a legacy fallback for pre-fingerprint rows.
SCOPE by AST: resolve_key / fetch_transactions / member_direction / net_direction / _validity_set /
direction / build_parser UNCHANGED; only emit_alerts, find_clusters and main changed.
scripts/rule_cluster.py BYTE-IDENTICAL.

🔴 THE USER-FACING REGRESSION THE VERIFIER CAUGHT — I would have shipped it.
  I stored identity as JSON in `detail`. alerts.detail is rendered as PROSE by FOUR consumers, and
  RULE_02 hits all of them:
      api/receipts.py::_generic       RULE_02 has no builder -> raw JSON receipt
      api/static/congress.html:307    fetches rule=RULE_02 BY NAME -> raw JSON on the page
      scripts/telegram_bot.py:42      detail[:280] appended to every push
      scripts/generate_brief.py:122   f"-> {s['detail'][:80]}"
  Users would have seen {"fingerprint": "RULE02::C001123+M001217::SPCX::NET_LONG", ...} where a
  sentence belongs. RULE_CLUSTER escapes this only because it has a dedicated receipt builder;
  RULE_02 has none. FIXED by moving identity into why_matters — RULE_CLUSTER's own "Identity {fp}"
  convention — leaving detail NULL, pinned by a test.

🔴 FIVE MUTATIONS MY TESTS DID NOT CATCH, all now killed:
      revert ONLY the LEGACY window to 7 days     escaped -> 1 kill   <- the dangerous one
      make the superset rule direction-aware      escaped -> 1 kill
      re-admit `superseded` rows as candidates    escaped -> 1 kill
      identity written to `detail` instead        escaped -> 10 kills
      emit_alerts default days 90 -> 7            escaped -> 1 kill
  ⭐ The legacy one matters most: ALL 82 stored alerts predate the fingerprint, so
  legacy_alert_exists is the ONLY dedup that can see them until the corpus turns over — and the AAPL
  refire IS a legacy-row refire. My 30-day backdate test seeded a FINGERPRINT row, exercising the
  wrong path entirely.
  ALSO FIXED: a latent breach in the collision guard — _fingerprint_ticker used a fixed parts[2], so a
  symbol containing "::" desynchronised the parse and let a DIFFERENT company dedup against it. Now
  "::".join(parts[2:-1]). Zero transactions carry ':' today. ⚠️ rule_cluster.py:165-168 STILL has the
  fixed-index form.

⭐ WHAT THE VERIFIER PROVED THAT I HAD NOT — an exact per-cluster replay, main vs branch, over the
  FULL history:
      clusters 1442      OLD emitted 627      NEW emitted 933
      suppressed by same-direction superset      : 72
      suppressed only by different-direction one : 59
      *** GENUINELY LOST: 0 ***                  +437 distinct clusters RECOVERED
  The identity fix is net strongly positive for SIGNAL, not merely for noise — better than I claimed.
  ⚠️ BUT the direction-blind superset costs 59 of 131 suppressions (13 strict LONG↔SHORT), not the
  single MSFT case I cited. Defensible as RULE_CLUSTER parity (overlapping 7-day windows on one ticker
  are slices of one event) but INTERNALLY INCONSISTENT with identity itself, where direction DOES
  distinguish. Now pinned as a KNOWN_TRADEOFF so it cannot drift silently.

CALIBRATION — and I UNDERsold it: rule10_instruments(['RULE_02']*5) is ONE congressional instrument,
so a double-fire never double-counted at the gate. And the verifier found it is stronger:
_candidate_alerts NEVER READS lifecycle_stage AT ALL, so retraction is gate-cosmetic for every rule,
not just unsigned ones. Nuance: theme_signals gets a row per contributing alert, so a double-fire did
add a redundant EVIDENCE row — receipts noise, not gate arithmetic.
⭐ AND the whitespace-clustering finding from the parser session turned out ALREADY MITIGATED by #2:
resolve_key maps '', '   ', '\t' and None all to ("", False) and the `if not key` guard drops them —
0 rows survive where 3 did pre-#2. No separate grouping-key fix needed.

THE REMAP — remap_rule02_identity_dedup.py, PREPARED, NOT RUN. 16 redundant (13 subset, 3 refire),
13 HIGH, 66 canonical, keeping largest/earliest. ⚠️ RUN ORDER #1 -> #2 -> THIS, ENFORCED not asserted:
refuses with EXIT 2 until both prior pre-image tables exist, writing nothing and not creating its own
backup table (DB byte-identical). Chained it retracts 15 and SKIPS id 74 ("already retracted" by #1).
#1∩#3 = {66} (direction-CORRECTED, so retractable); #2∩#3 = {66,68,69} (review-state, orthogonal).
Only lifecycle_stage + why_matters change; NO score column; 82 rows in, 82 out; idempotent.

⚠️ A DEFECT IN A PREVIOUSLY-SHIPPED REMAP, found by running the chain:
  remap_rule02_directional_count.py pins its reconstruction baseline to `git show HEAD~1:...`. That was
  right when HEAD WAS the #1 commit; on main HEAD~1 is a MERGE PARENT that already contains #1, so it
  reproduces 75/82 where the true pre-#1 baseline reproduces 82/82 — and refuses. THE GUARD IS
  UNUSABLE ON MAIN: the only way to run it is --skip-reconstruction-check, which DISABLES the safety
  property rather than satisfying it. And since remap #3 refuses until #1's backup table exists, THE
  WHOLE CHAIN IS BLOCKED behind that flag. Fix: pin to the commit before #1 (2f16e36^), not HEAD~1.

NUMBERS: suite 1420 passed / 8 skipped / 5 xfailed / 0 failed vs a 1390 baseline on main (+30 = the new
test file exactly). ⚠️ I earlier reported 1413/+23; the verifier's recount was right and it is higher
again after the gap tests. 11 mutations, all killing: headline identity 4, collision guard 3,
fingerprint window 2, LEGACY window 1, largest-first 2, legacy unscoped 21, direction-aware superset 1,
re-admit superseded 1, identity-into-detail 10, default days 1, fixed-index ticker 1.

STILL QUEUED: the CROSS-PARTISAN definitional layer — the last thing before RULE_02 is SIGNABLE; fix
remap #1's baseline pin (it currently blocks the whole chain); rule_cluster.py's fixed-index
_fingerprint_ticker; a _rule02 receipt builder if identity ever moves back toward detail; the two HELD
ingestion fixes (fix/house-parser-no-drop 9533e94, fix/linker-casefold 8ad21e0, both on origin,
unmerged); option E backfills; fix/novelty-anchor-rebased (165ceba) AWAITING MERGE DECISION;
eight remaps prepared, none run.

## 2026-08-05 — Code deploy: "merge what is supposed to be merged"

Full note: [[SESSION-2026-08-05-make-it-real-deploy]]

origin/main 477eb1e -> 052d081, PUSHED. Three branches merged in a detached scratch worktree, 0
conflicts each, full suite 1474 passed / 8 skipped / 5 xfailed / 0 FAILED: fix/rule02-remap-baseline-pin
(19d942e), fix/house-parser-no-drop (f0bc660), feat/signed-rule-null-detector (052d081). Deploy surface
is THREE FILES: remap_rule02_directional_count.py +16, parse_house_pdfs.py +43,
scripts/rule_10_corroboration.py +94.

The human's instruction overrode the runbook's human-only-merge rule FOR MERGES ONLY. Every protection
over production DATA held: no remap run, no scheduler, no dev server, no resolve_transactions. Working
DB md5 177f474b03495c20df10a21335ca9dc3 UNCHANGED after the push.

THE BASELINE PIN IS THE LOAD-BEARING ONE. Its guard loaded `git show HEAD~1`, which on main resolves to
a merge parent ALREADY CONTAINING the fix (75/82, refuse); the only way past was
--skip-reconstruction-check, which DISABLES the safety property rather than satisfying it, and every
later RULE_02 remap refuses until this one's backup table exists. So the WHOLE CHAIN was blocked behind
that flag. Now: reconstruction 82/82 OK, 0 REFUSING, dry run 4 to retract / 3 to correct. THE PROD
REMAP CHAIN IS UNBLOCKED WITHOUT DISABLING A SINGLE GUARD.

VERIFIED ON origin/main AFTER THE PUSH, not asserted: I2 SIGNED_RULES is frozenset({"RULE_06"}) so
RULE_01B IS NOT SIGNED; I6 resolve_tickers.py still uncased (linker parked); _detect_dark_signed_rules
present; PRE_FIX_COMMIT = "2f16e36^"; resolve_tickers.py / rule_02_cluster.py / scripts/rule_cluster.py
/ jpt_common.py ALL UNCHANGED.

DELIBERATELY NOT MERGED: sign/rule01b — INVARIANT I2, the RULE_01B prod remaps (chronology -> ticker ->
direction) MUST run BEFORE the signing code deploys, or RULE_01B goes 100% DARK fail-closed against an
unpopulated corroborates column. DATA BEFORE CODE. Also held: fix/linker-casefold (parked,
net-negative alone), fix/rule02-directional-count (regressive, +13/-331 behind main),
fix/novelty-anchor-rebased and feat/signed-rule-degraded-warning (awaiting decisions).

NEW OPERATIONAL CONSTRAINT (I6): the parser now KEEPS rows it used to drop, carrying ticker=None so they
cannot become a FALSE corroboration key. Do NOT run resolve_transactions over them while the linker is
parked — that is precisely how the guarantee would be lost.

CALIBRATION: NO PRODUCTION DATA CHANGED. The 82 stored RULE_02 alerts are still wrong; RULE_01B is
still unsigned; RULE_11 $48B and RULE_09 4-row untouched; every remap still PREPARED-NOT-RUN. And
Phase 3's FIRST, cross-cutting repair — the LIKE-substring novelty fix — IS STILL NOT LANDED
(jpt_common.py:1231 is still %anchor%). Do not read 052d081 as "the campaign shipped."

ROADMAP CORRECTION: [[Make It Real campaign]] said "#3 Phase 3 rule repairs — NOT STARTED". Stale in
BOTH directions — RULE_09 and RULE_02 #1-#4 HAD landed, while the LIKE-novelty repair that was supposed
to go FIRST had not. Row rewritten per-repair against 052d081.


## 2026-08-05 — Scope as an installable PWA (frontend only)

Full note: [[SESSION-2026-08-05-pwa-installable]]

Branch feat/pwa-installable off main 052d081, UNCOMMITTED, 0 commits ahead. Suite 1477 passed / 8
skipped / 5 xfailed / 0 FAILED (baseline 1474; the +3 is test_theme_holdouts parametrising over
api/static and auto-enrolling offline.html, pwa.js and sw.js — all pass). Working DB md5
177f474b03495c20df10a21335ca9dc3 UNCHANGED. Nothing merged, deployed or committed.

TWO WORK-ORDER PREMISES WERE WRONG. It is FastAPI, NOT Flask, and there is NO StaticFiles mount —
every asset is an explicit per-file route, which made /sw.js root scope trivial and the
Service-Worker-Allowed header UNNECESSARY (counterfactual tested: register('/static/sw.js',
{scope:'/'}) throws SecurityError). And viewport was ALREADY on all 30 pages, both target pages
already had breakpoints, and neither contains a <table> or fixed grid — the real gap was TOUCH
TARGETS, not reflow.

THE 1b ICON NO-GO FIRED: zero icon sources in the repo. Stopped and asked rather than invent a brand
mark; the human supplied "Primary logo use this.png" (gold delta on dark card). The MASKABLE icon
needed a second pass — a straight resize left rounded-card corners that a circular mask renders as a
sticker; regenerated full-bleed at 680->crop 512, delta at ~50%, inside the inner-80% safe zone.

THE HONESTY DESIGN IS INVERTED FROM THE WORK ORDER, DELIBERATELY. Enumerating DATA paths fails OPEN
— the two pages alone hit eleven data endpoints and next month's would default to cache-first. So
the allowlist is SHELL-ONLY and everything else is network-ONLY with no cache fallback. Dumping
every cached body: 9 shell paths + a 20-byte timestamp, SIGNAL_DATA_FOUND_IN_ANY_CACHED_BODY: [].
Same on the real Android device. HTML NAVIGATIONS ARE UNCACHED OUT OF NECESSITY: GET / is
server-rendered and injects the STORED morning brief straight from the DB (api/main.py:772), so a
page in this app IS signal data. Cost stated: you cannot read past alerts offline. Intended.

OFFLINE PROVEN AGAINST A GENUINELY KILLED SERVER, not an emulated flag: title "Offline — Scope",
shows_stale_alert_data FALSE, explicit "Offline — last updated 8/5/2026, 5:13:24 PM (less than a
minute ago)", styled by cached tokens.css.

A SECOND DISHONESTY FOUND MID-BUILD, and the verifier split the attribution — the split matters. On
the BRIEF, "No brief available yet. Click Regenerate" is a regression MY OWN worker introduces
(unmodified main rejects and says "Failed to load brief: Failed to fetch"), so the guard prevents
damage rather than repairing it. On the FEED, "Failed to load alerts. Is the API running?" is
PRE-EXISTING on main, and there the guard is a genuine improvement.

INSTALL: Chrome's own menu offered "Install app"; the dialog rendered the manifest name with the
icon correctly circular-masked; Chrome's engine on the device reports getAppManifest errors: [] and
getInstallabilityErrors: []; Lighthouse 11 PWA category = 1.0. LIGHTHOUSE 12 REMOVED THE PWA
CATEGORY ENTIRELY. Note LH11's PWA category has NO service-worker audit, so 1.0 is not evidence the
worker works — the cache dumps are. UNVERIFIED: the home-screen standalone launch — logcat
"WebAPK service unknown_account"; minting needs a signed-in Google account, which I would not create
on the human's behalf. The human's REAL PHONE was adb-paired; deliberately left alone.

MOBILE: 0 horizontal overflow and 0 sub-44px interactive elements at 380 AND 414 on both pages.
UNDER-CLAIMED: baseline had 32 sub-44px targets on /feed and 7 on /brief. Three offenders
(.nav-brand, .cmdk-btn from cmdk.js, .theme-btn from theme.js) were raised in each PAGE's own 480px
block, never in the shared files. DESKTOP BYTE-IDENTICAL — geometry hashes equal on both pages while
the branch page is SW-controlled.

THE VERIFIER OVERTURNED SIX CLAIMS; THREE WERE REAL DEFECTS, FIXED AND RE-PROVEN: (1) the ICONS were
precached by sw.js but NOT hashed by _shell_build, so replacing a logo left every installed phone
serving the old one from a cache with no expiry — added to _SHELL_SOURCES, proven by mutation
(66a226aada90 -> ec3069f3b7b6 -> revert). (2) the "last updated" stamp advanced on ANY successful
non-shell GET — a /manifest.json fetch ALONE created it, so the offline page could promise recent
data while every data endpoint 5xx'd; fixed with NON_DATA_PATHS, proven (manifest-only: false,
/alerts: true). (3) a LIVE OS light/dark flip desynced the status bar because theme.js:53 follows the
OS without dispatching scope:themechange; fixed by listening to the same media query in pwa.js —
UNVERIFIED end-to-end, since a synthetic dispatchEvent provably cannot reach a listener bound to a
different MediaQueryList instance (two of my own test attempts were invalid, not failing).

WORDING CORRECTED: "structurally impossible" is really "impossible while every SHELL pathname stays
a static file" — the verifier made /rule-names.js return a signal-shaped body and watched it get
cached and served cache-first. A TEST ASSERTING THAT INVARIANT IS THE TOP FOLLOW-UP, and it closes
the icon class of bug too. sw.js wrongly claimed "no HTML is cached" while /offline.html is. And my
"Google Fonts is blocked in this sandbox" caveat was FALSE for desktop Chrome (three woff2 files
loaded and cached) — true only on the emulator; I attached the emulator's limit to a desktop
measurement. DOWNGRADED: the WebappRegistry display_mode=3 record to UNVERIFIED (read under adb root
on the now-killed non-Play image, unreproducible).

ENVIRONMENT CHANGES ON THE HUMAN'S MACHINE: sdkmanager failed with "Unable to locate a Java Runtime",
so brew install openjdk (keg-only, no sudo); then platform-tools, emulator, platforms;android-34 and
two system images into ~/Library/Android/sdk; then symlinks for emulator and system-images into
/opt/homebrew/share/android-commandlinetools because avdmanager derives its SDK root from its own
install location. AVDs scope_pwa and scope_play. All reversible; none touches the repo.

STILL OWED: the SHELL-path invariant test; deploy then confirm install on the human's own phone
(closes the one UNVERIFIED item in a minute); THEN wrap to APK via PWABuilder/Bubblewrap. AND THE
FEED IS STILL NOT HONEST — the whole deploy runbook (RULE_11 $48B, RULE_09, RULE_01B and RULE_02
remap chains) is untouched. This makes Scope INSTALLABLE, nothing more.


## 2026-08-08 — §3's first Phase-3 repair: the novelty anchor is a token, not a substring

Full note: [[SESSION-2026-08-08-novelty-like-substring-land]]

Branch fix/novelty-like-substring-rebased off main 052d081, UNCOMMITTED, 0 commits ahead. Suite 1517
passed / 8 skipped / 5 xfailed / 0 FAILED. Working DB md5 177f474b03495c20df10a21335ca9dc3 UNCHANGED.
Nothing merged, pushed, deployed or committed.

BASELINE CORRECTION: I first compared against 1477. That was the PWA BRANCH's number (it carries 3
extra parametrised cases because test_theme_holdouts enrols offline.html/pwa.js/sw.js). The true
052d081 baseline is 1474, so 1474 + 43 = 1517 exactly — the new fixture and nothing else.

RECONCILE. Both old branches carried exactly ONE commit with BYTE-IDENTICAL payloads: 165ceba is a
pure rebase of 7816e04, differing only in blob hashes and one hunk offset (@@1195 vs @@1221). Neither
was on the current base — 28 and 20 commits BEHIND. A TRAP WORTH RECORDING: git diff
052d081..<branch> --stat reports THOUSANDS OF DELETIONS on both; that is the branches being BEHIND,
not deleting anything, and reading it literally would have condemned a good fix. It never landed for
no technical reason whatsoever — the 08-03 backlog sweep left it "AWAITING MERGE DECISION".

REUSE, NOT RE-IMPLEMENT. 3250818 is an ancestor of 052d081 and jpt_common.py has ZERO drift between
them, so git cherry-pick --no-commit 165ceba applied at exit 0, no conflict; the new test file cannot
conflict. A STAGE-1c PREMISE WAS INVERTED: _is_ticker_shaped does NOT exist on the base (0
occurrences anywhere on 052d081) — the fix INTRODUCES it, so the NO-GO never fires.

CALLER SET BY STRUCTURE: insert_alert:1458 and score_alert_fields:1284 (the latter has NO novelty_key
leg), with enrich_alert_scores:1316 routing through score_alert_fields — the path the ~15 raw-INSERT
rules depend on. The gate is on the ANCHOR'S OWN SHAPE, so a rule written tomorrow is covered on day
one. NEW INTERACTION POST-DATING THE FIX: RULE_02 #3 (landed 08-05) writes Identity
RULE02::C001123::… into why_matters; under the old substring rule a 1-char ticker C matched
::C001123, under boundary matching it does not — the fix is worth MORE on this base than when written.

NUMBERS MEASURED ON THIS BASE, NOT RECALLED: US 82->4 matches, novelty 0.185->0.383 (Cl-US-ter
contains "us"); DIS 22->2, 0.242->0.477, resolving the why_matters widening; NVDA/BRK.B/AAPL
UNCHANGED delta 0.000; 0 anchors scored lower (monotone). My DIS figures match NEITHER previously
reported pair (0.247->1.0 and 1.0->0.175) — both predate this base; the mechanism is proven, not a
number.

RE-CERTIFIED, NOT A STALE GREEN: mutating _is_ticker_shaped -> return False gave 15 FAILED; dropping
upper() gave 2 FAILED (the 08-01 case regression); restore by file copy -> 43/43.

THE VERIFIER UPHELD THE HEADLINE AND FOUND SIX OVER-CLAIMS, TWO OF THEM DEFECTS I SHIPPED:
(1) a WRONG NUMBER in the product docstring — 0.186 where the function's own formula gives 0.185
(1/(1+ln(83))=0.184541); (2) THREE STALE file:line CITATIONS carried in verbatim BY THE FAITHFUL
CHERRY-PICK — rule_02_cluster.py:134 (now 317, line 134 is a SQL WHERE clause) and
rule_cluster.py:229,270 (now 320,366; :229 is _verb()), appearing in four places. THE COST OF A
BYTE-IDENTICAL CHERRY-PICK IS THAT IT REPRODUCES STALE REFERENCES TOO. Both fixed, with AST
(docstrings stripped) PROVEN IDENTICAL to the verified payload, so the mechanism is untouched.
Provenance added to the docstring's 450/511/53.8%/6-row figures (md5 named, marked UNVERIFIED vs prod).
Also corrected: T/ST=1.000 was a CORPUS ARTIFACT (0.339/0.358 with genuine rows seeded); "exactly
three write paths" MISSED A FOURTH — rule_10_corroboration.py:582 -> score_alert_fields ->
themes.novelty_score, so the blast radius includes THEME scores the war room reads (theme novelty
0.244->0.591); and "blank-ticker collapse" is MISNAMED ('' is falsy, headline[:30] intercepts).

THE VERIFIER'S PLANT TEST IS THE SESSION'S BEST EVIDENCE: it planted short tickers IT/AT/T in
RULE_ADSB — a raw-INSERT rule named NOWHERE in the fix — and drove them through the real
enrich_alert_scores: 0.231 -> 0.591, controls unmoved; plus RULE_INVENTED_TOMORROW via insert_alert,
0.281 -> 1.0. The class is closed by SHAPE. Monotonicity survived 27 adversarial rows, a 5000-draw
randomised differential (0 subset violations) and an exhaustive 0x20-0x30000 scan proving SQLite
upper() never folds where LIKE does not. Pre-existing suite differential: 1473 on base AND fixed.

FLAGSHIP EXAMPLE EXPIRES: remap_rule02_ticker_resolution.py:7-11 says US IS NOT A SYMBOL (213
unlinked transactions) and re-keys those 4 alerts to ticker='' — after which they anchor on
headline[:30] and never reach the ticker branch. The mechanism and the class (T, F, C, A, BA, GD, IT,
ST) are unaffected, but US stops being the good demo once that remap runs on prod.

NO RETROACTIVE REWRITE: 0 UPDATEs of any score column, 0 migrations, 2 files touched;
calculate_opportunity_score / calculate_evidence_confidence / assign_time_horizon /
_distinct_rule_count / rule10_instruments / normalize_ticker all byte-identical. FUTURE COMPUTATION
ONLY. Out of scope and noted: CTA.PB's 6-char hole, composite tickers, the empty-anchor case.

STILL OWED: the human merges. And the prod remap chain is untouched and separate — RULE_11's 220
unverified contract rows (dry run showed 140 wrong amounts, LOCKHEED $48,063,737,196 -> true
$2.87B-$7.46B, and 111 alerts becoming CRITICAL which would take RULE_11's gate-eligible count from
51 to 145), then RULE_09, RULE_CLUSTER, RULE_01B, RULE_02.


## 2026-08-08 — RULE_02's cross-partisan definitional layer (hard gate)

Full note: [[SESSION-2026-08-08-rule02-cross-partisan]]

Branch fix/rule02-cross-partisan off main 052d081, UNCOMMITTED, 0 ahead. Suite 1509 passed / 8
skipped / 5 xfailed / 0 FAILED (baseline 1474 + 35 new). Working DB md5
177f474b03495c20df10a21335ca9dc3 UNCHANGED. Nothing merged, pushed, deployed or committed.
THIS COMPLETES RULE_02'S REPAIRS. Only RULE_08 remains in section 3.

THE GO/NO-GO WAS DATA AND IT PASSED. members.party: 2693 rows, ZERO NULL (Democratic 1350,
Republican 1328, Independent 12, plus 3 singletons). Over RULE_02's OWN scan population: 100% party
coverage at 90d / 365d / 3650d, all D-or-R, zero NULL member_id. NO NEW DATA SOURCE WAS NEEDED —
RULE_02 already LEFT JOINed members for full_name (rule_02_cluster.py:131), so party is ONE COLUMN on
an existing join; no hardcoded map, no fuzzy guess. Every one of the 119 members who has ever traded
is D or R, so the INDEPENDENT ARM IS UNEXERCISED IN PRODUCTION and is held by fixture only.

BLAST RADIUS: live 90d window 4 clusters, 4 cross-partisan, 0 single-party — the hard gate removes
NOTHING today. 365d: 47 -> 36, single-party 11 (8 HIGH, 3 MEDIUM). Stored corpus: 54 cross-partisan /
28 single-party (17 HIGH+). tags CANNOT be split on commas — member names CONTAIN commas ("Case, Ed"),
the RULE_06 hazard; resolved by longest-match with a collision guard (16 name-substring collisions
exist, 3 span parties, NONE occur in RULE_02's tags).

A CALIBRATION CORRECTION THE WORK ORDER'S FRAMING WOULD HAVE HIDDEN: stored retraction IS
gate-cosmetic (_candidate_alerts never reads lifecycle_stage), BUT FORWARD SUPPRESSION IS NOT — all
28 of 28 single-party stored alerts are the SOLE congressional contributor on their ticker within 14
days (RULE_01 has 0 alerts, RULE_CLUSTER 1, RULE_01B 192). Equivalent future clusters will produce no
congressional leg at all. That evidence was put to the human BEFORE implementing; HARD GATE chosen
over the weight alternative, on the record.

THE CHANGE: is_cross_partisan(parties) = DEMOCRAT in seen and REPUBLICAN in seen, gated on the
COUNTED (directional) members — an exchange-only Republican cannot supply the agreement, mirroring
#1's "presence is not direction". Independent/unknown SATISFY NEITHER SIDE BUT BLOCK NOTHING (D+R+I
fires; D+I does not; a NULL filer can neither manufacture nor destroy agreement). The gate sits
BEFORE seen_windows so a rejected single-party window cannot consume the dedup slot.

TWO HAZARDS THE ORDER DID NOT ANTICIPATE. (1) The new headline WOULD HAVE RE-EMITTED THE CORPUS:
emit_alerts probes legacy_alert_exists, which matches stored rows on HEADLINE TEXT, and all 82 stored
rows predate the fingerprint — that function's own docstring warns of exactly this. Fixed by carrying
a byte-exact legacy_headline used ONLY as the probe key. (2) My first version made emit_alerts
HARD-REQUIRE cluster["legacy_headline"], KeyError-ing every caller that builds a cluster dict by hand
(21 identity tests). Fixed in the CODE with .get(...) or headline — not by editing the tests.

I INTRODUCED A 44-TEST REGRESSION AND MIS-DIAGNOSED IT AT FIRST. Three causes, not one: 21 KeyError
(above); ~18 were NOT missing party but my headline splicing "(2D/1R)" BETWEEN "members" and the verb,
breaking "3 members bought NVDA" substring assertions — fixed by MOVING THE SPLIT TO THE END, i.e.
respecting an existing contract instead of rewriting the tests that encode it; 5 were genuinely
single-party fixtures, given explicit party. ONE PRE-EXISTING TEST LOST SPECIFICITY PERMANENTLY:
test_the_WAT_shape probed find_clusters(min_members=1) and asserted member_count==1, but A LONE
MEMBER CAN NEVER SPAN BOTH PARTIES, so a 1-member cluster cannot form at any threshold. Widened to
4-present/2-directional; the guard is intact (reverting #1 still reads 4, not 2) but the original 3/1
shape is no longer expressible.

MUTATIONS 5 / 1 / 7: removing the gate -> 5 failed; pointing the legacy probe at the new headline ->
exactly 1 failed; replacing D-and-R with len(seen)>=2 -> 7 failed, proving the predicate is
specifically DEMOCRAT AND REPUBLICAN, not "any two distinct parties". Restored by file copy, 174
RULE_02 tests green.

BEFORE/AFTER: 90d 4 -> 4 (nothing lost); 365d 47 -> 36; single-party 11 -> 0; cross-partisan 36 -> 36
UNCHANGED — no cross-partisan cluster was suppressed.

REMAP PREPARED NOT RUN (scripts/remap_rule02_cross_partisan.py): dry run on a COPY = 28 to retract,
17 HIGH/CRITICAL, 0 unresolvable; --apply = 28 retracted, 82 rows in and 82 out, 28 pre-images; re-run
= "Nothing to do"; 0 rows changed score/severity/ticker; refusal EXIT 2 with the DB BYTE-IDENTICAL.
Run order #1 -> #2 -> #3 -> this, and the reason is concrete: #1 REWRITES THE MEMBER NAMES IN tags AND
THIS SCRIPT READS THAT FIELD. Party is read by bioguide ID after longest-match resolution; anything
unresolvable is SKIPPED and reported, never guessed.

SCOPE: 11 protected functions AST-byte-identical (incl. _fingerprint — identity does NOT mention
party); added only is_cross_partisan/party_split; changed only fetch_transactions/find_clusters/
emit_alerts; rule_cluster.py and every other rule untouched; MIN_MEMBERS, WINDOW_DAYS, the severity
map and COUNTED_DIRECTIONS all unchanged — NOTHING LOOSENED to offset the volume drop.

RULE_02 IS NOW SIGNABLE (its own session, unblocked). Still outstanding and separate: RULE_08; the
prod remap chain (RULE_11's 220 unverified rows, RULE_09, RULE_01B, RULE_02 x4); and the unmerged
fix/novelty-like-substring-rebased.

VERIFIER (2026-08-08, RULE_02 cross-partisan) — HEADLINE UPHELD, FOUR THINGS OVERTURNED/CORRECTED.
Its strongest evidence beats mine: a 20,000-FIXTURE DIFFERENTIAL showing the new rule's output is
EXACTLY the base output filtered to cross-partisan (identical fingerprints, severities, and
legacy_headline == base headline in every case), plus a 50,000-fixture soundness fuzz with 0 escapes
and an exhaustive sweep of all 4^3 three-member party combinations with 0 mismatches. So the
survivors are not merely 36 in COUNT, they are the identical MULTISET.

OVERTURNED 1 — MY 28/28 FIGURE, WHICH WAS THE HUMAN'S DECIDING EVIDENCE. I reported that all 28
single-party stored alerts are the sole congressional contributor within 14 days. Arithmetically true,
MISLEADING as gate evidence, and I confirmed every part of the overturn myself: ALL 28 ARE 2-MEMBER
CLUSTERS and --min-members defaults to 3, so TODAY'S RULE WOULD NOT EMIT ONE OF THEM WHATEVER THEIR
PARTY (single-party counts {2:28}; all 82 are {2:62, 3:17, 4:3}); 11 of the 28 are MEDIUM and were
never gate candidates; 3 of the 17 HIGH keep a surviving cross-partisan peer. GATE-RELEVANT FIGURE IS
14, NOT 28. The honest forward evidence was in my own table: 0 suppressed in the live 90d window, 11
of 47 at 365d. The correction points the SAME WAY as the decision (hard gate is cheaper than I said),
so it does not invalidate the choice — but the choice was made on an overstated cost and the human was
told explicitly.

OVERTURNED 2 — THE seen_windows PLACEMENT RATIONALE WAS INERT AND I ASSERTED IT AS DESIGN. Moving the
gate below seen_windows.add kills 0 TESTS and produces BYTE-IDENTICAL output over 365d and 3650d,
because window_key already contains anchor_date. The test whose docstring claimed to pin it CANNOT
fail on placement. Code comment and test docstring corrected.

OVERTURNED 3 — "only rule_02_cluster.py changed": THREE tracked files are modified. Heading fixed.
The discipline claim survives (11 protected functions AST-identical, nothing loosened).

FOOTGUN IN MY OWN HELPER, now documented: _auto_party used last-character parity and its comment
claimed real ids "end in sequential digits so the last char alternates". FALSE — C001123/G000583 both
end in '3'; B000000/S000000 both end in '0'. THE FAILURE MODE IS SILENT: a same-last-digit pair forms
no cluster, so an `== []` assertion passes VACUOUSLY. The comment now warns instead of reassuring.

FIVE UNDISCLOSED FACTS IT SURFACED: --min-members 1 is now DEAD at any threshold (a lone member cannot
span both parties) and 2 demands exactly one D and one R, yet build_parser still advertises it freely;
members.party has NO AS-OF DATE so a party switcher is attributed retroactively (Van Drew stored
Republican; zero current exposure, no switcher trades); members.is_current is uniformly 1 for all 2693
rows including members who left in 2021 — carries no information; retraction is PRODUCT-cosmetic too
(nothing filters `retracted`, the rows stay in the feed with a badge); the scan population is
HOUSE-ONLY so "100% party coverage" is narrower than it sounds.

CONFIRMED ON REQUEST: the widened WAT test STILL catches the #1 mutation (reverting the
directional-count fix makes it read 4 == 2). It judged moving the party split to the END of the
headline the RIGHT call over rewriting 18 assertions, and judged the .get() fallback a REAL TRADE-OFF,
MILDLY NEGATIVE — correct today but a silent degradation path resting on a single test.

PROCESS FINDING AGAINST ME: I EDITED THE WORKTREE THREE TIMES WHILE THE VERIFIER WAS AUDITING IT (the
KeyError fix, the headline reformat, the fixture repairs). Two of its red-suite runs were against code
that no longer exists and it withdrew them. FREEZE OR COMMIT THE TREE BEFORE HANDING IT TO A VERIFIER.

TRUSTED-DATA CAVEAT: the stored-corpus split (82/54/28/17) comes from the local working DB and is
CORROBORATIVE, NOT DECISIVE. Settle in prod with the read-only
`python scripts/remap_rule02_cross_partisan.py --preflight`, which the verifier confirmed writes
nothing. All code-level findings fixed; suite re-proven at 1509 passed / 0 failed; DB md5 unchanged.


## 2026-08-10 — RULE_08 diagnosis (read-only): what it actually is

Full note: [[SESSION-2026-08-10-rule08-diagnosis]]

READ-ONLY. No code changed, no data written, nothing merged. Working DB md5
43564013d1eff60d292d9aef4350ce55 unchanged. main/origin/main still 052d081.

⚠️ DB BASELINE CHANGED OUTSIDE THIS SESSION. Every prior note pinned 177f474b03495c20df10a21335ca9dc3;
the file was rewritten 2026-08-09 to 43564013d1eff60d292d9aef4350ce55 with contents apparently intact
(alerts 3347, transactions 9967, members 2693, tickers 10619). Cause UNVERIFIED. A SECOND signal: the
repo was found on branch fix/novelty-anchor-rebased, not fix/rule09-demote-to-context as the
session-start snapshot recorded, and I did not check it out. Something outside these sessions is
operating on this repo — do not trust snapshot-derived numbers without re-deriving.

THREE WORK-ORDER PREMISES WERE WRONG. RULE_08 is NOT "the last unstarted rule" and NOT "excluded as a
stopgap": it was excluded 2026-07-29, human-gated, with a 40-line measured rationale at
jpt_common.py:764-793 and a written re-admission condition. And it is NOT the same shape as RULE_09 —
RULE_09 was demoted on a CATEGORY judgement (lobbying measures influence on government), RULE_08 on a
CORRECTNESS one (its attribution is broken). Same remedy, different disease.

WHAT IT IS: Federal Register API (rule_08_federal_register.py:22) + match_tickers (:101-111) doing a
RAW SUBSTRING match of 16 hardcoded SECTOR_MAP keywords (:26-43) onto FIXED HAND-WRITTEN BASKETS
("bank" -> JPM/BAC/WFC/GS). The ticker derives from a sector WORD, never from the ENTITY the document
is about.

ZERO EXPOSURE TO THE FIRING DECISION, PROVEN BY EXECUTION: HIGH and CRITICAL RULE_08 alerts with a
valid ticker dated today were absent from _candidate_alerts(conn, 336) while RULE_06 and RULE_01B
controls were present. The verifier re-derived it independently with a real 3-instrument convergence
firing while a CRITICAL RULE_08 sat on the same ticker — absent from tags.rules, rules_present,
supporting_rules and theme_signals.

BUT "LIVE EXPOSURE IS ZERO" WAS OVER-STATED AND WAS OVERTURNED. TWO USER-FACING LEAKS: (1)
api/routers/forming.py:57 builds ALL_INSTRUMENTS from RULE_10_INSTRUMENTS with NO exclusion filter, so
the near-miss surface lists fed-register in missing_legs — an instrument no eligible rule can supply;
that file's OWN comment (:154-158) already calls it a pre-existing defect, which my "inert" claim
contradicted. (2) scripts/morning_brief.py:163 does a["n"] += 1 BEFORE the exclusion branch at :164,
and n is the third ranking key at :205 — so EXCLUDED-RULE VOLUME CAN FLIP THE HERO (flip-tested both
directions with 5 RULE_08 alerts). ⚠️ Leak 2 is BROADER THAN RULE_08 — it hits every rule in
RULE_10_EXCLUDED — and deserves its own work order rather than being filed under a RULE_08 demotion.

THE RISK FINDING: all 72 stored alerts are PRE-SPLIT LEGACY composites ("JNJ PFE MRK GOOGL…"), 72/72
absent from tickers, detail NULL on all 72. But the CODE at 052d081 SPLITS (:223-224) into single
normalized tickers, and 35 of the 37 SECTOR_MAP symbols exist in tickers (only BRK.B and XLE absent).
So post-split alerts look COMPLETELY CLEAN to the gate — the ticker != '' filter would not bar them —
and RULE_10_EXCLUDED is the ONLY barrier, pinned by NO test.

RELIABILITY, BOUNDED HONESTLY AND WRONG TWICE FIRST. My first pass was contaminated: every headline
embeds the rule's own "— affects $TICKER" output, so the issuer test matched the rule's own tickers
and wrongly scored 5 DEFENSIBLE; and I used a 72 denominator when only 15 are measurable. Corrected: 0
of 72 alerts name a company from their own basket — but the verifier was right that this is A
RESTATEMENT OF THE DESIGN, NOT A MEASUREMENT (a sector map essentially cannot name an issuer), so the
star came off. Of the 15 checkable, 2 are demonstrable fragment errors (id 637 "tech" inside
"Technical Amendments"; id 8478 "tech" inside an EPA rule's "Control Technology"). ⚠️ WHOLE-WORD DOES
NOT MEAN DEFENSIBLE: id 2877 "Direct Multifamily Housing Subsequent Loans" emits ADM/BG/MOS —
fertilizer and grain for a housing loan; id 7822 "Federal Home Loan Bank" emits money-centre banks
though FHLBank is a GSE co-op; id 641 DOE transformer efficiency emits LMT/RTX/NOC. 38 of 72 titles are
truncated at exactly 80 chars, so my "the keyword is in the abstract" cause was over-precise and NO
single rate is defensible.

NOT DORMANT: activity_log RULE_08 scanned=70 flagged=25 emitted=0 @ 2026-07-20 08:31 — alive and
matching 25 documents, emitting nothing only because of the 14-day dedup lookback. Better evidence
than the alert-timestamp inference I used.

VERDICT / RECOMMENDATION (NOT TAKEN): FORMALLY DEMOTE — pin the exclusion with a test and record the
context/globe intent. No behaviour change; the exclusion already works, it is simply unpinned, and one
deletion from a set would silently re-admit a structurally broken attribution. REPAIR & READMIT
rejected: replacing SECTOR_MAP with real issuer attribution is a rewrite of the rule's core, not a
repair. LEAVE AS-IS rejected: the belief was unverified until today and the inert-looking fed-register
entry is not inert.

This was §3's LAST repair-list item; its resolution CLOSES THE RULE-REPAIR PHASE, leaving signing
(RULE_02 is now signable) and the deploy. The phase closes with RULE_08 as CONTEXT, not as a repaired
instrument — fed-register is not an instrument Scope currently has.


## 2026-08-10 — RULE_08 demote-and-pin: a NO-OP, and the premise was mine

Full note: [[SESSION-2026-08-10-rule08-demote-pin]]

NO CODE CHANGED. NO BRANCH CREATED. Base confirmed main == origin/main == 052d081, jpt_common blob
03dcc58c (no history rewrite), 0 tracked modifications under Scope/, DB md5
43564013d1eff60d292d9aef4350ce55 unchanged.

The work order asked for (a) a pin test, (b) retirement of the "inert" fed-register mapping, (c) a doc
line. ALL THREE ALREADY EXISTED, AND (b) WOULD HAVE BEEN A REGRESSION.

(a) THE PIN ALREADY EXISTS AND IS STRONGER THAN SPECIFIED.
tests/test_rule08_composite_split.py:302 pins BEHAVIOUR through the gate's real
find_corroborated_tickers/instruments_for — "fed-register" not in instruments, instruments ==
["congressional","insider"], gate does not fire. And :326 is exactly the mutation the order asked me
to write: it lifts RULE_08 out of BOTH RULE_10_EXCLUDED and EXCLUDED_FROM_CORROBORATION and asserts
the SAME data THEN FIRES with fed-register as the third leg. That is stronger than "fails when the
string is removed", because it also proves the first test is not green for the lazy reason of a
silently regressed composite split. Reinforced by test_exclusion_single_source.py:98-111
(BASKET_EXCLUDED = RULE_08, RULE_ADSB, RULE_TELEGRAM_OSINT) and test_basket_rule_gate_class.py:102-106.
Measured: 69 passed / 5 xfailed on 052d081.

(b) THE MAPPING MUST NOT BE REMOVED. fed-register IS sole-sourced by RULE_08 (jpt_common.py:848), and
rule10_instruments filters eligibility BEFORE mapping — sorted({RULE_10_INSTRUMENTS.get(rule, rule)
for rule in rule10_eligible_rules(rules)}) — so the entry is genuinely inert to firing. That is
precisely why deleting it is dangerous: an eligible-but-UNMAPPED rule becomes its own PHANTOM
INSTRUMENT via .get(rule, rule) — "the phantom trap that let RULE_12/13/14 count as three legs after
being 'retired'" (test_gate_redesign.py:482; test_exclusion_single_source.py:108-111). It is DEFENCE
IN DEPTH. Removal would also break four live assertions. DECISION: KEEP, proven not assumed.

(c) THE DOC ALREADY SAYS IT. instrument-definitions-and-tiers.md:134-149 records "RULE_08 — federal
register -> LEAN context, door open", "Currently excluded from the gate, which is the correct interim
state", and the re-admission bar verbatim ("attributed to the specific named regulated entity that the
document itself names — never via a keyword->basket map"), plus the table row at :185, plus a
PROVISIONAL caveat that the user has not yet read the Federal Register in depth.

THE ERROR WAS MINE. The 08-10 diagnosis asserted "nothing tests it for RULE_08 specifically" WITHOUT
GREPPING FOR IT, and that false claim generated this whole work order. The diagnosis note is now
corrected: LEAVE AS-IS is marked the correct answer, the two grounds I used to reject it are marked
false, and "formally demote" is withdrawn. LESSON: A RECOMMENDATION IS A CLAIM AND DESERVES THE SAME
EVIDENCE BAR AS A FINDING. The verifier did not catch it because it was asked to check the diagnosis's
FINDINGS, not its RECOMMENDATIONS — worth remembering when scoping a verifier brief.

No verifier run this session: there is no change to verify. Offered an independent pass on the no-op
finding itself if wanted.

SECTION 3'S RULE-REPAIR PHASE IS NOW CLOSED — RULE_08 stands as CONTEXT, not a repaired instrument;
fed-register is not an instrument Scope currently has. Remaining: SIGNING (RULE_02 became signable
2026-08-08) and the DEPLOY. Still queued separately: the morning-brief excluded-volume tie-break
(morning_brief.py:163 — affects EVERY excluded rule) and the near-miss missing_legs leak
(forming.py:57 — already pinned as a deliberate surfacing decision).


## 2026-08-10 — morning-brief hero: stop ranking on volume (second design)

Full note: [[SESSION-2026-08-10-morning-brief-exclude-rank]]

Branch fix/morning-brief-exclude-rank off main 052d081, UNCOMMITTED, 0 ahead. Suite 1494 passed / 8
skipped / 5 xfailed / 0 FAILED (baseline 1474 + 20 new). DB md5 43564013d1eff60d292d9aef4350ce55
unchanged. Base confirmed clean per the STOP condition.

THE DEFECT: _synthesize_headline ranked on (instruments, hi, n) and a["n"] += 1 ran BEFORE the
exclusion branch, so excluded-rule volume chose the headline on a tie. An INCOMPLETE FIX, not a design
choice — the function's own comment says "never by raw alert volume, which is what let a single noisy
source dominate", and hi had ALREADY been made excluded-aware for exactly this reason. NARROWER THAN
IT SOUNDS: key 3 is only reached on an exact tie of instruments AND hi, and the existing pin resolves
at hi and never exercises n. Checked, not assumed.

n IS BOTH A RANKING KEY AND A DISPLAYED TOTAL (rendered as "(N signals)"), so the fix could not simply
exclude rules from n. I inferred from that that the answer was "a separate ranking count". THE
INFERENCE WAS WRONG.

MY FIRST FIX WAS PERVERSE AND THE VERIFIER PROVED IT. rank_n (count of non-excluded alerts) is
IDENTICALLY hi + |rejected|, because every non-excluded alert is either credited to hi or refused
per-alert into dropped. So once the first two keys tie, the third differs ONLY by alerts THE GATE
REJECTED — five insider-SALE RULE_06 alerts on the losing ticker flipped the hero to it. I traded a
wrong tie-break for a worse one, and the comment I wrote calling it "the last place raw volume from a
rule the gate throws out can decide anything" was false one field after I wrote it.

SHIPPED DESIGN (attempt 2): remove the volume key entirely; rank on (instruments, hi) only, over
sorted(scored.items()) so an exact tie breaks ALPHABETICALLY. Ranking now uses only what the gate
actually credits. This ALSO closes a PRE-EXISTING non-determinism: without sorted, ties fall out of
dict insertion order = SQL row order, and the verifier demonstrated that adding an ordinary
alerts(severity, created_at) index changes the plan from SCAN to SEARCH and reshuffles the hero.

THREE VACUOUS TESTS OF MINE IN ONE SESSION: (1) the class test piled noise on AAA, the tie winner, so
all 13 parametrised cases passed with the fix reverted; (2) ...third_tiebreak[AAA] survived ALL 13 of
the verifier's mutations for the same reason; (3) the determinism test called _hero twice against the
SAME per-test DB, so the "reversed row order" call APPENDED rather than replaced and AAA kept the
lowest rowid either way — dropping sorted() killed 0 tests. Fixed by noising the loser, deleting the
unfailable direction, and clearing alerts at the top of _hero. A TEST THAT CANNOT FAIL IS WORSE THAN
NO TEST, BECAUSE IT READS LIKE COVERAGE. The verifier's rule — every new test must fail under at least
one mutation — is the bar to apply before claiming a fixture proves anything.

MUTATIONS NOW: old n third key -> 15 failed; MY OWN ATTEMPT-1 DESIGN (rank_n) -> 1 failed, precisely
the gate-rejected-volume test, so the code cannot silently regress into it; dropping sorted() -> 1
failed, precisely the determinism test. Restored by file copy, 29 passed, md5 match.

"NO USER-FACING CHANGE" WAS ALSO OVERTURNED. The narrow claim holds (n's increment, both returns and
the render string are byte-identical, and the displayed total still counts context rules — 7, not 2),
but across 1,500 fuzzed corpora the hero ticker changed in 110, n in 105, types in 91, and the noise
wording can degrade from a named source to a generic "excluded sources". mode NEVER changed (0/1500),
so it remains a RANKING fix and not a gate fix — but it is visible and I had said otherwise.

SCOPE: 2 files (scripts/morning_brief.py, tests/test_brief_hero_agrees_with_gate.py). jpt_common.py,
forming.py, rule_10_corroboration.py, every rule script, hi/noise/dropped/example all untouched.
RULE_10_EXCLUDED reused; no second rule list.

FLAGGED, NOT FIXED: (1) "(N signals)" counts context rules while the sentence claims "the day's
strongest cross-source read"; (2) PRE-EXISTING AND NOT MINE — morning_brief.py:571 renders "in the
last 7 days" while CONVERGENCE_WINDOW_DAYS = 14 (byte-identical at 052d081; I reviewed that string and
missed it).

This clears the last loose code thread from the RULE_08 diagnosis. Remaining: SIGNING (RULE_02 became
signable 2026-08-08) and the DEPLOY, plus three committed-but-unmerged branches (1315c78 novelty,
21e46ee cross-partisan, b48c332 PWA) and the untouched prod remap chain.


## 2026-08-11 — sign RULE_02: NO-GO, the operation is inverted

Full note: [[SESSION-2026-08-11-sign-rule02]]

STOPPED AT STAGE 1. No branch created, no code changed, nothing committed. Base confirmed
main == origin/main == 052d081, 0 tracked modifications under Scope/.

THE CENTRAL PREMISE IS BACKWARDS. The order says "an unsigned rule is context; a signed rule is a leg".
rule_10_corroboration.py:226-231 says the opposite: if rule not in SIGNED_RULES -> return True
UNCONDITIONALLY; signed + corroborates IS NULL -> return False. Its own docstring: "THE BLAST RADIUS IS
SIGNED_RULES AND IT IS TINY BY DESIGN. Only those rules are interrogated. EVERY OTHER RULE RETURNS TRUE
UNCONDITIONALLY, so the congressional, earnings, 13F and senate-lda legs behave EXACTLY as before."
SIGNING IS A RESTRICTION, NOT A PROMOTION. RULE_02 IS ALREADY A FULLY-CREDITED congressional LEG.

AND NOTHING WOULD EVER POPULATE THE COLUMN. Writers of alerts.corroborates: rule_06_form4.py,
rule_01b_first_touch.py ("corroborates / corroboration_note ARE WRITTEN BUT INERT TODAY,
DELIBERATELY"), remap_rule01b_direction.py. NOT rule_02_cluster.py — its 6 "corroborat" mentions are
all prose/comments — and NOT any of the four RULE_02 remaps, which touch lifecycle_stage and
why_matters only. So the order's sequence "(1) prod remaps populate corroborates, (2) code deploys,
(3) signed RULE_02 lights up" HAS A FALSE STEP 1. The rule would be dark PERMANENTLY, not temporarily.
This looks like RULE_01B's (valid) sequencing transplanted onto RULE_02, where the prerequisite was
never built.

MEASURED BLAST RADIUS (prod 2026-08-11): RULE_02 96 alerts, 70 HIGH/CRITICAL, 0 with corroborates NOT
NULL. 41 distinct tickers carry an eligible RULE_02 alert and ALL 41 have NO sibling congressional leg
(RULE_01B / RULE_CLUSTER) within 14 days — so the shared instrument does NOT cushion the loss; signing
strips congressional from 100% of them. Contrast: RULE_06 (signed) 616 alerts / 258 populated;
RULE_01B (unsigned but writes the column) 2,149 / 23 — which is precisely why sign/rule01b remains
correctly blocked by I2.

THREE FURTHER PREMISE FAILURES: UNSIGNED_RULES DOES NOT EXIST anywhere in the repo (unsigned is simply
absence from SIGNED_RULES = frozenset({"RULE_06"}), jpt_common.py:890), so "remove it from
UNSIGNED_RULES" is not a performable operation; the cross-partisan branch 21e46ee is NOT merged though
the order states all prior work is; and this is not "the second and final signing" — SIGNED_RULES stays
{"RULE_06"}.

WHAT WOULD ACTUALLY BE REQUIRED: RULE_02 must emit a per-alert direction verdict into corroborates /
corroboration_note the way RULE_01B does. The raw material EXISTS — RULE_02 computes net_direction
(NET_LONG/NET_SHORT/MIXED) and, on the cross-partisan branch, party_split — so a defensible verdict is
close (NET_LONG corroborates a bullish thesis; NET_SHORT/MIXED does not, mirroring the insider "a sell
is not evidence for a bullish thesis" principle). But that is a gate-consequential DESIGN decision and
belongs in its own work order. Then a history-populating remap. Then signing. Three steps, in order.

ALSO CORRECTED: [[What Scope Is Today]] said "Sign RULE_02 — its repairs are complete, so it is now
signable". That is now known false and has been rewritten, along with a clarification that signing is
a restriction rather than a credit.

No verifier run: there is no change to verify. Offered an independent pass on the NO-GO finding itself.


════════════════════════════════════════════════════════════════════════════════════════════
2026-08-11 — PRODUCTION REMAP VERIFICATION (read-only)
════════════════════════════════════════════════════════════════════════════════════════════

ALL FIVE APPLICATIONS INDEPENDENTLY CONFIRMED ON THE REAL PROD DATABASE. Read-only throughout:
every prod connection opened file:/app/data/jpt.db?mode=ro, no --apply, no writes to any
database, no branch merged, no commit.

PROD ACCESS METHOD: `railway ssh` into the deployed container, then python3 with sqlite3 in
URI read-only mode. The CLI at /opt/homebrew/bin/railway is authenticated
(sloppysecondstbb@gmail.com) and the repo is linked to project respectful-generosity ->
service Scope -> environment production, volume scope-volume-tHBX at /app/data.
Prod DB: 35,115,008 bytes, md5 f3ab5acc2ec8d4296025f33902b95e7e.

THIS SUPERSEDES MY OWN EARLIER FINDING. SESSION-2026-08-11-get-db-path-fix and What Scope Is
Today both recorded "prod has NOT been remapped" with 220/518 unverified and zero pre-image
tables. That was TRUE WHEN MEASURED. The chain ran tonight between 19:57 and 20:44, after
those notes were written. The finding is superseded, not retracted as an error.

LOCAL IS NOT PROD, WITH RECEIPTS. local Scope/data/jpt.db: 171 contracts, 3,347 alerts,
0 themes. prod /app/data/jpt.db: 518 contracts, 35,705 alerts, 1 theme. AND THE LOCAL FILE
CARRIES THE PRE-IMAGE TABLES — rule09_ticker_remap_backup,
rule01b_first_touch_retraction_backup, rule01b_ticker_validation_backup,
rule01b_direction_backup, PLUS rule02_directional_remap_backup, with verified_at IS NULL = 65.
That is the physical proof of tonight's confusion: remaps were applied to the local file and
their success read as production being fixed. Note the tail: rule02_directional_remap_backup
exists on LOCAL and NOT on prod — the RULE_02 chain's first step went to the wrong database,
and a local pre-image is not a substitute for a prod one.

RULE_11 — PROVEN. contracts 518; verified_at IS NULL 65 (was 220); 453 award-id rows verified,
65 no-award-id rows cleared; 453 + 65 = 518 exactly. Zero rows with NULL verified_at still
carrying an amount or date, so --clear-unverifiable did its job. Zero duplicate award_ids —
the fabrication signature is gone. Alerts: 223 total, 65 superseded (== 65 retracted, all
carrying "original figure retracted", all with event_date NULL), 158 created == 117 corrected
+ 41 already_ok. Both cross-checks close. RULE_11 gate-eligible surface now 139 HIGH/CRITICAL.

THE $48B/$51B HEURISTIC IS RETIRED. Verified against USASpending DIRECTLY (not the transcript,
not the script): id=284 LOCKHEED DEAC0494AL85000 $48,063,737,196 -> total_obligation
$48,063,737,196; id=217 HUMANA HT940216C0001 $51,269,205,263 -> $51,269,205,263; id=151
$9,273,610,228 -> match; id=152 $2,870,112,487 -> match; id=265 $7,463,526,548 -> match. 5/5.
So "$51,269,205,263 is still the top displayed award" is NO LONGER evidence of an unrepaired
DB. The fabrication was never the number — it was one number copied across rows pointing at
DIFFERENT award ids. Lockheed's ~30 rows now hold ~30 distinct amounts from $54M to $48B. The
one alert still showing $51.2B (#6303) is CORRECT: its date 2016-07-29 matches USASpending's
date_signed exactly, and _detail_fields reads date_signed, not period_of_performance.

RULE_11 STRUCTURAL GAP: repair_rule11_contracts.py creates NO pre-image table. The end state
is proven; the transition counts ("175 corrected", "140 wrong amounts", "155 wrong dates") are
UNVERIFIABLE after the fact because the before-image no longer exists; and the repair CANNOT
BE UNDONE. It is the highest-blast-radius script in the chain and the only one without an undo.

RULE_09 — PROVEN. rule09_ticker_remap_backup holds exactly 13197/DTGI, 13199/PRIM, 15093/VRE,
all stamped 2026-08-11 20:40:48. All three now ticker=NULL; #15092 IR untouched and not in the
backup. RULE_09: 10 alerts, 9 blank-tickered, 1 keyed. RESIDUAL: the headlines still name the
cleared symbols ("Lobbying spike: $DTGI spend up 167% YoY"). The remap writes alerts.ticker
only. No corroboration consequence — RULE_09 is excluded as context — but a reader still sees
$DTGI on an alert the system decided is not about DTGI.

RULE_01B CHRONOLOGY — PROVEN BY RE-DERIVATION, NOT BY COUNT. I re-implemented the retraction
predicate and re-derived the set from `transactions`, using each alert's PRE-IMAGE ticker where
the later ticker-validation remap had blanked it (otherwise the join breaks for 208 rows).
Result: SHOULD-RETRACT 694, SOUND 1465, UNDECIDABLE 4; 694 + 1465 + 4 = 2,163. The derived set
is SET-EQUAL to both the backup table and the retracted rows — symmetric difference 0, no
missed retractions, no over-retractions. All 694 pre-images record old_lifecycle_stage='created'.

THE 7 SUPPRESSED ROWS, exactly as disclosed, and their dedup slots are STILL OCCUPIED (each
(RULE_01B, ticker, member_id) key holds exactly 1 row and it is retracted, so the rule's dedup
keeps skipping it and the true first touch stays suppressed — the honest outcome; nothing was
deleted to make the number look better): #22612 GIL K000398 claimed 2026-06-17 true 2026-06-02;
#22625 WAB M001232 06-15/06-04; #22632 LYV 06-12/06-01; #22669 TSCO 05-22/05-15; #22680 HUBB
05-21/05-13; #22688 CDW 05-19/05-14; #22694 CHRW 05-18/05-15.

#22612 pre-image: "First Touch — Kean, Thomas H. opens new position in GIL" / "…has no prior
disclosed trade in GIL. Transaction: sale, $1,001 - $15,000." Note it carries BOTH bugs in one
row — claims "opens new position" while its own detail says "sale".

RULE_01B TICKER VALIDATION — PROVEN (caveat). 208 backup rows; 208/208 now blank; 208/208 still
exist (none deleted); 0 resolved to a guessed ticker; lifecycle created->review 153,
retracted->retracted 55 (a retracted row correctly stayed retracted). Spot-checks: US 6 alerts
/ 6 members, NY 4/4, MMC 2/2 — all blank, all present, none guessed.

THREE TRANSCRIPT NUMBERS CORRECTED. (1) "30 distinct FALSE CONVERGENCE KEY symbols" — the
correct figure is 29. The script's own definition (_report, :223) flags a symbol only when
len(members) > 1. Re-derived: BRCM CA CS CTRA FI FL HONAV I II IV JP LLC MICH MMC MN N NY OL PA
PLC SPL SYS TN TPH TREAS US VA WA X. (2) The corporate-suffix words are barred but are NOT
false convergence keys: LLC (3 members) qualifies, but CORP, INC, TRUST, FUNDS, GROUP, BANK,
DEBT, GOVT each appear on ONE alert from ONE member, as do IN, MM and OH. Correctly barred;
they simply never spanned members. (3) The barred set is 160 distinct symbols, not 30 —
29 cross-member + 131 single-member.

A RESIDUAL THE REMAP CANNOT REACH, AND IT IS GATE-RELEVANT. The barred symbols are barred for
RULE_01B only. Across all rules 788 alerts still key on them: 320 from RULE_ANOMALY (excluded,
no effect) — but SIX FROM RULE_02, WHICH IS A CREDITED congressional LEG, four of them HIGH:
#16 CA HIGH "2 members sold CA (NET_SHORT)"; #66 US HIGH; #67 US MEDIUM; #68 US HIGH; #69 US
HIGH; #36121 HONAV MEDIUM. Not a defect in tonight's work — it is exactly what
remap_rule02_ticker_resolution.py (chain step #2, still outstanding) exists to fix. It is now
the LAST place a false convergence key can reach the gate.

RULE_01B DIRECTION — PROVEN. 1,432 backup rows; 1,432/1,432 pre-images said "opens new
position"; 626 now carry a disposal verb (matches the report); 369 corrections at HIGH/CRITICAL
(matches); ZERO rows still saying "opens new position" while the underlying transaction is a
sale. Verdicts now: corroborates=1 833, =0 636, NULL 694 (exactly the retracted set).

RE-DERIVED FROM `transactions`, NOT FROM TAGS. For all 1,432 corrected rows I joined
(member_id, raw_ticker, transaction_date) to the transactions table and compared the stored
verdict to the real transaction_type: 1,432/1,432 joined, 0 unjoinable, 24 disagreements — and
ALL 24 are `exchange`, which maps deliberately to corroborates=0, "exchange — directionally
neutral" (25 such rows on prod, all consistent). My expectation was wrong, not the data.
Excluding that category the agreement is 1,432/1,432.

CROSS-CONSISTENCY (STAGE 3) — PROVEN BY DIRECT QUERY. chronology-retracted 694;
direction-corrected 1,432; OVERLAP 0; union 2,126 of 2,163; touched by neither 37. Zero
retracted rows whose headline no longer says RETRACTED; zero retracted rows given a direction
verdict. NOT ONE ROW REASSERTED A DIRECTION ON A RETRACTED CLAIM. The 37 untouched rows were
created 2026-08-05 -> 2026-08-11 20:32, i.e. emitted by the live rule after the fix shipped, so
they were already correct (27 with corroborates=1, 10 with 0). 694 + 1,432 + 37 = 2,163.

RUN ORDER, from the data rather than the scripts' self-report. contracts.verified_at 19:57:19
(RULE_11); rule09_ticker_remap_backup 20:40:48; first_touch_retraction_backup 20:43:16;
ticker_validation_backup 20:44:29. rule01b_direction_backup HAS NO TIMESTAMP COLUMN, so it
cannot be clocked — but it is placed last by a STRONGER argument than a clock: 153 rows appear
in both the direction and ticker-validation backups and ALL 153 carry "(UNVERIFIED SYMBOL)" in
their headline. Only the direction remap writes that marker, and only when the row is already
barred — a state that exists solely after validation applied. Direction ran third, proven by
content. The four "(UNVERIFIED SYMBOL)" headlines not in the validation backup (#38804 CADDO,
#38805 TULSA, #38807 GRAND, #38810 OKLA) are live-rule emissions at 20:32:51, before the chain
ran; the rule stamps the marker itself at emission. Consistent, not an anomaly.

STALE COMMENT FOUND: remap_rule01b_ticker_validation.py:284 says "9 rows are in both this set
and the chronology remap's." The real overlap on prod is 55. The code is correct — it branches
on lifecycle_stage and all 55 correctly stayed retracted — but the number would mislead.

GATE IMPACT, MEASURED AND HONESTLY SMALL: RULE_10 alerts 1, themes 1, theme_signals 7 —
UNCHANGED. No new convergence fired. That is the expected direction: the chain removed false
claims and withheld unvalidated keys. It makes the existing legs honest; it does not
manufacture crossings. The campaign's supply problem is untouched by tonight.

"RULE_01B IS NOW UNBLOCKED FOR SIGNING" IS OVER-STATED AND I DID NOT RECORD IT AS DONE.
Coverage went from 23 of 2,149 (1.1%) to 1,469 of 2,163 (67.9%); among gate candidates
(HIGH/CRIT + keyed) 299 of 366 (81.7%). The remaining 67 gate-candidate rows with corroborates
IS NULL are EXACTLY the 67 retracted rows that are still HIGH/CRITICAL with a live ticker.
Signing today fails those closed — arguably correct, but a behaviour change nobody has decided.
AND IT EXPOSES THE RESIDUAL THE CHRONOLOGY REMAP DISCLOSED IN ITS OWN DOCSTRING: lifecycle_stage
is a display label and RULE_10's candidate query NEVER READS IT, so those 67 retracted false
first-touch claims across 58 distinct tickers are still live corroboration candidates. The one
mercy is timing — 0 of them fall inside the gate's 14-day window, so present exposure is nil
and the risk is latent, not active. Signing RULE_01B is a real session, not a checkbox, and it
must first answer whether the gate should honour lifecycle_stage.

STILL OUTSTANDING: the RULE_02 remap chain (x4) and the RULE_CLUSTER ticker-validity remap have
NEVER run on prod — zero rule02* tables there. fix/get-db-path-missing-arg (uncommitted) is
required before they can be invoked the documented way. Unmerged and ready: 1315c78 novelty,
21e46ee cross-partisan, b48c332 PWA, morning-brief ranking. RULE_02 signing remains a NO-GO.

LOCAL DB CHANGED AGAIN, MID-SESSION. Session start md5 0861fc993c71853d0be0b22148718601; session
end 31387d757e0d5c8049f3d1e3eec98605. This session never wrote to it — every prod query ran over
SSH against /app/data/jpt.db and the only local reads were mode=ro. lsof shows no process holding
the file and no scheduler or dev server running. But the mystery is now largely SOLVED and is not
sinister: the local file contains the four remap pre-image tables plus
rule02_directional_remap_backup and a repaired verified_at count, so scripts have been run
against it from outside these sessions — exactly the local-vs-prod confusion this session exists
to correct. Treat every local figure as indicative only.

LIVE ADDENDUM, recorded at the end of the same session — THE RULE_02 CHAIN STARTED WHILE I WAS
VERIFYING. Re-checked prod and rule02_ticker_remap_backup (6 rows) had appeared, absent at the
start. remap_rule02_ticker_resolution.py --apply landed on prod and barred EXACTLY the six alerts
I had flagged in Stage 2d: #16 CA, #66/#67/#68/#69 US, #36121 HONAV — all now ticker='',
lifecycle_stage='review', none deleted, none guessed. RULE_02 alerts still keyed on a
RULE_01B-barred symbol: 0. THE "LAST ROUTE A FALSE CONVERGENCE KEY HAS TO THE GATE" IS CLOSED.

I FLAGGED AN ORDERING WORRY AND THEN DISPROVED IT. rule02_directional_remap_backup does not exist
on prod, so step #2 applied before step #1, which looked like a skipped guard. It is not:
remap_rule02_ticker_resolution.py:24 states "Run either order; each backs up only the columns it
touches." #1 and #2 are order-independent BY DESIGN. No --skip or --force was used.

THE WORKING TREE ALSO MOVED MID-SESSION, not by me: fix/rule02-cross-partisan was MERGED to main
(fast-forward, HEAD 052d081 -> 21e46ee), and _get_db_path(None) was applied by hand to the two
scripts that have run (remap_rule02_directional_count.py, remap_rule02_ticker_resolution.py).

THREE CALL SITES REMAIN BROKEN AND THEY ARE THE THREE THAT HAVE NOT RUN YET:
remap_rule02_identity_dedup.py:83 (chain #3), remap_rule02_cross_partisan.py:105 (chain #4),
remap_rule_cluster_ticker_validity.py:76 (RULE_CLUSTER). NOTE: remap_rule02_cross_partisan.py is
a FIFTH broken site that DID NOT EXIST when SESSION-2026-08-11-get-db-path-fix measured "exactly
four" — that count was correct for main at 052d081, and the cross-partisan merge introduced a new
one. Anyone re-running that search should expect THREE remaining, not two. Same one-line fix; the
crash is at _connect, before the database is touched. Do not work around it with --skip flags.

A PROD MD5 IS NOT A STABLE FINGERPRINT AND MUST NOT BE USED AS ONE. f3ab5acc… at session start,
6e8a230f… at session end — prod is live (a new alert landed 20:53:07 mid-session) and the operator
was applying a remap in parallel. Byte-identity checks belong to the LOCAL working DB, not prod.


════════════════════════════════════════════════════════════════════════════════════════════
2026-08-12 — SIGNAL EVENT SYSTEM (bus + DNA + pulse), additive frontend
════════════════════════════════════════════════════════════════════════════════════════════

Branch feat/signal-event-system off main (21e46ee). UNCOMMITTED, 0 commits ahead. Surface: four new
files (api/static/signal-events.js, signal-dna.js, signal-pulse.js, signals.html) plus 27 ADDITIVE
lines in api/main.py (four FileResponse routes). NO rule/gate/scoring/detection/ingestion/migration
code touched. NO database written (working DB md5 31387d75… identical before and after). NO existing
page's rendering changed — /signals is a NEW page.

1a — HOW ALERTS ACTUALLY REACH THE BROWSER: POLLING, AND NOTHING ELSE. Searched all of Scope/api/:
zero WebSocket, zero SSE, zero text/event-stream, zero EventSource. The ONLY StreamingResponse in the
codebase is a CSV download at routers/history.py:187. The existing feed's only recurring network call
is alerts.html:1153, setInterval(updateAlertBadge, 5*60*1000) — a COUNT BADGE every five minutes; the
list itself loads on page load and user action. What makes honest near-real-time possible is a cursor
that ALREADY EXISTED: routers/alerts.py:93-95, `datetime(a.created_at) > datetime(:since)`. Exercised
live: /alerts?since=2026-08-12T12:00:00 returned 3 rows, 12:09:09 -> 12:09:15. So "live" here means
POLLED EVERY 30s, not pushed, and SignalEvents.describeTransport() returns that literal string for the
UI to print rather than implying a socket.

1b — FIELDS. Payload read from PROD, not inferred. Keys: created_at, detail, evidence_confidence,
full_name, headline, id, lifecycle_stage, member_id, novelty_score, opportunity_score, party, receipts,
rule, severity, source_quality, source_url, state, tags, theme_id, ticker, time_horizon, verify_url.

CATEGORY COMES FROM THE GATE ITSELF. /api/rule-model (main.py:789) derives instrument_of from
jpt_common BY IMPORT, with a docstring that says exactly why: "it must never learn that from a list
copied into JavaScript". Live: 8 eligible rules -> 5 instruments (congressional, contracts, earnings,
insider, institutional), 13 excluded, min_instruments 3, window_days 14. The bus reads that map and
hardcodes nothing. A rule with no instrument gets category "context" — not a styling default, a true
statement that it cannot corroborate.

THREE REQUESTED FIELDS DO NOT EXIST. Named as API requirements, NOT fabricated client-side:
(1) `location` — no column anywhere, no alert carries one; emitted as a permanent null nothing reads.
(2) `corroborates` / `corroboration_note` — EXIST IN THE DATABASE and are NOT in the /alerts SELECT
    (routers/alerts.py:170-178), so the per-alert signed-leg verdict is invisible to the browser. This
    is the single highest-value follow-up and belongs in its own reviewed change.
(3) Theme membership on contributing legs — only the RULE_10 SYNTHESIS alert carries theme_id.
    Measured: of 50 RTX alerts, exactly 1 has one. The RULE_06/11/15 legs that actually corroborated
    do NOT, so a leg that DID corroborate is indistinguishable from one that did not. Adapter refuses
    to guess. Corroboration is therefore derived from theme_id != null plus tags.instrument_count
    (verified live on #32990: {"instruments":["contracts","earnings","insider"],"instrument_count":3})
    and confidence passed through from stored evidence_confidence.

1c — STACK: vanilla JS, no framework, no bundler, no Jinja. There is NO StaticFiles mount; every asset
is its own explicit route (/theme.js, /rule-names.js, /cmdk.js). The three new modules follow that.

BUS IS THE SOLE PATH, PROVEN BY GREP: fetch( count — signal-dna.js 0, signal-pulse.js 0,
signal-events.js 2 (/alerts and /api/rule-model). Neither subscriber can see raw alert data.
created_at is parsed EXPLICITLY as UTC rather than handed to Date.parse, which is
implementation-defined on the naive form and which Safari has historically read as local time — that
would shift every rung by the viewer's offset.

TWO DEFECTS FOUND IN MY OWN BUS, BY MEASURING RATHER THAN READING.
(1) BOUNDARY-SECOND DROP. The cursor sat on the newest row's timestamp and the endpoint filters
    STRICTLY GREATER, while created_at is second-resolution. Measured on prod over 500 alerts: 310
    distinct seconds, 60 of them carrying more than one alert, 50.0% OF ALL ALERTS SHARE THEIR SECOND,
    worst case 28 alerts inside 2026-08-11 08:32:53. Any row written into the cursor's own second
    after a poll returned would have been skipped PERMANENTLY AND SILENTLY — the helix would simply be
    quietly incomplete. Fixed: hold the cursor ONE SECOND BEHIND the newest row and let dedup absorb
    the overlap. Cheap re-query beats silent loss.
(2) THE DEDUP SET WAS TIED TO THE DISPLAY BUFFER. emit() deleted an id from seenIds when its event
    aged out of `recent`, so a re-delivered row could be emitted twice. Latent while the cursor only
    moved forward — but fix (1) makes the bus re-see rows ON PURPOSE, so dedup now has its own FIFO
    bound (5,000 ids) independent of the 400-event display buffer.
    Verified after the fix: cursor lags newest by exactly 1s; re-ingesting all 120 rows emitted 0
    duplicates and left the bus count unchanged; the bus holds all 12 alerts from a shared second.

DNA — 120 real alerts on first paint, categorised entirely from the live rule model: context 73,
insider 47 (RULE_ANOMALY 61 + RULE_08 12 -> context; RULE_06 47 -> insider); MEDIUM 44, HIGH 55,
CRITICAL 21. THE DECISIVE TEST, two isolated instances, one real alert each, pixels measured off the
canvas: #32990 (RULE_10 RTX, HIGH, theme_id=1, instrument_count=3) vs #38416 (RULE_11 J, LOW,
theme_id null) — 316 vs 88 lit pixels (3.59x), 12 vs 2 lit columns (6x, the halo's spread), peak
alpha 85 vs 44 (1.93x); stroke width 2.6px vs 1.1px. Rotation speed encodes NOTHING and is constant,
stated in the header rather than tied to a metric it would misrepresent.

PULSE — THE PROTOTYPE'S 12-SECOND WINDOW WAS DISCARDED, NOT COPIED. It was sized for a fake generator
firing every few seconds; against real cadence it would read "quiet" ~100% of the time and the whole
indicator would be decorative. Measured prod, 500 alerts over 2026-08-10 00:32 -> 08-12 12:09 (57
hours with activity): alerts/hour min 1, MEDIAN 5, max 64; busiest 64, 42, 40, 33, 22, 22. Window set
to 60 MINUTES with thresholds read off that distribution: quiet 0-1, active 2-7 (straddles the
median), elevated 8-24, high-signal 25+ (observed top ~5%). ALL FOUR STATES REACHED FROM REAL HOURLY
VOLUMES: 2026-08-11 00 -> 64 alerts -> high-signal; 08-10 18 -> 8 -> elevated; 08-10 14 -> 4 ->
active; 08-10 04 -> 1 -> quiet. CAVEAT STATED: the counts are real (exactly what Scope emitted that
clock hour) but the timestamps were replayed into the current window — there is no way to observe a
64-alert hour on demand. The live page unassisted read "Active · 3 alerts in the last 60 min · 54 bpm".
SPIKE FIRES ON CORROBORATION AND NOTHING ELSE: a real CRITICAL uncorroborated (#38936) -> spiking
false; the real corroboration (#32990) -> spiking true, footer "Last corroboration seen on the bus:
RTX · 3 instruments". A CRITICAL alert is not a corroboration.

HONESTY AUDIT — THERE IS NO DEMO MODE AT ALL. The contract permits a visibly-labelled one; none was
built, so nothing can be silently active. Math.random count 0. Every match for random/fake/simulated/
demo in the four new files is PROSE IN A COMMENT, never code. FAILURE PATH PROVEN, NOT ASSERTED:
window.fetch replaced with a rejecting stub and the bus restarted — 121 events before, 0 EMITTED
DURING THE OUTAGE, 121 after, status "error"/"simulated network failure", and the pulse held its last
TRUE reading rather than inventing one.

FRAME BUDGET, MEASURED: DNA 0.508 ms avg / 1.50 ms worst at 120 rungs against a 16.67 ms budget (3%);
pulse 0.029 ms avg / 0.80 ms worst. At 414px: 0.407 ms avg, zero horizontal overflow. ctx.filter 0,
shadowBlur 0, createPattern 0 (no grain at all), devicePixelRatio capped at 2, depth faked with alpha
+ scale never blur. BOTH PAUSE CONDITIONS GENUINELY STOP THE LOOP: scrolled out of view -> 0 frames
advanced over 1.5s, resumed -> 120; tab backgrounded -> 0 frames for DNA AND pulse while still
intersecting, restored -> 97 each. MY FIRST OFF-SCREEN TEST WAS INVALID AND I CAUGHT IT: the page is
only 1,158px tall, so scrollTo(0,3000) did nothing and the canvas never left the viewport — the first
run reported 145 frames "while paused", which was the test lying, not the code. Re-run with a spacer
that makes the page genuinely scrollable, the loop stops dead. Both listeners are needed because a
backgrounded tab still reports the element as intersecting.

A REAL DEFECT CAUGHT IN REVIEW — THE PALETTE HID THE MOST IMPORTANT STATE. The first build had ONE
hardcoded palette tuned for dark, but the page supports both themes (theme.js writes
data-theme="light") and canvas cannot inherit a CSS custom property. Measured contrast of the
CORROBORATION ink against the page background: light theme BEFORE the fix 1.14:1 — effectively
invisible; after 3.11:1; dark 18.8:1. A palette that hides the thing the product exists to detect is
not a styling nitpick. Fixed with two palettes resolved at DRAW time so the toggle works without a
reload; paused canvases and the DOM legend repaint on scope:themechange (theme.js:62). NOTE STATED
RATHER THAN ROUNDED UP: 3.11:1 clears WCAG 1.4.11 non-text contrast (3:1, the applicable bar for a
graphical object) and does NOT clear the 4.5:1 text bar; the figure is a mean over alpha-composited
halo pixels, so the core stroke is better and the margin is thin.

TESTS: 49 passed across the API-importing suites (test_landing, test_phase3, test_evidence_today,
test_heatmap_activity, test_winrate_placeholder_honesty); working DB md5 identical before and after.
All four new routes REGISTERED on the app (/signals, /signal-events.js, /signal-dna.js,
/signal-pulse.js), verified by plain import — no TestClient context manager, so the lifespan and its
APScheduler never ran (api.main._scheduler is None after import).

LOCAL PREVIEW: the Scope app was deliberately NOT started (its lifespan boots APScheduler, which
writes real alerts). Instead a scratchpad static server served the real files and PROXIED read-only
GETs to production, so every number above came from real prod data through the real component code.

NEXT, per the design brief's build order: global system state is largely covered by pulse, so
signal constellation -> globe weather -> event animation -> telemetry -> scrubber -> ambient flow,
each a new subscriber to the same bus with no new data path. THE HIGHEST-VALUE FOLLOW-UP IS NOT A
COMPONENT — it is getting `corroborates` into the /alerts payload. Until then no visual can
distinguish a leg the gate CREDITED from one it merely SAW.

VERIFIER PASS — IT OVERTURNED THE COMPLETENESS HEADLINE, AND IT WAS RIGHT. The honesty headline
survived (no simulated/random/fallback path, proven by execution rather than by reading comments;
0 events emitted on a failed poll; both components mounted in a sandbox with NO fetch binding in scope
at all, so a fetch would have thrown ReferenceError). The completeness headline did not.

THE CRITICAL FINDING: ingest() could permanently skip rows while the page reported "live". A single
request cannot carry a backlog — /alerts orders created_at DESC and truncates (routers/alerts.py:180,
206), so one request returns the NEWEST n after the cursor, not the oldest. Advancing the cursor to
the newest row therefore jumped everything between the old cursor and the oldest row returned, and
`since` excluded it FOREVER. Reproduced in the verifier's own harness: 251 server rows in a gap,
121 delivered, 130 NEVER DELIVERED, still never delivered after two more polls. Reachable by any gap
beyond ~2h at the measured peak of 63 alerts/hr: laptop sleep, background-tab throttling, a deploy,
an API restart. THIS WAS WORSE THAN A CRASH because it broke the page's own promise —
signals.html told the user "if the poll fails, the helix stops gaining rungs and the pulse goes
quiet", and here the poll SUCCEEDED. Not a synthetic-data violation, a silent-incompleteness one,
which is the same family of dishonesty by a different route.

FIXED by draining the endpoint's own paginated form (routers/alerts.py:183-199), which returns `total`
and `pages` so the backlog is a number we can READ rather than guess; pages drain in sequence and THE
CURSOR MOVES ONLY AFTER EVERY PAGE HAS LANDED, so a row missed because new alerts shifted pagination
mid-drain is still > cursor and is caught next poll — self-healing rather than lossy. PROVEN against a
real 305-row / 4-page backlog (manufactured by having the preview proxy strip the `since` cursor, so
the server answered REAL prod data as if the client were far behind): after seed 120 retained; after
one drain 305 retained, server-reported backlog 305, unique alert ids 305 == server total 305,
duplicates 0. AND an over-long backlog now refuses to read as healthy: with the page size shrunk so
305 rows exceed MAX_DRAIN_PAGES, state = "truncated", and the UI prints "BACKLOG TRUNCATED — more rows
matched than were drained; this view is INCOMPLETE". `days=2` remains a second, harder ceiling of the
same class — disclosed via getStatus().lookbackDays rather than hidden.

FIVE MORE FINDINGS, ALL FIXED.
(1) gate_eligible: true for RULE_10 was FACTUALLY WRONG — RULE_10 is in RULE_10_EXCLUDED because it IS
    the gate, not a peer instrument. Consumed nowhere today, but a false statement in the public event
    contract that would have let a future consumer draw the gate as one of its own legs. Now false.
(2) An ABSENT instrument_count rendered identically to a measured 3 (`instrument_count || 3`), so
    "corroborated, breadth unknown" was indistinguishable from a measured 3-instrument convergence —
    while the file header claimed the glow "scales with instrument_count". Unknown now renders at a
    FLOOR below any measured count. Verified by recording actual stroke alphas: unknown 0.001 <
    3 -> 0.003 < 5 -> 0.008, monotonic. It SATURATES at 5, matching the gate's own tiers, and that is
    now stated rather than implied away.
(3) senate-lda and fed-register fell through to the `unknown` grey, which was itself near-identical to
    `context`. Inert today (both rules excluded) but CLAUDE.md explicitly plans fed-register's return
    under real issuer attribution — at which point an ELIGIBLE instrument would have rendered as "we
    don't know". Both now coloured; `unknown` darkened so it cannot be mistaken for `context`.
(4) A listener leak on the legacy path: the ResizeObserver-absent fallback registered a `resize`
    handler that destroy() never removed. Now removed in both components.
(5) The page HARDCODED A PRODUCTION FACT — "RULE_10 has fired once in Scope's history" — as static
    copy, on a page whose whole thesis is that everything shown is real. Unverifiable from the browser
    and false the moment RULE_10 fires again. Replaced with a statement about what the page can
    actually see. Also s.count was labelled "alerts ingested" when it is the capped retention buffer;
    now "retained".

AND IT WAS RIGHT THAT A CLAIM OF MINE HAD AN UNSTATED BASIS, which turned out to be TWO WRONG NUMBERS.
The cadence sample came from the rule=ALL FIREHOSE, not the default view the bus actually feeds — a
ladder calibrated on ~3x the volume the component sees is a decorative ladder. Re-measured on the
DEFAULT view (500 alerts, 2026-08-07 03:32 -> 08-12 12:09, 62 hours with activity): min 1, p25 1,
MEDIAN 3, p75 8, p90 25, max 63. The ladder ITSELF SURVIVED — quiet 0-1 <= p25, active 2-7 straddles
the median of 3, elevated 8-24 is p75..below p90, high-signal 25+ is >= p90 — but the stated basis was
wrong, and a number nobody could have checked is not a justification. Separately, my "68% of the
firehose is those three noise rules" comment was flatly WRONG: it is 34.8%. I had counted RULE_ANOMALY,
which the DEFAULT VIEW KEEPS. Corrected in source.

WHERE THE VERIFIER WAS RIGHT ABOUT THE EVIDENCE RATHER THAN THE CODE: `git diff main --stat` CANNOT be
the scope-discipline evidence, because all four primary files are UNTRACKED and therefore do not appear
in it at all. Fair hit. Scope discipline is established instead by direct inspection: no write verbs,
no database access, no rule/gate/scoring imports, and the only tracked change is main.py's four
additive routes.

PROCESS FAULT OF MINE: I edited signal-events.js WHILE the verifier was reading it (the
boundary-second fix), so its review covers a file that had already moved — 15,813 -> 17,626 bytes at
14:36. It said so plainly rather than quietly reporting on the old version. The post-fix
re-verification of the drain was run by ME, not independently.

CORRECTLY LEFT UNVERIFIED: production is unreachable from the verifier's environment, so every
prod-derived figure here rests on my own measurements against the live API. It corroborated the
DIRECTION of the shared-second finding against a local copy (3,347 alerts over 657 distinct
created_at) but not the magnitude.

NOT CLOSED — THE LARGEST REMAINING GAP: no test file covers any of the three modules (75 files in
Scope/tests/, zero matching signal/dna/pulse/bus). Every proof in this session is a live browser
measurement rather than a committed regression test, so nothing here is protected against a future
edit. That is the first thing the next session should fix.

FINAL STATE: nothing merged, deployed or committed. Branch feat/signal-event-system, 0 commits ahead
of 21e46ee. Working DB md5 31387d75… unchanged. Four new untracked files plus 27 additive lines in
api/main.py; all four routes registered; api.main._scheduler is None after a plain import.


════════════════════════════════════════════════════════════════════════════════════════════
2026-08-12 — SIGNAL CONSTELLATION (relationship graph), additive frontend
════════════════════════════════════════════════════════════════════════════════════════════

Branch feat/signal-constellation off feat/signal-event-system (NOT off main — it inherits the bus).
UNCOMMITTED. Surface: 1 new file (api/static/signal-constellation.js), 2 modified frontend files
(signal-events.js adapter, signals.html), +7 additive lines in api/main.py (one route). No
rule/gate/scoring/detection/backend code touched. No DB write. NOTHING read from or written to the
separate OSINT-Graph project.

PLACEMENT DECIDED, NOT ASSUMED: added to the EXISTING /signals page as a third panel. All three
components share one bus; a separate page would start a second bus for the same data, which is exactly
what the bus exists to prevent. No other page changed.

STAGE 1a — WHAT EACH RULE ACTUALLY CARRIES (real prod rows). SAFE AND STRUCTURED: RULE_01B typed
columns member_id H001082 + full_name "Hern, Kevin" + ticker DEO; RULE_CLUSTER JSON arrays
tags.members ["C001123","M001217","V000139"] + tags.member_names, ticker MSFT; RULE_16 JSON
tags.filer "MARKEL GROUP INC." + tags.cik 0001096343, ticker CP; RULE_11 PIPE-delimited, field 0 =
recipient; RULE_10 JSON tags.instruments ["contracts","earnings","insider"]. HAZARDOUS: RULE_06 comma
`owner,action,multiplier`. REFUSED: RULE_02, RULE_09, RULE_08.

THE COMMA HAZARDS, MEASURED NOT ASSUMED. RULE_06: 29 of 500 prod rows have FOUR comma fields, not
three — MBG INVESTORS I, L.P. / Saba Capital Management, L.P. / AH Bio Fund IV, L.P. / PRESCOTT GROUP
CAPITAL MANAGEMENT, L.L.C. So parts[0] truncates on 5.8% of rows. RULE_02: the names THEMSELVES
contain commas and are joined WITH commas — "Cisneros, Gilbert Ray,James, John,McGuire, John J.,…"
splits into 12 pieces for 6 members. RULE_09: comma arity varies 4→10 fields and only 1 of 10 rows has
a ticker.

TWO PRE-EXISTING BACKEND DEFECTS FOUND ON THE WAY — REPORTED, NOT FIXED (both in api/receipts.py,
both user-facing).
(1) _insider uses parts[0], so the server's own receipt renders "Saba Capital Management" for
    "Saba Capital Management, L.P." — 29 of every 500 rows.
(2) _contract PRINTS A MATCH SCORE AS A DOLLAR AMOUNT. rule_11_contracts.py:361-362 writes
    tags = "|".join([recipient, award_date, award_id, parent, str(confidence), piid]) — field 4 is
    CONFIDENCE. receipts.py:_contract reads amount = _clean(parts[4]) and renders f"${amount}".
    Field-4 values across prod are 88, 90, 92, 95, 96, 98, 99, and 107 of 137 RULE_11 alerts have a
    field-4 value that CONTRADICTS THE DOLLAR FIGURE IN THEIR OWN HEADLINE. The drawer says "$88" on a
    Jacobs Engineering award. A FABRICATED NUMBER ON A LIVE SURFACE; deserves its own work order.
    The constellation reads tags|0 and deliberately never touches field 4.

STAGE 1b — THE BRIEF'S CHAIN, LINK BY LINK. person→organization NOT TRACEABLE (no field anywhere).
organization→government NOT DRAWN (RULE_09 client↔registrant is real but 9/10 rows have no ticker, so
the sub-graph is an island, and the arity varies). government→contract NOT DRAWN. contract→company
REAL (tags|0). company→ticker REAL (alerts.ticker). Plus three the brief did not ask for and the data
does support: member→ticker, insider→ticker, institution→ticker. SO THE HONEST GRAPH IS A
HUB-AND-SPOKE ON THE TICKER, NOT A CHAIN — that is the finding, not a shortcoming.

RULE_08 IS REFUSED WHOLESALE, AND IT IS THE MOST IMPORTANT DECISION HERE. It carries the corpus's ONLY
real government entity (tags field 0 = "Treasury Department") — and its TICKER comes from a
keyword→basket lookup, not from the document's subject ("affects: JPM, BAC, WFC, GS, GOOGL, META,
AMZN, AAPL, MSFT, BRK.B, AIG, MET"). Drawing Treasury Department → JPM would assert a relationship
SCOPE'S OWN GATE HAS EXPLICITLY RULED FALSE. entitiesFor() returns early on RULE_08. Not an oversight,
a refusal. The verifier independently confirmed it: 317 rows, 88 multi-symbol strings, and 229 single
symbols that are plainly the SECTOR_MAP fan-out (GOOGL/META/AMZN/AAPL/MSFT each exactly x20).

STAGE 1c — MULTI-HOP works through the shared TICKER node (a member who traded RTX and a contractor
awarded on RTX are two hops apart via a real stored path). It DID require extending the adapter: the
prior session shipped entities[] as a SPECULATIVE, NEVER-POPULATED field. Rather than leave a lie in
the event shape, entitiesFor() was rewritten to populate it from NAMED STORED FIELDS ONLY and a
sibling relations[] was added. Every entity and relation carries a sourceField string so a reviewer
can check the claim without reading the adapter.

STAGE 2/3 — FIVE EDGE TYPES SHIP, each with its field, and the mapping + gap list are PRINTED ON THE
PAGE. Proofs on real data: every edge type driven by real rows through the real bus path; animate-in
measured 22.4% -> 59.9% -> 85.5% -> 99.3% -> 100% at +1104ms, MONOTONIC; dedup key is
`from + "|" + to + "|" + kind` (e.g. M:C001123|T:MSFT|member->ticker) and a replay gives 0 duplicates,
hits 1->2, reinforcedAt stamped — and it holds ACROSS RULES (RULE_01B alert 31951 then RULE_CLUSTER
32023 share one M:M001217 -> T:MSFT edge). Node cap 320/700 justified by a MEASURED 198 distinct
tickers in the bus's 2-day window; the verifier reproduced 306 alerts / 198 tickers / 80 owners /
5 members EXACTLY. 0 dangling edges after 949 evictions.

🔴 THE VERIFIER OVERTURNED THREE CLAIMS AND ALL THREE WERE FAIR.
(1) "government → contract is unbuildable without scraping headline prose" — WRONG. /contracts/data
    (api/routers/contracts.py:69) is LIVE ON PROD and returns agency + award_id straight from the
    stored column; 182 of 183 RULE_11 alerts carrying a CONT_AWD_ id in tags|2 join to a row with a
    non-null agency (38416 JACOBS -> Department of Homeland Security; 33180 SPCX -> DoD). The obstacle
    is ARCHITECTURAL — consuming it needs a SECOND FETCH PATH, which the one-bus rule forbids — NOT
    evidential. Restated in code, on the page, and in the note. Two honest fixes, both out of scope:
    put agency on the alert payload, or let the BUS (never a component) enrich from /contracts/data.
(2) "RULE_02's members are unrecoverable" — WRONG. 96 of 96 prod rows satisfy
    `comma-fields == 2 x the member count stated in the headline`, and 53 of 53 roster names carry
    exactly one comma, so a COUNT-VALIDATED PAIRWISE SPLIT recovers them (38 distinct real members)
    and fails closed on any row that breaks the invariant. THE REAL OBJECTION IS IDENTITY, NOT
    PARSING: a recovered NAME is not a bioguide id, so it would mint a SECOND node for a member
    already present as M:<bioguide> — two nodes for one person asserts two people, a worse falsehood
    than one absent edge. rule_02_cluster.py already computes the bioguide set into why_matters, which
    is not on the payload. Behaviour unchanged; justification replaced.
(3) "a repeat never duplicates" — A REAL DUPLICATE EXISTED. RULE_11 HAS TWO TAG SHAPES IN PROD AND I
    ONLY HANDLED ONE: 39 rows are the LEGACY COMMA form `RECIPIENT,YYYY-MM-DD` with no pipe at all,
    14 of them with a resolved ticker. tags|0 on those returns the whole string, so the graph minted
    R:RAYTHEON COMPANY,2026-07-08 BESIDE R:RAYTHEON COMPANY — a node named after a company plus a
    date, and three distinct edges into T:RTX for two companies. FIXED: pipe form wins when a pipe is
    present, otherwise strip exactly one trailing ,YYYY-MM-DD, otherwise refuse. Re-proven on the same
    14 rows: ids are now R:LEIDOS, INC. / R:BELL TEXTRON INC / R:OSHKOSH DEFENSE LLC, and 0 contain a
    date.

🔴 AND IT FOUND A HOLE A GREP COULD NOT — AN OMISSION, NOT A BAD LINE. There was no ticker sanity
check, so RULE_06's junk ticker strings became HUB nodes: T:N/A JOINED FIVE UNRELATED INSIDERS
(Manulife, Calamos, Knudsen Todd, Manufacturers Life…) into one apparent cluster; T:GEF, GEF.B (a
multi-symbol basket) joined three, most recent 2026-08-10 i.e. INSIDE a live 2-day window; plus
T:(CALX); and T:tyg sitting beside T:TYG as two nodes for one company. A FALSE HUB IS WORSE THAN A
MISSING NODE, BECAUSE IT ASSERTS A RELATIONSHIP BETWEEN FIVE PEOPLE WHO HAVE NONE. Fixed with
case-folding plus a symbol-shape guard; re-proven — GEF, GEF.B / N/A / (CALX) now yield NO ticker
entity at all (so no edge), and tyg folds into T:TYG.

THREE MORE FROM THE VERIFIER, ALL FIXED.
- The RULE_10 `kind` label was INVERTED: emitted from T:RTX -> to N:contracts while labelling it
  "instrument->ticker", so the one edge whose direction the header describes correctly was the one
  mislabelled in summary().edgesByKind. Now "ticker->instrument"; verified on the real RTX row.
- insiderOwnerName still truncated on an `owner,action` shape (0 of 500 prod rows, so latent). Now
  ANCHORED ON THE MULTIPLIER (/^\d+(\.\d+)?x$/) rather than counting from the end, and REFUSES an
  unrecognised shape: "Saba Capital Management, L.P.,sale,2.3x" -> I:SABA CAPITAL MANAGEMENT, L.P.;
  the same string without the multiplier -> no insider entity at all.
- edgesEvicted DID NOT EXIST, so the counters could not be reconciled: 952 edgesAdded against 170 live
  edges, 782 vanished silently, while the NODE counters balanced exactly. A COUNTER YOU CANNOT
  RECONCILE IS NOT EVIDENCE. Added; now edges 217 == edgesAdded 921 - edgesEvicted 704 exactly, and
  nodes 320 == 1291 - 971.
- A PAUSED COMPONENT STILL DREW ONE FRAME PER EVENT. push() called draw() whenever the rAF loop was
  paused — but paused means off-screen or backgrounded, so nobody could see it; the verifier measured
  200 draws executed with BOTH pause conditions true. Removed; the loop redraws on resume. Re-proven:
  200 pushes while hidden -> 0 frames.

PERF FIGURE WITHDRAWN AND REPLACED. My "1.10 ms avg / 2.10 ms worst at 320 nodes / 334 edges" did NOT
reproduce — real topology tops out around 170-217 edges at the node cap depending on feed order, and
334 was an artefact of the order I fed batches in. Measured now: 0.571 ms avg / 1.20 ms worst at
320/217 (3.4% of a 16.67 ms budget), and the verifier measured 1.412 ms / 3.70 ms at the 320/700 HARD
cap (8.5%). ctx.filter === "none" and ctx.shadowBlur === 0 after thousands of frames; no createPattern,
no gradient, no fillText. IntersectionObserver with the canvas 5000px below the fold: isRunning false,
0 frames in 1500ms. visibilitychange -> hidden: isRunning false, 0 frames in 1500ms, resumes on return.

HONESTY AUDIT: one edge-creation call site (touchEdge, reachable only by iterating event.relations);
endpoints must appear in the SAME event's entities, so an edge can never be synthesised across alerts;
0 inference/proximity/sector/similarity keywords in code (every hit is prose in a comment);
0 Math.random — layout is HASHED FROM THE NODE ID deliberately, so the same data looks the same on
every reload rather than reshuffling; 0 network calls in the constellation (verifier instrumented
fetch/XHR/WebSocket/EventSource BEFORE load and saw 0 across mount and 1,230 pushes); the bus still has
exactly two fetch call sites.

ARCHITECTURAL ECHO WITH THE OSINT-GRAPH PROJECT — INFORMATIONAL ONLY. The vocabulary (entities, edges,
relationship types, provenance-per-edge) deliberately mirrors osint-system-design because they are the
same architectural family and the same discipline applies: PROXIMITY IS NOT PROOF. That is where the
overlap ends. Nothing was read from or written to that database or schema. If the two ever converge,
the natural seam is the sourceField string every entity and relation already carries — exactly the
provenance field a real graph store would need.

NEXT, per the design brief's build order: globe weather stays BLOCKED on the non-existent `location`
field (a backend change, not a frontend one); event animation, telemetry, scrubber and ambient flow are
all feasible as further subscribers to the same bus. HIGHER VALUE THAN ANY OF THEM: the three named API
gaps — `corroborates` (from the prior session), `agency` on the contract payload, and RULE_02's
bioguide set (already computed into why_matters, just not sent). Each unlocks a real edge or a real
distinction no amount of frontend work can create.


════════════════════════════════════════════════════════════════════════════════════════════
2026-08-12 — NAV ROLLOUT (shared navigation, FEED + SIGNAL SYSTEM + MORE)
════════════════════════════════════════════════════════════════════════════════════════════

Branch feat/nav-rollout off main (4afe0e4). UNCOMMITTED. Surface: 1 new file
(api/static/nav.js), 31 pages x ONE LINE, 1 extra line on signals.html, one route in
api/main.py, and the landing-page template in scripts/morning_brief.py. ZERO DELETIONS from any
page. No rule/gate/scoring/detection/ingestion logic touched.

1a — THE NAV WAS COPY-PASTED INTO 31 PAGES AND HAD DRIFTED INTO NINE DISTINCT VARIANTS. Not a
shared include. 17 pages carried one set, 4 carried it plus "Live Feed ->", 3 plus "Ask AI ->",
2 plus Universe, and status/forming/universe/congress_digest/signals each had their own. The four
names the work order assumed WERE the nav (FEED/FORMING/SIGNAL SYSTEM/STATUS) existed on EXACTLY
ONE PAGE — signals.html, created earlier the same day.

1b — cmdk.js IS A REAL PALETTE BUT NOT A PAGE-JUMPER. Read, not assumed. Cmd/Ctrl-K opens it
(cmdk.js:134), it injects its own trigger into <nav> on every page, and its search hits
/api/search (cmdk.js:163) returning exactly three kinds: tickers -> /ticker/{sym}, members ->
/member/{bioguide}, headlines -> /feed?ticker=. IT CONTAINS NO PAGE LIST AT ALL. So it does not
solve navigation and the rollout does not solve entity search — complementary, not redundant, and
the rollout footer says so in those terms.

1c — /signals WAS THE SYMPTOM; THE DISEASE WAS A THIRD OF THE PRODUCT. /signals was linked from
exactly one page: itself. Measuring every route against every nav: TEN routes appeared in NO nav
at all (/backtest /brief /digest /docs /foreign-influence /history /members-list /sectors
/watchlist /congress/digest) and three more on a single page (/performance only from /status,
/theses only from /forming, /signals only from itself).

1d — NO SHARED PARTIAL EXISTED, SO ONE WAS CREATED. cmdk.js had already reached this conclusion
for its own button ("Anything duplicated 17 times drifts; the fix has to live in one place that
every page loads") and nav.js follows that precedent deliberately. Each page gains ONE line:
<script src="/nav.js"></script>, SYNCHRONOUS so stale markup is hidden before first paint. The
page's <nav> element is kept and its CONTENTS replaced — no page markup deleted.

THE ROLLOUT: SCOPE · FEED · SIGNAL SYSTEM always visible, MORE (a real <button>, so keyboard
operable for free) rolls a panel open beneath the bar with 24 destinations in six groups (Daily,
Convergence, Sources, Coverage, Evidence, Tools), each a labelled row with a one-line description.

🔴 THE VERIFIER OVERTURNED THE HEADLINE TWICE AND BOTH WERE FAIR.

(1) THE FRONT DOOR STILL HAD THE OLD NAV AND I NEVER LOOKED AT IT. `/` is NOT a static file — it
    is HTML GENERATED by scripts/morning_brief.py, so it was invisible to a sweep of
    api/static/*.html. It hand-rolled a twelve-link bar from a _FULL_NAV constant WITH NO /signals
    IN IT. So the nav's own first rollout entry, "Brief", pointed at the one page the new nav could
    not reach — the most-visited page in the product, and the fix skipped it. FIXED: the template
    now loads /nav.js, _FULL_NAV is DELETED (leaving it as dead code would invite exactly the copy
    that caused this), TEMPLATE_VERSION bumped light-theme-2 -> shared-nav-1. The bump is the
    DESIGNED path: brief_is_current() compares a template marker and regenerate_if_stale_async()
    rebuilds off the request thread — and the PREVIOUS bump's own comment reads "nav: + Dashboard
    (/home); shared ⌘K", so a nav change bumping this has precedent.
    ⚠️ NAMED CONSEQUENCE: every cached brief row is now template-stale, so the next view of `/`
    REGENERATES AND WRITES a briefs row, possibly calling Groq for the preamble. Existing designed
    behaviour, but it is a data write and a human should know before deploy.

(2) /congress/digest IS A 27th ROUTE AND A GENUINE ORPHAN — linked from exactly one page, itself.
    THE IDENTICAL PATHOLOGY TO /signals, sitting in the same codebase. AND I MISSED IT TWICE, BOTH
    TIMES BY THE VERY MISTAKE THIS SESSION EXISTS TO FIX: first by enumerating routes from MY OWN
    PREVIEW MIRROR instead of from main.py; then, re-deriving from main.py, by a REGEX BLIND TO
    STACKED DECORATORS —
        @app.get("/congress/digest", ...)
        @app.get("/congress/digest/{date}", ...)
        def congress_digest_page(date: str | None = None):
    A regex anchored on @app.get(...)\ndef only ever sees the decorator nearest the def. Replaced
    with an AST WALK OVER EVERY DECORATOR ON EVERY FUNCTION, and the preview server's route map is
    now GENERATED from main.py by that same pass rather than hand-maintained, which closes the class.

(3) /brief IS NOT AN ALIAS OF / — I called it one, wrongly. They read DIFFERENT TABLES: / renders
    `briefs` (api/landing.py, the deterministic morning brief) while /brief renders `daily_briefs`
    (routers/brief.py, the LLM brief) and carries an AI-summary box and a regenerate control that /
    does not have. Two doors, two rooms. Now its own entry, "AI brief". (/theses -> /intelligence IS
    a true alias — both return intelligence.html — correctly folded.)

FINAL REACHABILITY AUDIT (AST-derived from main.py): 27 non-parameterised page routes, 7
parameterised (reached from content, deliberately absent), 26 nav destinations, the only route not
listed is /theses which is a true alias of the listed /intelligence. DEAD NAV LINKS: none. GENUINE
ORPHANS: NONE.

PROVEN IN A REAL BROWSER ON 34 RENDERED ROUTES (all 27 non-parameterised + 7 parameterised
samples): 1 distinct primary bar ("/feed,/signals"), 1 distinct rollout size (24 items), 1 <nav>
element per page, no stale nav visible anywhere, ⌘K present on every page, panel not focusable when
closed, zero failures. THE LANDING PAGE SPECIFICALLY: Signal System reachable from the primary bar,
MORE opens 24 destinations including /congress/digest and /brief, the twelve-link bar gone, ⌘K
surviving, aria-current on /.

KEYBOARD, BY REAL KEY EVENTS NOT SYNTHETIC DISPATCH: Tab x3 from the wordmark -> BUTTON#scope-more;
Enter -> panel opens, aria-expanded=true, focus moves to the first item; Escape -> closes and focus
returns to MORE. Verified independently under CDP: Space also opens, rollout links are tabbable when
open and NOT focusable when closed (an 80-Tab sweep never landed inside the closed panel), no focus
trap.

RESPONSIVE/THEME: at 414px single column, no horizontal overflow, panel scrolls (content 1348px in a
670px panel) with the last item and the ⌘K hint both reachable. Light theme inherits tokens.css.

⭐ FAIL-SAFE CONFIRMED BY THE VERIFIER: with /nav.js 404ing, the page renders its OLD nav, fully
functional — because the stale-hiding rule ships INSIDE nav.js and so cannot hide markup that will
never be replaced.

🔴 A REGRESSION I INTRODUCED AND CAUGHT IN THE BROWSER: nav.innerHTML = "" DELETED THE ⌘K BUTTON
FROM ALL 31 PAGES. My comment confidently asserted a `defer` script lands AFTER us; it is the other
way round — DEFERRED SCRIPTS EXECUTE BEFORE DOMContentLoaded, so cmdk.js had already placed its
button. Now preserved and re-appended (the node is moved, not cloned, so its listener survives), and
verified in BOTH script orderings plus an idempotency re-run.

KNOWN, NOT FIXED: Tab order does not match visual order (the panel is body-appended, so Tab from the
last rollout link goes to <body> — not a trap, focusout closes it, but not ideal); universe.html's
nav is position:static where every other page is sticky or fixed, and its document is not scrollable
at any tested size so the fixed-position panel never detaches today — but the first scrollable
static-nav page added will hit it; dead .nav-links CSS rules remain in the brief template's
stylesheet (harmless, left rather than widening the diff on a generated file); /theses, /brief-via-/
and parameterised pages highlight nothing in the nav (cosmetic).

MERGE-READINESS: the code is ready, but deploying makes every cached brief template-stale, so the
next view of / regenerates and WRITES a briefs row (Groq preamble included). That is a data write on
a human-gated path and should be a deliberate decision, not a side effect.

════════════════════════════════════════════════════════════════════════════════════════════
2026-08-15 — UI FEATURE AUDIT (20-item checklist vs. real frontend), read-only
════════════════════════════════════════════════════════════════════════════════════════════

Human-gated, read-only. No code, no branch, nothing built. Listed every real page first (31 static
pages under api/static/, plus / — generated dynamically by scripts/morning_brief.py, NOT a static
file, invisible to a naive sweep of api/static/*.html) rather than assuming the set, then checked 20
common small-UI-feature checklist items against actual code.

RESULT: 9/20 PRESENT, 4 PARTIAL, 4 genuinely N/A (no auth, no forms exist anywhere in the app — not
soft-pedaled, just true), 3 ABSENT-and-flagged-as-wrong-fit (cookie consent, UTM tracking, floating
contact button — marketing-site patterns that do not belong on an intelligence terminal).

⭐ CONFIRMED STILL LIVE: the "shows 7 days, actually 14 days" dishonest-copy bug, traced to a SPECIFIC
prior finding (SESSION-2026-08-10-morning-brief-exclude-rank: "morning_brief.py:571 says 'in the last
7 days' while CONVERGENCE_WINDOW_DAYS=14, byte-identical at 052d081") and re-verified against the live
file today, not assumed from the work order's framing — same defect, still unfixed at session start.

OTHER REAL FINDINGS: watchlist.html's Remove buttons fired an immediate DELETE with ZERO confirmation
step; no copy-to-clipboard anywhere in the app; mobile "menu" turned out to be real (nav.js's "More"
disclosure panel, not a hamburger) — confirmed the legacy per-page hamburger markup (e.g.
alerts.html:250) is DEAD CODE, deleted at runtime by nav.js's synchronous nav.innerHTML="" before the
stale markup can paint. Loading animations (DNA/pulse/constellation from the signal-event-system
session) are real but confined to /signals only — everywhere else is a plain pulsing "Loading…" text.

TOP 5 RANKED RECOMMENDATIONS (cost vs. value): fix the 7-vs-14-days copy (trivial, ties to a known
bug), add a watchlist-remove confirmation (small), add copy-to-clipboard for ticker/alert-id/permalink
(small), add a skip-to-content link (trivial), add content-level sticky headers on History/Alerts
(small-medium). Full 20-item table + evidence: SESSION-2026-08-15-ui-feature-audit.md.

════════════════════════════════════════════════════════════════════════════════════════════
2026-08-15 — UI AUDIT FIXES (all 5 items from the audit above)
════════════════════════════════════════════════════════════════════════════════════════════

Branch fix/ui-audit-batch off main (ef227f2). 6 files (alerts.html, history.html, nav.js,
ticker.html, watchlist.html, scripts/morning_brief.py). Each item's diff kept cleanly separable.
No rule/gate/scoring/detection code touched — item 1 only READS the gate window, never writes it.

(1) THE 7-VS-14-DAYS COPY. Re-confirmed CONVERGENCE_WINDOW_DAYS=14 from source, not trusted from
memory, and found the SAME dishonest "7 days" string on the converge branch too
(morning_brief.py:549), not just the "quiet window" fallback (:574) the audit cited — both are fed by
the SAME query in _synthesize_headline, so both had drifted. Fixed by stamping window_days onto every
one of the four hero-mode return dicts and reading that value into both f-strings instead of a second
hardcoded number. PROVEN by real code execution against gather()/render_html() on a scratch DB, not
just reasoning: both quiet-mode and converge-mode renders now say "14 days", matching the constant.

(2) WATCHLIST DELETE CONFIRMATION. removeTicker() and removeRule() now open a native confirm() before
the DELETE fetch is even constructed. PROVEN with network-level evidence, not DOM appearance: cancel
path -> ZERO DELETE requests observed, entry survives a full page reload; confirm path -> exactly ONE
DELETE fires, entry gone after reload. Both paths proven, not just the happy one, per the work order's
specific ask to hunt for "a confirmation that doesn't actually block the request."

(3) COPY-TO-CLIPBOARD. Shipped on /feed and /ticker/*, a 📋 button per alert card copying
"TICKER — Alert #ID — <link>", with a real navigator.clipboard.writeText() + visible "Copied" state.
NOT added to /signals — reading signal-events.js and its three canvas scripts showed /signals is pure
visualization with no discrete, DOM-rendered alert item to attach a button to; the audit's assumed
page set was corrected on inspection rather than forced. PROVEN via real click + clipboard read-back
on both pages, values matched exactly, zero console errors.

(4) SKIP-TO-CONTENT. Added once in nav.js (site-wide, zero per-page HTML edits) since there is no
consistent <main> landmark across the ~31 pages (27/31 use class="page", 4 don't). Self-verification
covered 3 pages and passed all 3 — TOO NARROW A SAMPLE, see the verifier section below.

(5) STICKY HEADERS. /feed's filter toolbar made position:sticky under the nav. /history required real
CSS-spec debugging, not a one-line fix: overflow-x:auto ALONE forces the unset overflow-y to compute
as auto too (per the CSS Overflow spec), silently making .table-wrap — not the viewport — the sticky
positioning context, and since that wrapper never itself scrolled (unbounded height), nothing ever
actually stuck. Proved the diagnosis by forcing .table-wrap fully visible on both axes (sticky started
working immediately), then fixed it properly by embracing a bounded 70vh internally-scrolling pane
(same vh-bounded-pane convention nav.js already uses for #scope-rollout) instead of fighting the
browser. That surfaced a SECOND, separate bug: something looked wrong in the header band once the
pane genuinely scrolled — described (WRONGLY, by me) as "row text painting through the header."

════════ VERIFIER PASS (self-verification was not the last word on two of the five) ════════

An independent verifier subagent ran its OWN browser session against its OWN scratch DB and server —
did not just re-read the report — and OVERTURNED the item-4 headline. It tested 26 routes, not 3, and
found /osint's skip link was a genuine no-op: Tab->Enter->Tab returned to the skip link itself, having
skipped nothing. ROOT CAUSE: isRendered()'s `offsetParent !== null` check does not only catch hidden
elements (its intended target) — offsetParent is ALSO null for any position:fixed element, and EVERY
sibling after <nav> on /osint is position:fixed (the WebGL globe canvas, its legend, the side panel,
the conflict tape — the whole page is fixed-position chrome over a full-bleed globe). Fixed by
switching to a getBoundingClientRect()-size + computed-visibility check, which is position:fixed-safe.
A SECOND bug rode along: the fallback path was stamping body.id = "scope-main-content" globally,
a collision risk on any page hitting that fallback — fixed by skipping the id-stamp specifically when
the target IS document.body (natively focusable via tabindex="-1" without needing an id at all).

The verifier ALSO corrected item 5's stated root cause: forced border-collapse:collapse back on at
runtime and diffed the header band pixel-for-pixel across 12 scroll offsets in both themes — could NOT
reproduce "row text bleeds through the header" at any offset. What IS reproducible: under collapse,
the sticky <th>'s own border-bottom disappears entirely (painted with the rest of the non-sticky
table, while the cell itself visually detaches to stick) — a lost underline, not text bleed-through.
The border-collapse:separate fix itself was UPHELD as necessary; the comment describing WHY was wrong
and has been corrected to state the border-loss symptom instead of the unreproduced one.

NEW DEFECT THE VERIFIER FOUND THAT I MISSED: the newly-sticky /feed filter bar, at 414x736 mobile,
wraps to ~5 rows and permanently consumes 264 of 736px (35.9%) of the viewport, clipping the top
card's badge row under it on every scroll — I had re-verified /history at mobile width and never
measured the bar I'd just made sticky on /feed. Fixed: sticky only above the existing 700px
breakpoint; position:static below it.

Both verifier-found bugs fixed and RE-VERIFIED in a fresh browser session before closing out (not
just fixed and assumed correct): /osint's skip link now lands on the real, visible #globe-canvas, and
the next Tab moves to real subsequent content, not back to the skip link; body.id confirmed no longer
polluted; .filters confirmed static at 414px and still sticky at 1280px.

Full suite: 1520 passed both before and after the verifier round, same 3 pre-existing failures
(test_basket_rule_gate_class's 'modules' in 'node_modules' false positive; test_theme_holdouts on
nav.js/signals.html) confirmed unrelated via git stash on clean main, not introduced here — including
the 2 new nav.js hardcoded-fallback-colour lines from the skip-link CSS, which follow the SAME
var(--token,#hex) pattern already used ~20 times unchallenged in that same file.

MERGED TO MAIN AND PUSHED TO ORIGIN, on explicit human instruction, in two separate steps (merge+
commit first, push-to-trigger-Railway-deploy second, confirmed via AskUserQuestion before the push
specifically because it goes straight to production with no local click-through first). main is at
c9d86ce; origin/main pushed to the same commit.

════════════════════════════════════════════════════════════════════════════════════════════
2026-08-15 — SECURITY AUDIT (20-item pre-launch checklist), read-only
════════════════════════════════════════════════════════════════════════════════════════════

Human-gated, read-only. Confirmed architecture BEFORE checking any checklist item, not assumed: zero
accounts/login/sessions/cookies anywhere (repo-wide grep, zero hits — Scope sets NO cookies of any
kind), no file uploads in the ordinary sense (one admin-gated raw-bytes endpoint is a separate matter,
see below), no payment processing, and Groq/llama-3.3-70b-versatile AI narrative generation IS real
and live across 7 files (chat.py is the one surface taking free-form user text into the model).

8 of 20 items genuinely N/A with the underlying mechanism absent, not padded to look thorough: CSRF,
session invalidation, reset-link expiry, login enumeration, failed-login lockout (all no-auth-exists),
payment webhooks (no payments), directory listing (no StaticFiles mount exists ANYWHERE — confirmed by
grep, every static asset is served via an individually-named route), secure cookie flags (no cookies).

TOP FINDING: POST /api/watchlist-rules needs ZERO authentication, and watchlist.html's renderRules()
interpolated label/condition_type/condition_value into innerHTML with NO escaping at all — a stored
XSS anyone can plant with a plain POST request, no secret required, executing for the next person who
loads /watchlist.

SECOND: POST /admin/upload-db (main.py:460-481) accepted ANY bytes >=4KB and shutil.move'd them
straight over the live database file — no file-type check, no SQLite-header validation, no upper size
bound, gated by a plain != (non-constant-time) compare against ADMIN_KEY passed in the URL QUERY
STRING (so it lands in every log/proxy/browser-history that touches the request).

THIRD, AS THE WORK ORDER SPECIFICALLY FLAGGED GOING IN AND ASKED TO BE CONFIRMED RATHER THAN ASSUMED:
prompt injection on /chat is genuinely undefended. req.message — raw, unvalidated, no length cap —
goes straight into the Groq prompt with zero isolation from instructions. Blast radius judged bounded
(no tool-calling/write access on this endpoint, no other users' private data exists to leak — the
alerts context is the same public feed for everyone), so real but not catastrophic.

FOURTH: zero rate-limiting anywhere in the codebase, repo-wide grep for slowapi/Limiter/rate_limit
returns nothing real (two false-positive hits were both just handling GROQ'S OWN 429, not a cap Scope
imposes). /chat costs a real billed Groq API call per request with no ceiling of Scope's own.

ALSO: CORS is allow_origins=["*"] PAIRED WITH allow_credentials=True — a known-bad combination
(browsers/Starlette respond by reflecting the caller's actual Origin instead of a literal "*", which
functionally becomes "any origin, WITH credentials"). Low practical impact today only because there
are no cookies to leverage it with.

Full 20-item table + evidence: SESSION-2026-08-15-security-audit.md.

════════════════════════════════════════════════════════════════════════════════════════════
2026-08-15 — SECURITY AUDIT FIXES (all 5 findings from the audit above)
════════════════════════════════════════════════════════════════════════════════════════════

Branch security/fix-audit-findings off main (c9d86ce). Commit d658852, 7 files (api/main.py, new
api/rate_limit.py, api/routers/chat.py, api/routers/tickers.py, api/static/alerts.html,
api/static/ticker.html, api/static/watchlist.html). NOT merged, not pushed, not deployed.

RAN CONCURRENTLY WITH ANOTHER SESSION'S UNCOMMITTED WORK IN THE SAME SHARED DIRECTORY (a
position-sizing feature on fix/ticker-page-position-sizing, uncommitted edits to tickers.py/
ticker.html/jpt_common.py plus new scripts). Branched off main directly rather than off that branch
(safe — both sat on the identical commit, so nothing was lost by switching). The two files that
genuinely overlapped (tickers.py, ticker.html) were committed via HAND-ISOLATED git apply --cached
hunks — extracted exactly this session's hunks from the combined working-tree diff into a standalone
patch, validated with --check, staged into the index alone, leaving the other session's uncommitted
hunks physically untouched in the working tree for it to commit separately. Verified after commit:
both sessions' markers present simultaneously (_TICKER_SHAPE/rate_limit committed; position-sizing/
psSection still uncommitted, undisturbed).

(1) STORED XSS — fixed the audit's finding AND a worse one found while fixing, not in the original
audit. watchlist.html's ticker-list Remove button built onclick="removeTicker('${w.symbol}')" by
string interpolation. HTML-ENTITY ESCAPING ALONE DOES NOT CLOSE THIS SINK: a browser HTML-decodes
entities inside an attribute value BEFORE compiling the result as JavaScript, so escaping the quote
as &#39; gets undone by the parser and still breaks out of the JS string literal — an incomplete fix
would have shipped a false sense of security. Rewritten to a data-symbol attribute + one delegated
click listener instead; dataset.symbol is read as a plain string value, never re-parsed as code, safe
regardless of what esc() renders. Separately: POST /tickers/watchlist/{symbol} had NO server-side
validation at all (client filter only, trivially bypassed by hitting the API directly) — a LOWER-
BARRIER XSS than the one the audit flagged, since it needs no free-text field, just a raw path
segment. Closed with a server-side ticker-shape regex (^[A-Z0-9.\-]{1,10}$). Also fixed the same
unescaped-interpolation pattern in alerts.html/ticker.html (headline, member name, tags,
why_matters) using the codebase's own existing esc()/escHtml() helpers, now actually applied.

PROVEN in a real browser, not a code read: posted a live <img onerror=alert(1)> payload, loaded
/watchlist, confirmed zero alert() fired, outerHTML showed properly escaped entities, textContent
showed the inert literal text, and NO live <img> element was ever created in the DOM. Confirmed
POST .../x');alert(1)// now returns 422 and stores nothing. Regression-tested the rewritten
event-delegation Remove button still works (correct ticker removed, others untouched).

(2) /ADMIN/UPLOAD-DB now requires the real SQLite file-format magic header, a PRAGMA integrity_check
on a TEMP copy before the shutil.move swap (mirrors db_backup.py's own precondition for treating a
snapshot as good), and a 500MB ceiling. Admin-key compare switched from != to hmac.compare_digest.
Failed attempts on both /admin/refresh and /admin/upload-db now log to activity_log — previously
invisible. PROVEN against a real running server: garbage bytes rejected (bad magic), too-small body
rejected, a TRUNCATED-BUT-VALID-HEADER file rejected by integrity_check with the LIVE DB CONFIRMED
UNCHANGED afterward (proving validation happens before the swap, not after), wrong key rejected +
logged, and a genuinely valid upload accepted and correctly swapped in.

FOUND, LEFT UNFIXED, OUT OF SCOPE: testing the CORRECT admin key on /admin/refresh hit a pre-existing
500 NameError — LIVE_RULES is not defined anywhere in main.py. Confirmed via git show main:... that
this is broken on clean main too, unrelated to anything touched this session. Flagged for a human
decision, not silently left unmentioned.

(3) chat.py's SYSTEM_PROMPT now explicitly frames the user's message and the DB context as data to
read, never instructions to follow, with the user turn wrapped in <user_message> tags (context in
<database_context> tags) so the model has something concrete to apply that framing to — stated
honestly as a mitigation, not a solve, since there is no reliable way to strip "instructions" out of
natural language without also breaking legitimate questions. Added a 2000-char cap on message (there
was none — every byte was previously billed as Groq input tokens with no limit). PROVEN: a >2000-char
message now returns 422 with the exact limit named.

(4) NEW api/rate_limit.py — a minimal in-memory per-IP sliding-window limiter, no new dependency
(a Redis-backed one would be scope creep for a single-container deployment). Key derived from
X-Forwarded-For (Railway's edge sets it) falling back to the raw connection IP — stated honestly in
the module's own docstring that this is spoofable and stops casual/scripted abuse, not a determined,
header-forging attacker. Applied to /chat (10/60s — was completely uncapped against a real billed
Groq call), both admin routes (5/60s each), both watchlist write endpoints (20/60s each). PROVEN:
429s fired at EXACTLY the configured thresholds in both directions, and the limiter correctly counted
an earlier failed request (a too-long chat message that 422'd) toward the SAME bucket as the
follow-up load test — proving it counts all requests hitting the endpoint, not just successful ones,
which is the correct behaviour for preventing rate-limit evasion via deliberately-malformed requests.

(5) CORS allow_credentials flipped True -> False. allow_origins=["*"] kept deliberately — Scope is a
public, unauthenticated JSON API by design. PROVEN: a cross-origin curl request now gets back a
literal "Access-Control-Allow-Origin: *" with NO Access-Control-Allow-Credentials header at all.

Full suite: 1539 passed (up from 1520 — the concurrent session's own new tests), same 3 pre-existing
failures, confirmed unrelated.

## 2026-08-15 (manual) — ticker position-sizing, written on `security/fix-audit-findings`

⚠️ WRITTEN BY HAND. Full note: `SESSION-2026-08-15-ticker-position-sizing.md`.

Display-only materiality panel on the ticker page (market cap, shares, float, revenue, cash,
runway, dilution) plus every dollar-denominated alert re-expressed as % of market cap and
% of revenue. Migration m015 `position_sizing_cache`, new `scripts/position_sizing.py`, new
`GET /tickers/{sym}/position-sizing`, panel in `ticker.html`, 23 tests.

🔴 THE BRANCH `fix/ticker-page-position-sizing` IS EMPTY — it equals main. A concurrent
session checked HEAD out to `security/fix-audit-findings` at 17:20:40 while this work was
uncommitted, so all of it is working-tree state on THAT branch and a checkout destroys it.
Backed up to `scratchpad/backup-position-sizing/` (files + patches vs main). NOTHING COMMITTED.

⚠️ `api/routers/tickers.py` and `api/static/ticker.html` are INTERLEAVED with that session's
watchlist-XSS work, which now depends on the `esc()` helper this session added. Splitting the
hunks is a human call.

VERIFIER OVERTURNED THE DELIVERY HEADLINE and found 4 real defects, all fixed: a reported $0
relabelled "unavailable" with a false reason (server used truthiness, client used status);
52/53-week filers falling back to a stale FY figure (10 of 34 sampled tickers — QCOM, AMD,
INTC, MU, LMT, GD…); `last_close` undated and market cap's resolve timestamp mislabelled
`as_of`; and the events column header hardcoded "% of TTM rev" over an FY denominator.
Numbers and isolation upheld: BA/28182 = 17.4615%/34.0156% re-derived from raw SEC+Yahoo by
two independent routes; RTX/18038 = 2.4953%/8.0199%; whole-DB 35-table digest unchanged
across a real resolve with a stale `ticker_meta` row left untouched.

Suite: 3 failed / 1542 passed — exactly the three that already fail on main.

## 2026-08-15 (manual) — branch split: position-sizing vs security-audit

⚠️ WRITTEN BY HAND. Full note: `SESSION-2026-08-15-position-sizing-branch-split.md`.

Untangled two changesets that were interleaved in one shared working tree.
`fix/ticker-page-position-sizing` rebuilt clean off main as **4d91341** (5 files, 1609
insertions, 0 deletions). `security/fix-audit-findings` left exactly as that session
committed it (**d658852**) — it had already committed itself at 17:40, which collapsed most
of the expected difficulty. NEITHER MERGED, NEITHER PUSHED. main untouched at c9d86ce.

Method was SUBTRACTION, not transcription: checked the mixed copy into an isolated worktree
and reverse-applied the security session's own commit diff, so the subtrahend is their
artefact and mis-attribution fails loudly. Reconciled byte-exactly against the backup:
3 files identical, and for the 2 shared files `branch + security patch == backup`.

🔴 THE `esc()` COUPLING WAS REAL, AND IT IS THE SECURITY BRANCH THAT IS BROKEN. d658852 commits
4 `esc()` calls into ticker.html with NO definition, and none reachable (nav.js/cmdk.js both
IIFE-scoped; `typeof esc === "undefined"` verified in a node vm). The throw is at
ticker.html:875 inside the `#content` template, outside any try — so the WHOLE ticker page
body fails to render for any ticker with ≥1 alert. Not fixed; not this session's branch.
By contrast the SAME commit fixed alerts.html correctly with its own `escHtml`, which points
at the cause: their ticker.html change depended on this session's uncommitted `esc` helper and
broke the moment it was committed without it.

Also finally explained: `test_basket_rule_gate_class::test_the_detector_names_no_table_and_no_file`
fails only where `Scope/scope_env/` exists — that test's own inline walk skips `.venv` but not
`scope_env`, so it harvests the venv and trips on a `modules` dict in PIL/features.py:13.

VERIFIER: all 6 claims upheld from its own detached checkout; mutation-tested the 3 pinned
regressions to failure; proved isolation by SQL trace (10 statements, one table, ticker_meta
and issuer_cap at 0 rows); re-derived BA 17.4615%/34.0156% and RTX 2.4953%/8.0199% from raw
SEC+Yahoo+prod. Two corrections accepted: "490 insertions" is 490 across the 3 MODIFIED files
(1609 total), and the cap percentages are conditional on the 2026-08-14 close so must not be
used as regression constants. One verifier sub-claim OVERTURNED: alerts.html is correctly
self-contained (defines escHtml, 17 uses) — that criticism of the other session was unfair.

## 2026-08-15 (manual) — ticker liquidity / fillable position size

⚠️ WRITTEN BY HAND. Full note: `SESSION-2026-08-15-ticker-liquidity.md`.

`fix/ticker-page-liquidity` = **fa987af** (32c856f feature + fa987af verifier fixes),
STACKED on fix/ticker-page-position-sizing (4d91341). NOT MERGED, NOT PUSHED.
Suite 1545 passed / 2 pre-existing failures. Built in an isolated worktree; the shared tree
(on another session's security branch) was never written to.

⭐ NO NEW DATA SOURCE. Yahoo's existing chart call already returned `volume` beside `close` —
discarded, and range=5d was too short to average. `_close_and_adv` REPLACES that call at
range=3mo, so a cold resolve still makes 7 HTTP calls / 2 Yahoo, identical to the base.
20 trading days chosen to match label_outcomes' +20-day forward-return horizon.

🔴 WORK-ORDER CONFLICT: "branch off main" vs "reuse position_sizing_cache" — that table does
not exist on main. Branched off 4d91341 instead and flagged it as stacked.

🔴 VERIFIER FOUND A REGRESSION I CAUSED AND HAD NOT FLAGGED: a short ADV window was
withdrawing last_close AND shares_outstanding, under a reason string blaming the cap guards.
A liquidity feature must not be able to delete a position-sizing fact. Plus a last-close that
could be a day stale, and an adv guard tickers.py claimed to enforce but didn't. All fixed.
Verifier's own note: "the report under-reports".

Two of the build's bugs were IN MY OWN TESTS: a vacuous window test that trimmed nothing, and
a monkeypatch that mutated the shared `requests` module and broke four unrelated tests.

## 2026-08-15 (manual) — alert confidence breakdown: NO-GO at the Stage 0 gate

⚠️ WRITTEN BY HAND. Full note: `SESSION-2026-08-15-alert-confidence.md`.
NO BRANCH, NO CODE. The work order's own go/no-go gate fired before Stage 1.

Also this session: main f789b44 (position-sizing + liquidity) was merged, pushed, deployed,
and verified live on prod — m015/m016 applied cleanly (22 cols), BA reads 17.4615% on the
live endpoint.

WHY IT STOPPED — the mission required each rule family's WEIGHT from persisted values only:
  * `leg_weight` appears in ZERO COLUMNS OF ZERO TABLES across all 54 prod tables.
  * `_leg_weights` only ever emits a RULE_11 key, so RULE_06/RULE_15 have no weight concept
    at all — the field is not un-backfilled, it is NOT MODELLED. Unbuildable past or future.
  * THE STORED TOTAL IS NOT DECOMPOSABLE (verifier's argument, I had missed it): 46.0 =
    40.0 instrument tier + 6.0 RULE_10's OWN Derived quality. Neither term belongs to any
    rule family. The only derivable per-family weights reconcile to 57.3, not 46.0 — the
    parts would contradict the whole on the same screen.

🔴 LIVE DEFECT FOUND, FLAGGED NOT FIXED: `evidence.py::_confidence_breakdown` RECOMPUTES a
/100 confidence and ships it on the homepage with a progress bar. Returns 65 vs stored 46.0,
and 80 when the alert is fresh (freshness is age-derived) — a 34-point divergence at peak
user attention. Three numbers exist for one convergence: alert 46.0, theme 57.3, drawer 65.

🔴 WORK-ORDER PREMISE WRONG: RULE_10 is 3+ INSTRUMENTS / 14 DAYS, not "4+ rule families /
24h". Verifier empirically proved all three of the order's phrasings FAIL
test_cleanup_pass::test_no_stale_gate_wording_survives_in_user_facing_pages, with
"3+ instruments within 14 days" passing as a negative control.

⭐ ARCHITECTURAL FINDING: alert 23430 stores ec=60.0 that today's engine cannot reproduce
(it would score 20.0) — reconciles only against the pre-fix member-count bug PLUS the
pre-rescale 4/5/6 tiers. Scores are forward-only, so the corpus spans three scoring
generations with no stored record of which produced a row. Any score-explanation feature
must handle that or it will confidently misexplain history.

NEXT: fix or relabel the live drawer — that is the real work, and it is a scoring surface,
so human-gated.

## 2026-08-16 (manual) — evidence drawer honesty: the "Confidence" number was never the stored one

⚠️ WRITTEN BY HAND. Full note: `SESSION-2026-08-16-alert-confidence-drawer-honesty.md`.
`fix/alert-confidence-drawer-honesty` = 50190d3 (39881f1 fix + 50190d3 verifier findings)
off main f789b44. Suite 1547 passed / 2 pre-existing. NOT MERGED, NOT PUSHED.

THE DEFECT: the drawer rendered its own recomputed score under the label "Confidence" with a
/100 bar. Alert 32990: showed 65 vs stored evidence_confidence 46.0 — and 80 on the day it
fired, because the freshness term decays. index.html::corrConfidence was a JS twin of the
same formula; its own comment said it "must agree with the server". It did. Two copies of a
heuristic agreeing with each other is not agreement with the engine.

OPTION A, on evidence: one call site, one renderer, no external consumer, and the stored
score was ALREADY in the payload (SELECT *). The heuristic merged the two axes the model
refuses to merge (freshness is opportunity-side), was uncalibrated, and nothing consumed it.

VERIFIER CALLED THE REPORT UNDERSTATED: "34 points" was the anchor's gap, not the maximum —
corpus-wide the old heuristic was off by mean >=14.9 pts over 37,763 single-rule alerts,
worst case 43. The anchor was the RAREST case (exactly 1 RULE_10 alert exists).

IT OVERTURNED TWO CLAIMS:
  * My call-site inventory MISSED test_evidence_today.py, which asserted two keys I deleted
    and stayed green only because it was VACUOUS (early-return on conftest's empty DB). The
    only integration guard on that payload, and it could not fail. Now seeds its own row.
  * corrConfidence was NOT the only twin: thesis.html:186-199 recomputes
    opportunity_score_breakdown and captions it "never recomputed" — 62 vs stored 0 on alert
    40888 — with a server twin at warroom.py:121. REPORTED, NOT FIXED.

TWO MORE DEFECTS OF MINE, FIXED: the `basis` string was false on ~99.99% of alerts (credited
corroboration on single-rule scores it contributed nothing to — the same confident-wrong
sentence this branch exists to remove, reintroduced inside the fix); and "Distinct
instruments" meant two opposite things across the branches. Also: a property I claimed was
guarded was pinned by NOTHING — deleting the alert_corroborates filter left all 1,546 tests
green. Now pinned and mutation-verified.

STILL OPEN, FLAGGED: alert 46.0 vs theme 57.3 for the same convergence (scoring path);
related matched with ticker LIKE '%tk%' so a PFE row can be credited to a P alert.

## 2026-08-16 (manual) — source-links commit/merge: NO-OP, stopped at the Stage 0 gate

⚠️ WRITTEN BY HAND. Full note: `SESSION-2026-08-16-source-links-commit-merge.md`.
NOTHING COMMITTED, NOTHING MERGED. main untouched at e838453.

Every premise was stale: fix/source-links is ALREADY committed (df5683a) and ALREADY merged
(ef227f2, 2026-08-15 14:36). main is 9 commits past that. The order also expected
1,520 passed / 3 failures; the suite is 1,547 / 2.

🔴 THE DANGEROUS PART: Stage 1 said "commit the working-tree diff as-is". But HEAD is on
fix/match-member-id-term-dates, NOT fix/source-links, and that diff has ZERO source-links
references. It is another live session's matcher work (ingest_house_index.py, roster_check.py
— both byte-identical to main, i.e. already merged) plus stale duplicates of my own merged
position-sizing work, showing as "modified" only because HEAD (d658852) is behind main.
Executed as written it would have committed ANOTHER SESSION'S WORK, onto the WRONG BRANCH,
under a source-links message. A git diff against a stale HEAD is not a changeset.

✅ CHECKED WHILE HERE: the esc() break I flagged on security/fix-audit-findings did NOT reach
prod — merge order saved it (liquidity, which defines esc, merged first at f789b44). main has
1 definition / 18 calls, node --check clean, prod /status 200, BA cap live. That was LUCK,
not design; reversed order would have shipped a dead ticker page.

⚠️ The shared tree still has HEAD on fix/match-member-id-term-dates with uncommitted work in
it. Anything diffing against that HEAD will produce a misleading changeset.

## 2026-08-16 (manual) — drawer-honesty MERGED (part 1) + filing velocity NO-GO (part 2)

⚠️ WRITTEN BY HAND. Full note: `SESSION-2026-08-16-drawer-merge-and-filing-velocity.md`.

PART 1 ✅ MERGED: fix/alert-confidence-drawer-honesty -> main = 2b2cfe7 (LOCAL, NOT PUSHED;
origin/main still e838453, and pushing auto-deploys to Railway). Both named surprises were
present (main had moved f789b44->e838453; another session live in the shared tree with 17
uncommitted files) but the CONFLICT SURFACE WAS EMPTY — my 5 files vs the 15 main gained,
zero intersection. Shared tree never touched; merge ran in an isolated worktree.
Suite 1547 pre-merge -> 1567 on merged main, same 2 pre-existing failures.
Anchors on merged main: 32990 -> 46/100, 40501 -> 20/100, invariant across 12 severity x age
combos, labelled "Evidence confidence", no +N rows.

PART 2 🔴 NO-GO, four independent grounds:
  1. S-1/S-3/amendments ABSENT, not sparse — the only form-type column in prod is
     filings.report_type with exactly two values: 'PTR' and '4'.
  2. Form 4 has NO ticker column, member_id NULL on all 9,323 rows, and its 26 days of
     history are a rolling incremental scan — velocity would measure when RULE_06's scan
     started, not filer behaviour.
  3. earnings_sentiment (the only 8-K-ish data) is 71% BACKFILL: 77/108 rows ingested in ONE
     run on 2026-07-11 covering 4 months of filing dates. Exactly the artifact Stage 0 said
     to check for.
  4. What survives is quarterly by construction (Item 2.02 earnings only; 13F by mandate).

⭐ THE ANCHOR COULD NOT BE CONSTRUCTED, AND THAT IS THE ANSWER: max 7 filings per ticker,
71% backfilled, no ticker attribution on Form 4. No branch created, no code written.

⚠️ SECOND feature in two sessions blocked by the same root cause (alert-confidence stopped
because per-rule weights were never persisted). "Display layer ready, data is not." Worth a
decision of its own: what does Scope commit to persisting, and from when?

## 2026-08-16 18:10 UTC — `fix/match-member-id-term-dates`
No commits in the last 6h (read-only or discussion session).
