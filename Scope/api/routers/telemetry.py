"""Live operational telemetry for `/status`.

ONE request, many aggregates, each returned WITH THE SQL THAT PRODUCED IT so the
page can render provenance instead of asking to be trusted. Read-only: this
module contains no INSERT/UPDATE/DELETE and touches no rule, gate, scoring or
detection code.

── WHY AN ENDPOINT AT ALL ────────────────────────────────────────────────────
Nothing existing can serve this. `/api/activity-log` caps at 100 rows
(`main.py:718`, `min(limit, 100)`) while a 24 h window holds ~790 rows — measured
12.6% coverage, so no 24 h aggregate can be derived from it. `/api/stats` serves
six homepage counters and none of the per-source, per-hour or funnel figures.
`/api/scheduler-status` gives last-run-per-source but no volumes. So the choice
was build-or-drop per metric, and every metric here was BUILDABLE — nothing was
dropped for want of a source.

── LABEL SEMANTICS, RE-DERIVED AGAINST THE LIVE COLUMNS ──────────────────────
Every label below was re-tested against prod rather than inherited from the
column's documentation. Four things the documentation would have got wrong.

⚠️ The figures quoted in this docstring are MEASUREMENTS TAKEN 2026-08-17 and
will drift — they are here to show the reasoning, not as current values. The
endpoint always returns live numbers, and the page renders only those. If you are
checking whether a claim still holds, re-run the query, don't trust the comment.

0. 🔴 `events_scanned` AND `events_flagged` ARE NOT "RECORDS EXAMINED" AND
   "PASSED THE QUALITY FILTER" EITHER — and this is the finding I nearly missed.
   The first version of this module applied hard scepticism to `alerts_emitted`
   and then inherited the contract's wording for the two columns BESIDE IT in the
   same table. A verifier caught it. Read off the call sites:
     • `scripts/db_backup.py:285`        scanned=1        -> one BACKUP RUN
     • `scripts/decay_alerts.py:104`     scanned=flagged  -> ALERTS DOWNGRADED
     • `scripts/monitor_backup_stall.py:157` flagged=len(problems) -> PROBLEMS
       FOUND, i.e. the metric is INVERTED: a higher "flagged" is worse
     • `ingest_house_index.py:779`       flagged=registered_count -> PTRs
     • `scripts/morning_brief.py:856`    scanned=sections_populated
     • `resolve_tickers.py:301`          scanned=count    -> TICKERS UPSERTED
   Measured share: non-rule sources are only ~2.6% of `scanned` but ~31% of
   `flagged`, so a mixed `flagged/scanned` ratio is materially wrong for the
   thing it appears to describe (measured 1.94% mixed vs 1.37% rule-only).
   ⇒ The FUNNEL IS NOW RULE-ONLY (`rule_funnel_24h`), the excluded non-rule
   contribution is returned alongside it so the exclusion is visible, and
   `_COUNTER_MEANING` documents what every non-rule source's counters really are.

   ⚠️ THAT FIX WAS ITSELF WRONG, AND SO WAS THE FIX AFTER IT. The history matters
   more than any one of them, because the defect changed shape each time and the
   next one will too:
     • v1 trusted the column names.               Caught: labels false for 31% of
       `flagged`.
     • v2 scoped the funnel BY SOURCE TYPE — rules in, non-rule jobs out — on the
       assumption that the labels are true of rules. Nobody checked. Caught: the
       labels are false for 9 of 14 RULES too (`flagged` is identical to `scanned`
       in five, a DB-write count in two, an aggregate in two), leaving a residual
       error of 46.7% — LARGER than the 31% v2 removed.
     • v3 scoped each COLUMN by its own semantics, which is correct, and each of
       the three totals is true. Caught: the page then DIVIDED them. Every total
       is summed over a different (correct) set of sources, so a ratio between two
       of them counts sources the denominator excludes. `alerts_written` was
       `COUNT(*) FROM alerts`, scoped by nothing, and `written / flagged` was
       printed as a conversion rate: 19.4% of that numerator was outside the
       denominator's population over 24 h and 67.9% over 7 days (RULE_ANOMALY
       alone being 58.7% of the week — a source declared to contribute to NEITHER
       upstream stage). Overstated 1.24x on the day, 3.11x over the week.
   ⇒ Ratios are now computed ONLY over `ratio_population` — the intersection where
   BOTH columns mean what their names say — and the alert rows outside it are
   returned and displayed as their own named number.

   ⭐ THE TRANSFERABLE LESSON, five occurrences in: correcting each number is not
   enough, because the false claim can live in the RELATIONSHIP between numbers
   that are each individually true. Scoping fixes the terms; it does not fix the
   arithmetic performed on them. Before any two figures on this page are divided,
   subtracted or compared, check that they are drawn from the same population —
   and if they are not, say so instead of computing.

1. ⚠️ `alerts_emitted` IS NOT AN ALERT COUNT. The activity-log contract calls it
   "alerts inserted". Measured: in 24 h `SUM(alerts_emitted)` = 89 against 62
   real alert rows; all-time 64,644 against 37,818, with 36,274 of it from
   NON-RULE sources — REFRESH_TICKERS alone contributes 31,197 (a refreshed
   ticker), LABEL_OUTCOMES 3,677 (a labelled outcome), DB_BACKUP 529 (a snapshot
   FILE). The column means "units of work this job reported producing".
   ⇒ `alerts_written` here is `COUNT(*) FROM alerts`, never the counter.
   The counter is still returned, under a name that says what it is, so the
   discrepancy can be shown rather than hidden.

2. ⚠️ `LIKE 'RULE_%'` DOES NOT MEAN "starts with RULE_". In SQL LIKE, `_` is a
   single-character WILDCARD, so that pattern is really `RULE?%` — it would also
   match `RULES_ANYTHING`. Prod additionally holds SPACE spellings beside the
   underscore ones. Two consequences: the pattern only works by accident, and
   **the same rule appears under two different source labels**, so a per-source
   table lists one rule twice.
   ⇒ classification is explicit in `_is_rule_source()`, never by LIKE.
   ⚠️ Precisely which pairs exist, since an earlier version of this comment
   overclaimed and a verifier checked every one:
     • detected by the underscore<->space rule — `RULE ADSB`/`RULE_ADSB`,
       `RULE OSINT`/`RULE_OSINT`, `RULE REDDIT`/`RULE_REDDIT`;
     • NOT detectable by any spelling rule, so carried in
       `_KNOWN_SOURCE_ALIASES` — `RULE 07 POLYMARKET`/`RULE_07` (both from
       `rule_07_polymarket.py`) and `ENRICH SCORES`/`SCORING` (both from
       `scripts/enrich_scores.py`);
     • `RULE OPTIONS CORRELATION` has NO underscore twin in prod at all —
       `rule_options_correlation.py:167` logs `RULE_OPTIONS`, which has never
       been written to prod `activity_log`. It is not a duplicate, it is a
       different label for a job that has not logged under its own name.

3. ⚠️ THE FUNNEL IS NOT STRICTLY MONOTONIC. scanned → flagged → written reads as
   a subset chain, but `RULE_COLLECTOR` has 33 rows all-time (1 in the last 24 h)
   with `events_flagged > events_scanned` (scanned 0, flagged 1). Small, but the
   *shape* claim is false, so `funnel_monotonic_violations` is returned and the
   page must not call this a strict funnel.

4. ⚠️ `corroborates` IS POPULATED ON A RULE THAT IS NOT SIGNED. `SIGNED_RULES` is
   `{"RULE_06"}`, yet RULE_01B has 1,474 of 2,168 rows with a non-NULL verdict
   (written but inert — the gate ignores it for an unsigned rule). A
   "signed-leg verdict" computed over every populated row would silently fold in
   1,474 RULE_01B rows as if adjudicated. ⇒ the verdict block is scoped to
   RULE_06 explicitly and reports the RULE_01B population separately so the
   distinction is visible.

── COST ─────────────────────────────────────────────────────────────────────
Measured on the prod host against 28.8k activity_log / 37.8k alerts rows.
THE FIGURE HAS MOVED TWICE AND THE CURRENT ONE IS THE ONLY ONE TO QUOTE:
  • 138.7 ms  — first build (independently re-measured at 131.2 ms)
  • 178.7 ms  — after the label fixes added an all-time `SELECT DISTINCT source`
                scan and two `alerts` subqueries for a basket-free ticker count
  • 162.7 ms  — after `mode=ro` and dropping a never-rendered COUNT over `members`
  • **180.0 ms** — current, median of 9 runs (min 164.7, max 201.7), payload ~51 KB,
                independently re-measured by a verifier at 164.9 ms. The last rise is
                the per-block degradation tracking added after review.

⚠️ THE PAGE'S REAL PER-POLL COST IS NOT JUST THIS ENDPOINT, and a pre-merge review
was right that the first version hid it. `/status` also fetches
`/api/scheduler-status`, measured at **46.8 ms** on prod — a correlated scalar
subquery per source group with two temp b-tree sorts, so it scales as
rows x distinct-sources and is the WORSE-SCALING of the two. It is therefore on its
own **5-minute** timer (the fastest cadence in the page's own `EXPECTED` map), not the
60 s loop. Honest combined figure: 162.7/60 s + 46.8/300 s ≈ **2.87 ms/s ≈ 0.29% duty
cycle** — and `/api/scheduler-status` is now fetched ONCE PER LOAD plus on an explicit
Refresh click, not on any timer, because it still uses `db_connection()` and therefore
takes a whole-DB write lock on a `journal_mode=delete` database. A 5-minute timer
turned `main`'s one lock per page load into ~12 per hour per open tab, which would have
reintroduced through a side door exactly the contention `_ro_connection` removed. So
the honest steady-state figure is **180.0/60 s ≈ 0.30%**, from this endpoint alone.
Almost every query plans as a full table SCAN because no index exists on
`activity_log(run_at)`, `alerts(created_at)` or `alerts(rule)` — the only index on
`alerts` is `idx_alerts_award_key`.
⚠️ "EVERY query is a full scan" is very slightly overstated and a verifier caught
it: `SQL_COVERAGE`'s members subquery uses `sqlite_autoindex_members_1`, and its
MIN/MAX subqueries plan as SEARCH.
The data cannot move faster than the poll anyway — the
finest granularity shown is an hourly bucket while the fastest rule cadence is
5 minutes.
✅ The connection is now genuinely read-only (`_ro_connection`, `mode=ro`). It used
to be `db_connection()`, which re-runs the whole schema script and commits on every
call — only ~0.7 ms of elapsed time, but on a `journal_mode=delete` database
(verified on prod: NOT WAL) that is a whole-database write lock, twice a minute per
open tab, against a DB that rules write to every 5 minutes. A pre-merge review was
right to reject it. "Read-only" is now true of the connection as well as the SQL.

⚠️ GROWTH — READ THIS BEFORE THE NEXT CHANGE HERE. Eight of the twenty queries are
UNBOUNDED all-time scans (`SQL_EMITTED_TRUTH`, `SQL_EMITTED_NON_RULE`,
`SQL_FUNNEL_VIOLATIONS`, `SQL_ALERTS_PER_RULE`, `SQL_CORROBORATES_POPULATED`,
`SQL_CORROBORATIONS`, `SQL_ALL_SOURCES`, `SQL_COVERAGE`). `activity_log` gains a
measured **790-844 rows/day** on prod, so it DOUBLES about every 36 days and those
scans double with it: expect roughly 350 ms within five weeks and 700 ms within ten,
with nothing in the UI that would tell you. The cheap fix is NOT an index (see
below) — it is to stop recomputing all-time facts every 60 s: split the payload so
the 24 h blocks poll and the all-time blocks are fetched on load plus a manual
refresh, or behind a `?scope=` parameter. **Review by 2026-10-01 or when the median
passes ~400 ms, whichever is first.**

🔴 DO NOT "FIX" THIS WITH AN INDEX. Adding one is a migration, and Scope has a
recorded instance of an ordinary `alerts(severity, created_at)` index silently
RESHUFFLING the morning brief's hero selection by changing SQL row order. If the
poll ever needs to go sub-10s, that is a human-gated decision with its own
determinism review — not a convenience change made from here.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import sqlite3

from fastapi import APIRouter

from jpt_common import _get_db_path

router = APIRouter()


def _ro_connection() -> sqlite3.Connection:
    """A genuinely READ-ONLY connection, per `CLAUDE.md`'s own prescription.

    🔴 THIS USED TO CALL `db_connection()`, AND A PRE-MERGE REVIEW WAS RIGHT THAT IT
    SHOULD NOT. `db_connection()` -> `_initialize_schema` unconditionally
    `executescript`s the whole of `schema_sqlite.sql` (21 CREATE statements) and
    COMMITS, then runs the migration guards. Measured, that is only ~0.7 ms of
    elapsed time — but elapsed time is the wrong measure. **Prod runs
    `journal_mode=delete`, verified, not WAL**, so a writer takes a whole-database
    RESERVED->EXCLUSIVE lock and readers and writers exclude each other.

    A nav-linked page polling every 60 s from every open tab therefore took a
    whole-DB write lock twice a minute per tab, against a database that RULE_ADSB
    writes every 5 min, `enrich_scores` every 10 min and `db_backup` reads at :05.
    It could not corrupt anything, but it could block a rule's `record_activity()`
    into `SQLITE_BUSY` — on the page whose entire job is to tell you whether the
    rules are running.

    `CLAUDE.md`: *"For read-only diagnostics, connect directly with
    `sqlite3.connect('file:data/jpt.db?mode=ro', uri=True)` to avoid triggering
    migrations/backups."* This endpoint is a read-only diagnostic by its own
    docstring, so it now obeys that. It also means this endpoint can never be the
    thing that applies a migration on a prod boot.
    """
    conn = sqlite3.connect(f"file:{_get_db_path(None)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ── WHAT EACH SOURCE'S COUNTERS ACTUALLY MEAN ────────────────────────────────
# 🔴 THIS MAP EXISTS BECAUSE A VERIFIER CAUGHT THE PAGE GLOSSING A REAL NUMBER
# WRONG. An earlier version printed a hardcoded three-item sentence ("a refreshed
# ticker, a labelled outcome and a snapshot file") bound POSITIONALLY to the
# top-three non-rule contributors, which are DATA-ORDERED. When PARSE_HOUSE_PDFS
# (703 parsed congressional transactions) displaced DB_BACKUP from third place,
# the page called it "a snapshot file" — a false label on a real number, which is
# the exact defect this whole surface exists to prevent, committed inside the
# warning about it.
#
# So the gloss is now DATA, keyed by source, harvested by reading each writer.
# Anything absent renders as "not documented" — a new source can never inherit
# another source's meaning by position.
#
# 🔴 AND THE SAME DEFECT SHIPPED A THIRD TIME HERE, caught by a verifier: this
# comment used to claim "every entry was read off the call site", and it was false
# because I assumed ONE call site per source. Several sources have more than one, and
# the values differ by PATH, not just by run:
#   DB_BACKUP          3 sites — :285 success (scanned=1, flagged=0, emitted=1),
#                      :222 backup raised and :256 integrity_check FAILED, BOTH of
#                      which write flagged=1. So `flagged` is a FAILURE FLAG with
#                      inverted polarity, and the old gloss called it "unused
#                      (always 0)" — a false label on the single highest-severity
#                      operational signal on this page.
#   PARSE_HOUSE_PDFS   3 sites — :708 real, :669/:729 all-zero early exits
#   INGEST_HOUSE_INDEX 2 sites — :779 real, :798 all-zero early exit
#   RULE_ADSB          2 sites, RULE_10 2 sites, RULE_12 3 sites (retired)
# Each entry below is now read off EVERY call site for that source, and where the
# meaning differs by path the gloss says so.
# 🔴 FOURTH TIME. The rule-only funnel did NOT fix the label — it moved the lie one
# layer in, and a verifier found it there. I scoped the funnel to detection rules on the
# stated ground that "the column labels are only true of them", and never checked whether
# they are true of the RULES either. Read off every rule's own call site:
#
#   scanned == flagged, IDENTICAL by construction, so "of those, passed a filter" is
#   meaningless:  RULE_01B (:474 len(rows)), RULE_09 (:507), RULE_10 (:610 found),
#                 RULE_15 (:423 ingested), RULE_ANOMALY (:130 emitted)
#   flagged is a DB-WRITE count, not a filter:  RULE_11 (:529 inserted+updated),
#                                               RULE_16 (:697 stored)
#   flagged is an AGGREGATE in a different unit: RULE_02 (:726 clusters over
#                                                transactions), RULE_CLUSTER (:399)
#   flagged IS genuinely a filter output:        RULE_06, RULE_07, RULE_08, RULE_OSINT,
#                                                RULE_REDDIT
#
# 🔴 RULE_ANOMALY writes scanned=flagged=emitted — STRUCTURALLY IDENTICAL to
# `decay_alerts.py:104`, the writer this module cites as proof that non-rule sources mean
# something else. It is a detection rule, so the rule-only scoping waved it straight
# through into "records examined by detection rules".
#
# ⇒ `events_flagged` HAS NO CONSISTENT MEANING ACROSS SOURCES. It is not a funnel stage
# and this module no longer presents it as one. Two booleans per source now record
# whether each column means what its name implies, the funnel sums ONLY sources where the
# semantic holds, and every exclusion is disclosed with its real meaning.
#
# Tuple: (scanned_means, flagged_means, emitted_means, scanned_is_record_count,
#         flagged_is_filter_output)
_COUNTER_MEANING = {
    # ── DETECTION RULES ──────────────────────────────────────────────────────
    "RULE_OSINT":            ("GDELT events examined", "events passing the mention/hostility filter",
                              "alerts written", True,  True),
    "RULE_REDDIT":           ("reddit posts examined", "posts passing the filter",
                              "alerts written", True,  True),
    "RULE_07":               ("Polymarket markets examined", "markets that triggered",
                              "alerts written", True,  True),
    "RULE_06":               ("Form 4 filings examined", "filings judged significant",
                              "alerts written", True,  True),
    "RULE_08":               ("Federal Register documents examined", "documents matching a sector keyword",
                              "alerts written", True,  True),
    "RULE_01B":              ("congressional transaction rows examined",
                              "IDENTICAL to scanned (rule_01b_first_touch.py:474 passes len(rows) to "
                              "both) — NOT a filter output", "alerts written", True,  False),
    "RULE_02":              ("transactions examined",
                              "CLUSTERS found (rule_02_cluster.py:726) — an aggregate in a different "
                              "unit from scanned, not a subset of it", "alerts written", True,  False),
    "RULE_CLUSTER":          ("tickers examined",
                              "QUALIFYING GROUPS (rule_cluster.py:399) — an aggregate, not records",
                              "alerts written", True,  False),
    "RULE_11":               ("award records examined",
                              "contract rows INSERTED+UPDATED in the contracts table "
                              "(rule_11_contracts.py:529) — a DB-write count, not a filter",
                              "alerts written", True,  False),
    "RULE_16":               ("13F records examined",
                              "holdings STORED (rule_16_institutional.py:697) — a DB-write count",
                              "alerts written", True,  False),
    "RULE_ANOMALY":          ("IDENTICAL to alerts emitted (rule_anomaly.py:130 passes `emitted` to "
                              "all three columns) — NOT a count of records examined",
                              "IDENTICAL to alerts emitted — NOT a filter output",
                              "alerts written", False, False),
    "RULE_09":               ("IDENTICAL to flagged (rule_09_lobbying.py:507 passes total_triggered "
                              "to both)", "IDENTICAL to scanned — NOT a filter output",
                              "alerts written", False, False),
    "RULE_10":               ("IDENTICAL to flagged (rule_10_corroboration.py:610 passes `found` to "
                              "both)", "IDENTICAL to scanned — NOT a filter output",
                              "corroborations written", False, False),
    "RULE_15":               ("IDENTICAL to flagged (rule_15_earnings_nlp.py:423 passes `ingested` to "
                              "both)", "IDENTICAL to scanned — NOT a filter output",
                              "alerts written", False, False),
    # 🔴 READ OFF THE WRONG CALL SITE. `rule_adsb.py` has TWO record_activity()
    # calls and the gloss was taken from the early-exit one (`:121`, flagged=0,
    # emitted=0). The site that runs on a normal pass is `:170`:
    #   scanned=len(flights), flagged=len(concentrations), emitted=len(concentrations)
    # so `flagged` is not "unused" — it is a count of concentration ZONES, an
    # aggregate in a different unit from `scanned` (flights), which is the same
    # shape as RULE_02/RULE_CLUSTER and must not be summed as a filter output.
    # And `emitted` is not an alert count either: the loop at `:126` writes up to
    # TWO alert rows per zone (`for ticker in tickers[:2]`) and skips zones that
    # dedup out, so it can sit either side of the true alert count.
    # Magnitude today: prod holds 10,522 RULE_ADSB rows, ALL with flagged=0, and
    # RULE_ADSB has written 0 alerts ever — a latent mislabel, not a live one.
    "RULE_ADSB":             ("flights examined",
                              "concentration ZONES over the threshold — an aggregate over "
                              "flights, not a subset of them",
                              "concentration zones, NOT alert rows (up to 2 alerts per zone)",
                              True,  False),
    "RULE_TELEGRAM_OSINT":   ("unused (not passed)", "unused (not passed)", "alerts written",
                              False, False),
    # a collector, not a detector — see _NON_DETECTION_RULE_SOURCES
    "RULE_COLLECTOR":        ("0 by design (a lookup-table write is not a finding)",
                              "ticker names collected", "0 by design", False, False),
    "RULE COLLECTOR":        ("0 by design (a lookup-table write is not a finding)",
                              "ticker names collected", "0 by design", False, False),

    # ── INFRASTRUCTURE (never in the funnel; kept so the excluded table can gloss them) ──
    "REFRESH_TICKERS":       ("tickers upserted", "unused (always 0)", "tickers upserted", False, False),
    "LABEL_OUTCOMES":        ("alerts eligible for labelling",
                              "alerts with NO USABLE SYMBOL OR NO PRICE — 'unavailable', which is "
                              "mostly basket/multi-ticker and no-ticker, NOT merely 'unpriceable'",
                              "outcomes labelled", False, False),
    "SCORING":               ("alerts scored", "unused (unset)", "unused (always 0)", False, False),
    "MONITOR_ENRICH_STALL":  ("alerts unscored >30 min", "alerts unscored >30 min",
                              "alerts unscored >30 min", False, False),
    "MONITOR_BACKUP_STALL":  ("1 per check", "PROBLEMS FOUND (higher is worse)",
                              "PROBLEMS FOUND (higher is worse)", False, False),
    "DB_BACKUP":             ("1 per SUCCESSFUL backup run (0 on a failed run)",
                              "BACKUP FAILURES (higher is worse; 1 = backup raised or "
                              "integrity_check failed and the snapshot was discarded)",
                              "snapshot FILE written (0 on a failed run)", False, False),
    "DECAY":                 ("alerts downgraded", "alerts downgraded", "unused (always 0)", False, False),
    "INGEST_HOUSE_INDEX":    ("index entries seen (0 on an early-exit run)", "PTRs registered",
                              "new filings", False, False),
    "PARSE_HOUSE_PDFS":      ("filings processed (0 on an early-exit run)", "PDFs downloaded",
                              "TRANSACTIONS parsed", False, False),
    "DAILY_BRIEF":           ("brief sections populated", "active theses", "1 brief generated",
                              False, False),
    "BRIEF":                 ("alerts in the brief", "evidence alerts cited", "1 brief generated",
                              False, False),
    "BACKTEST":              ("alerts ok + skipped", "alerts ok", "unused (always 0)", False, False),
    "SCHEDULER_JOB_FAILURE": ("unused", "unused", "unused", False, False),
}


def _meaning(source):
    """The 5-tuple for a source, or None. Absent => rendered "not documented"."""
    return _COUNTER_MEANING.get(str(source or "").upper().strip())


def _scanned_is_records(source):
    m = _meaning(source)
    return bool(m and m[3])


def _flagged_is_filter(source):
    m = _meaning(source)
    return bool(m and m[4])


# ── source classification ────────────────────────────────────────────────────
# Explicit, because `LIKE 'RULE_%'` is a wildcard match (see the module note) and
# because "is this a rule run?" is a claim the page makes out loud.
#
# ⚠️ A verifier pointed out that the previous version of this set was INERT: the
# prefix test short-circuited before it was consulted, so a missing entry could
# not cause a misclassification but the set could not fix one either. It is now
# load-bearing and checked FIRST.
_NON_RULE_SOURCES = {
    "SCORING", "DB_BACKUP", "MONITOR_BACKUP_STALL", "MONITOR_ENRICH_STALL",
    "PARSE_HOUSE_PDFS", "INGEST_HOUSE_INDEX", "SCHEDULER_JOB_FAILURE",
    "DECAY", "BRIEF", "DAILY_BRIEF", "BACKTEST", "REFRESH_TICKERS",
    "LABEL_OUTCOMES", "POSITION_LEDGER_AUTH_DENIED", "ROSTER_CHECK",
    "INGEST_LOBBYING", "TELEGRAM_BOT", "INGEST_SENATE", "ENRICH SCORES",
}

# 🔴 Sources that START with RULE but are NOT detection rules, so counting them as
# "rule runs" overstates detection activity. Both were found by a verifier:
#   RULE_COLLECTOR  — a coverage collector. It has written ZERO rows to `alerts`
#                     in prod history and logs emitted=0; it collects NAMES.
#   RULE_OPTIONS*   — an ENRICHER. It decorates existing alerts, so its emitted
#                     count is enrichments, not detections.
# RULE_DISCOVERY is included for the same reason the gate excludes it: a collected
# name is "this exists", not "watch this".
_NON_DETECTION_RULE_SOURCES = {
    "RULE_COLLECTOR", "RULE COLLECTOR",
    "RULE_DISCOVERY", "RULE DISCOVERY",
    "RULE_OPTIONS", "RULE OPTIONS", "RULE OPTIONS CORRELATION",
}

# Same job, two labels, where no spelling rule can pair them. Harvested by
# reading the writers, not guessed from the strings.
_KNOWN_SOURCE_ALIASES = {
    "RULE 07 POLYMARKET": "RULE_07",        # rule_07_polymarket.py
    "ENRICH SCORES":      "SCORING",        # scripts/enrich_scores.py
}


def _norm_source(source: str) -> str:
    """Collapse a source label to its identity, for DETECTING collisions.

    Two mechanisms, because one is not enough:
      1. underscore <-> space, which pairs `RULE_ADSB` with `RULE ADSB`;
      2. `_KNOWN_SOURCE_ALIASES`, because `RULE 07 POLYMARKET` and `RULE_07` are
         the same rule and NO spelling rule pairs them — a verifier found that
         gap, and the page was under-disclosing as a result.

    Normalising is for detection only. The page shows the REAL labels and
    discloses the pairs; merging them would assert an identity nobody verified.
    """
    s = str(source or "").upper().strip()
    s = _KNOWN_SOURCE_ALIASES.get(s, s)
    return s.replace("_", " ").strip()


def _is_rule_source(source: str) -> bool:
    """True only for a DETECTION rule — one that can write to `alerts`."""
    s = str(source or "").upper().strip()
    if s in _NON_RULE_SOURCES or s in _NON_DETECTION_RULE_SOURCES:
        return False
    # Literal 'RULE' prefix on either spelling, checked in Python so no LIKE
    # wildcard can widen it (`LIKE 'RULE_%'` would also match RULES_ANYTHING).
    return s.startswith("RULE_") or s.startswith("RULE ")


# ── SQL, kept as named constants so the exact text can travel to the client ───
SQL_HOURLY = """
    SELECT strftime('%Y-%m-%d %H:00', run_at) AS hour,
           SUM(events_scanned) AS scanned,
           SUM(events_flagged) AS flagged,
           SUM(alerts_emitted) AS emitted_counter,
           COUNT(*)            AS runs
    FROM activity_log
    WHERE datetime(run_at) >= datetime('now','-24 hours')
    GROUP BY hour ORDER BY hour ASC
"""

SQL_PER_SOURCE = """
    SELECT source,
           COUNT(*)                       AS runs,
           SUM(events_scanned)            AS scanned,
           SUM(events_flagged)            AS flagged,
           SUM(alerts_emitted)            AS emitted_counter,
           ROUND(AVG(duration_seconds),2) AS avg_duration_s,
           SUM(duration_seconds IS NULL)  AS duration_missing,
           MAX(run_at)                    AS last_run
    FROM activity_log
    WHERE datetime(run_at) >= datetime('now','-24 hours')
    GROUP BY source ORDER BY scanned DESC, runs DESC
"""

SQL_HEAT = """
    SELECT source, strftime('%Y-%m-%d %H', run_at) AS hour,
           SUM(events_scanned) AS scanned, COUNT(*) AS runs
    FROM activity_log
    WHERE datetime(run_at) >= datetime('now','-24 hours')
    GROUP BY source, hour
"""

SQL_PEAK = """
    SELECT MAX(s) AS peak_hourly_scanned,
           ROUND(AVG(s),1) AS mean_hourly_scanned,
           COUNT(*) AS hours_observed
    FROM (SELECT SUM(events_scanned) s FROM activity_log
          WHERE datetime(run_at) >= datetime('now','-30 days')
          GROUP BY strftime('%Y-%m-%d %H', run_at))
"""

SQL_ALERTS_WRITTEN = """
    SELECT COUNT(*) AS alerts_written_24h FROM alerts
    WHERE datetime(created_at) >= datetime('now','-24 hours')
"""

# ⚠️ THE RATIO POPULATION — a fifth verifier found the page dividing across two
# different populations and calling the result a conversion rate.
#
# `rule_scanned` is summed over the sources whose `scanned` really is a record
# count; `rule_flagged` over the (smaller) set whose `flagged` really is a filter
# output. `alerts_written` was `COUNT(*) FROM alerts` — scoped by NOTHING. The
# page then printed `written / flagged` as a funnel conversion.
#
# On prod that numerator is 19.4% rules that contribute zero to the denominator
# over 24 h, and 67.9% over 7 days (RULE_ANOMALY alone is 58.7% of the week) —
# and RULE_ANOMALY is a source this module explicitly declares contributes to
# NEITHER upstream stage. The printed rate was overstated 1.24x on the day and
# 3.11x over the week.
#
# A ratio is only meaningful between two numbers drawn from the SAME sources, so
# the ratios are now computed over the intersection — the sources where BOTH
# columns mean what their names say — and the alert rows written by rules outside
# it are reported separately rather than folded into a numerator.
SQL_ALERTS_BY_RULE = """
    SELECT rule, COUNT(*) AS n FROM alerts
    WHERE datetime(created_at) >= datetime('now','-24 hours')
    GROUP BY rule
"""

SQL_EMITTED_TRUTH = """
    SELECT (SELECT SUM(alerts_emitted) FROM activity_log) AS emitted_counter_all_time,
           (SELECT COUNT(*) FROM alerts)                  AS alert_rows_all_time
"""

SQL_EMITTED_NON_RULE = """
    SELECT source, SUM(alerts_emitted) AS emitted FROM activity_log
    WHERE alerts_emitted > 0 GROUP BY source ORDER BY emitted DESC
"""

SQL_FUNNEL_VIOLATIONS = """
    SELECT source, COUNT(*) AS n, MAX(events_flagged - events_scanned) AS worst
    FROM activity_log WHERE events_flagged > events_scanned
    GROUP BY source ORDER BY n DESC
"""

SQL_FAILURES = """
    SELECT
      (SELECT COUNT(*) FROM activity_log WHERE source='SCHEDULER_JOB_FAILURE'
         AND datetime(run_at) >= datetime('now','-24 hours')) AS failures_24h,
      (SELECT COUNT(*) FROM activity_log WHERE source='SCHEDULER_JOB_FAILURE'
         AND datetime(run_at) >= datetime('now','-7 days'))   AS failures_7d
"""

SQL_FAILURE_DETAIL = """
    SELECT run_at, notes FROM activity_log
    WHERE source='SCHEDULER_JOB_FAILURE'
      AND datetime(run_at) >= datetime('now','-7 days')
    ORDER BY datetime(run_at) DESC LIMIT 15
"""

SQL_ALERTS_PER_RULE = """
    SELECT rule,
           COUNT(*) AS all_time,
           SUM(CASE WHEN datetime(created_at) >= datetime('now','-24 hours') THEN 1 ELSE 0 END) AS d1,
           SUM(CASE WHEN datetime(created_at) >= datetime('now','-7 days')   THEN 1 ELSE 0 END) AS d7
    FROM alerts GROUP BY rule ORDER BY all_time DESC
"""

SQL_SEVERITY = """
    SELECT severity, COUNT(*) AS n FROM alerts
    WHERE datetime(created_at) >= datetime('now','-24 hours')
    GROUP BY severity
"""

SQL_SIGNED = """
    SELECT SUM(CASE WHEN corroborates=1 THEN 1 ELSE 0 END)      AS corroborates,
           SUM(CASE WHEN corroborates=0 THEN 1 ELSE 0 END)      AS does_not,
           SUM(CASE WHEN corroborates IS NULL THEN 1 ELSE 0 END) AS unadjudicated,
           COUNT(*) AS total
    FROM alerts WHERE rule='RULE_06'
"""

SQL_CORROBORATES_POPULATED = """
    SELECT rule, SUM(corroborates IS NOT NULL) AS populated, COUNT(*) AS total
    FROM alerts GROUP BY rule HAVING populated > 0 ORDER BY populated DESC
"""

SQL_CORROBORATIONS = """
    SELECT (SELECT COUNT(*) FROM alerts WHERE rule='RULE_10') AS all_time,
           (SELECT COUNT(*) FROM alerts WHERE rule='RULE_10'
              AND datetime(created_at) >= datetime('now','-30 days')) AS d30,
           (SELECT COUNT(*) FROM alerts WHERE rule='RULE_10'
              AND datetime(created_at) >= datetime('now','-7 days'))  AS d7,
           (SELECT COUNT(*) FROM themes) AS themes
"""

SQL_CORROBORATION_DETAIL = """
    SELECT id, ticker, created_at, tags, evidence_confidence
    FROM alerts WHERE rule='RULE_10' ORDER BY datetime(created_at) DESC LIMIT 10
"""

# ⚠️ COALESCE and the STRICT `<` are not decoration — they make this the same
# predicate `scripts/monitor_enrich_stall.py:36-38` uses. The first version
# omitted both and still called itself "the criterion verbatim"; a verifier
# checked and it was not. On current data both forms return 0 (there are no NULL
# scores), so the NUMBER was right and the CLAIM of equivalence was not.
SQL_BACKLOG = """
    SELECT
      (SELECT COUNT(*) FROM alerts
         WHERE COALESCE(opportunity_score,0)=0 AND COALESCE(evidence_confidence,0)=0) AS unscored,
      (SELECT COUNT(*) FROM alerts
         WHERE COALESCE(opportunity_score,0)=0 AND COALESCE(evidence_confidence,0)=0
           AND datetime(created_at) < datetime('now','-30 minutes')) AS unscored_over_30min
"""

# 🔴 FOUR statuses exist, not three, and the fourth is semantically loud.
# `scripts/label_outcomes.py` writes:
#   complete    — a 20-day close exists (`:211`)
#   pending     — it does not yet (`:211`)
#   unavailable — no usable single-equity symbol OR no price series (`:192`, `:198`)
#   excluded    — the alert was QUARANTINED (`:190`, and an UPDATE at `:229`) because its
#                 rule's attribution was known-bad. Prod holds 0 of these today, so the
#                 gap was latent — but a quarantine would have shrunk the other bars with
#                 nothing on the page to say why. Same shape as the DB_BACKUP gloss defect.
# The page now renders whatever statuses come back, so a fifth cannot go missing either.
SQL_OUTCOMES = """
    SELECT status, COUNT(*) AS n FROM alert_outcomes GROUP BY status ORDER BY n DESC
"""

# ⚠️ AND "unavailable" IS NOT "unpriceable" — that gloss was right for a minority.
# Measured on prod: of 1,705 `unavailable` rows, only **546** are "no price data
# (delisted / unpriceable)". The other 1,159 are **1,026** "non-single-equity
# (basket/multi-ticker)" and **133** "no ticker" — the alert never had a usable symbol,
# which is a different fact from the market not having a price. The reason breakdown is
# returned so the page can state it instead of averaging three causes into one word.
SQL_OUTCOME_REASONS = """
    SELECT status, COALESCE(note, '(no note)') AS reason, COUNT(*) AS n
    FROM alert_outcomes
    WHERE status NOT IN ('complete','pending')
    GROUP BY status, reason ORDER BY n DESC
"""

SQL_ALL_SOURCES = """
    SELECT DISTINCT source FROM activity_log ORDER BY source
"""

# ⚠️ `distinct_tickers` USED TO COUNT THINGS THAT ARE NOT TICKERS. A verifier found
# 42 distinct values containing a SPACE — `$USO $XLE $LMT $RTX $NOC`,
# `$COIN $MSTR $IBIT` — which `CLAUDE.md` documents as multi-symbol BASKETS
# (`_is_equity_ticker` excludes exactly "contains a space"), plus `$SPY`/`SPY` and
# `$USO`/`USO` counted twice each because of the `$` prefix. ~2.7% of the figure.
# Both the raw and the cleaned count are returned so the page can show the real
# number and say what it excluded.
SQL_COVERAGE = """
    SELECT (SELECT COUNT(*) FROM alerts) AS alerts,
           (SELECT COUNT(*) FROM activity_log) AS activity_rows,
           (SELECT COUNT(DISTINCT ticker) FROM alerts
              WHERE ticker IS NOT NULL AND ticker != '') AS distinct_ticker_values_raw,
           (SELECT COUNT(DISTINCT REPLACE(UPPER(TRIM(ticker)),'$',''))
              FROM alerts
              WHERE ticker IS NOT NULL AND TRIM(ticker) != ''
                AND INSTR(TRIM(ticker),' ') = 0) AS distinct_tickers,
           (SELECT COUNT(DISTINCT ticker) FROM alerts
              WHERE ticker IS NOT NULL AND INSTR(TRIM(ticker),' ') > 0) AS multi_symbol_baskets,
           (SELECT MIN(run_at) FROM activity_log) AS activity_since,
           (SELECT MAX(created_at) FROM alerts) AS newest_alert
"""


def _fill_hour_gaps(rows):
    """Insert explicit zero rows for hours in which NOTHING ran.

    🔴 `GROUP BY` only emits populated hours, and the chart spaces bars by array
    INDEX. So an hour with no runs was silently DELETED from an axis the legend
    calls "one bar = one clock hour": a four-hour outage rendered identically to a
    one-hour gap, and since only three x-labels are printed it was invisible. A
    verifier reproduced it. An outage is exactly when someone opens /status, so
    this is the worst possible thing for that axis to hide.

    A filled hour is marked `no_runs: True` so the page can draw it as a real gap
    rather than as a genuine zero-volume hour — "nothing ran" and "ran and found
    nothing" are different facts.
    """
    if not rows:
        return rows
    from datetime import datetime as _dt, timedelta as _td
    fmt = "%Y-%m-%d %H:00"
    try:
        first = _dt.strptime(rows[0]["hour"], fmt)
        last  = _dt.strptime(rows[-1]["hour"], fmt)
    except (ValueError, KeyError, TypeError):
        return rows                      # unparseable: return untouched, never guess
    have = {r["hour"]: r for r in rows}
    out, cur = [], first
    while cur <= last:
        key = cur.strftime(fmt)
        if key in have:
            r = dict(have[key]); r["no_runs"] = False
        else:
            r = {"hour": key, "scanned": 0, "flagged": 0, "emitted_counter": 0,
                 "runs": 0, "no_runs": True}
        out.append(r)
        cur += _td(hours=1)
    return out


def _flat(sql: str) -> str:
    """Collapse SQL to one line for the provenance payload."""
    return " ".join(sql.split())


# ── tolerant reads ──────────────────────────────────────────────────────────
# A read-only connection cannot create schema, which is correct for a diagnostic —
# but it means a missing table raises instead of being silently initialised, and a
# whole-page 500 for one absent table is a bad trade. So a failed block degrades to
# EMPTY and its reason is RECORDED in `degraded`, never swallowed: the page then says
# "unavailable: no such table X" instead of either dying or inventing a zero.
# Reachable on a fresh deploy, a partially-restored DB, or a disposable test DB.
# Keyed by the SQL constant's name, so the page can mark the exact block that failed
# rather than guessing from an error string. A verifier found the first version's
# flat list was not enough: `drawHealth` still rendered 0/0/0 for a block listed as
# unavailable, and the live bar promised "shown empty, not as zero" one line above it.
_DEGRADED: dict = {}


def _rows(conn, sql, label, args=()):
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.Error as exc:
        _DEGRADED[label] = str(exc)
        return []


def _one(conn, sql, label, args=()):
    try:
        r = conn.execute(sql, args).fetchone()
        return dict(r) if r is not None else {}
    except sqlite3.Error as exc:
        _DEGRADED[label] = str(exc)
        return {}


@router.get("/telemetry", tags=["Meta"])
def telemetry():
    """Every aggregate the /status telemetry panel renders, with its own SQL.

    `sql` travels with each block on purpose: the page shows it in a provenance
    disclosure, so a reader can check the number rather than trust the label.
    That is the whole design, and it exists because this panel's predecessor
    shipped two REAL numbers under FALSE labels.
    """
    t0 = time.perf_counter()
    _DEGRADED.clear()
    try:
        conn = _ro_connection()
    except sqlite3.Error as exc:
        # ⚠️ `_ro_connection()` sits OUTSIDE the per-block guards, so an absent DB file
        # was a bare 500 rather than a reported degradation — the stated contract
        # ("degrades to EMPTY and reports the reason") did not cover it, and a verifier
        # hit it. `mode=ro` cannot create the file, which is correct for a diagnostic;
        # what was wrong was not SAYING so. Now every block is empty and the reason
        # travels, so the page renders "unavailable" rather than a blank or a guess.
        return {
            "as_of_utc": datetime.now(timezone.utc).isoformat(),
            "query_ms": round((time.perf_counter() - t0) * 1000, 1),
            "poll_hint_seconds": 60,
            "degraded": {"CONNECTION": str(exc)},
            "degraded_reasons": [str(exc)],
            "metrics": {},
        }
    try:
        hourly       = _fill_hour_gaps(_rows(conn, SQL_HOURLY, "SQL_HOURLY"))
        per_source   = _rows(conn, SQL_PER_SOURCE, "SQL_PER_SOURCE")
        heat         = _rows(conn, SQL_HEAT, "SQL_HEAT")
        peak         = _one(conn, SQL_PEAK, "SQL_PEAK")
        written      = _one(conn, SQL_ALERTS_WRITTEN, "SQL_ALERTS_WRITTEN")
        alerts_by_rule = _rows(conn, SQL_ALERTS_BY_RULE, "SQL_ALERTS_BY_RULE")
        emit_truth   = _one(conn, SQL_EMITTED_TRUTH, "SQL_EMITTED_TRUTH")
        emit_by_src  = _rows(conn, SQL_EMITTED_NON_RULE, "SQL_EMITTED_NON_RULE")
        violations   = _rows(conn, SQL_FUNNEL_VIOLATIONS, "SQL_FUNNEL_VIOLATIONS")
        failures     = _one(conn, SQL_FAILURES, "SQL_FAILURES")
        fail_detail  = _rows(conn, SQL_FAILURE_DETAIL, "SQL_FAILURE_DETAIL")
        per_rule     = _rows(conn, SQL_ALERTS_PER_RULE, "SQL_ALERTS_PER_RULE")
        severity     = _rows(conn, SQL_SEVERITY, "SQL_SEVERITY")
        signed       = _one(conn, SQL_SIGNED, "SQL_SIGNED")
        corr_pop     = _rows(conn, SQL_CORROBORATES_POPULATED, "SQL_CORROBORATES_POPULATED")
        corr         = _one(conn, SQL_CORROBORATIONS, "SQL_CORROBORATIONS")
        corr_detail  = _rows(conn, SQL_CORROBORATION_DETAIL, "SQL_CORROBORATION_DETAIL")
        backlog      = _one(conn, SQL_BACKLOG, "SQL_BACKLOG")
        outcomes     = _rows(conn, SQL_OUTCOMES, "SQL_OUTCOMES")
        out_reasons  = _rows(conn, SQL_OUTCOME_REASONS, "SQL_OUTCOME_REASONS")
        all_sources  = _rows(conn, SQL_ALL_SOURCES, "SQL_ALL_SOURCES")
        coverage     = _one(conn, SQL_COVERAGE, "SQL_COVERAGE")
    finally:
        conn.close()

    # ── derived, in Python, from the rows above — never a second query ────────
    # Rule vs non-rule run split, classified explicitly (see module note 2).
    rule_runs = sum(r["runs"] for r in per_source if _is_rule_source(r["source"]))
    all_runs  = sum(r["runs"] for r in per_source)

    # 🔴 THE FUNNEL IS SCOPED BY SEMANTICS, NOT BY SOURCE TYPE. Scoping to "detection
    # rules" was the previous fix and it was not enough: `events_flagged` means a filter
    # output in only 5 sources, a DB-write count in 2, an aggregate in 2, and is byte-
    # identical to `scanned` in 5 more. So each column is now summed over exactly the
    # sources where THAT column means what its name says, and everything else is
    # disclosed with what it actually counts.
    def _sum(rows, key, pred):
        return sum((r[key] or 0) for r in rows if pred(r["source"]))

    rule_scanned = _sum(per_source, "scanned", _scanned_is_records)
    rule_flagged = _sum(per_source, "flagged", _flagged_is_filter)
    # what the OLD, wrong scoping would have produced — kept so the page can show the
    # size of the correction rather than quietly restating the number
    old_rule_scanned = _sum(per_source, "scanned", _is_rule_source)
    old_rule_flagged = _sum(per_source, "flagged", _is_rule_source)
    non_scanned  = _sum(per_source, "scanned", lambda x: not _scanned_is_records(x))
    non_flagged  = _sum(per_source, "flagged", lambda x: not _flagged_is_filter(x))

    # ── THE RATIO POPULATION ─────────────────────────────────────────────────
    # The three headline totals above are each summed over the sources where THAT
    # column is honest, which makes each of them true on its own and makes any
    # ratio BETWEEN them a category error: the numerator counts sources the
    # denominator excludes. So ratios are computed only over the intersection,
    # and the page is given the population by name so it can say whose rate it is.
    def _in_ratio_pop(source):
        return _scanned_is_records(source) and _flagged_is_filter(source)

    ratio_sources = sorted({r["source"] for r in per_source if _in_ratio_pop(r["source"])})
    ratio_scanned = _sum(per_source, "scanned", _in_ratio_pop)
    ratio_flagged = _sum(per_source, "flagged", _in_ratio_pop)
    # Alert ROWS written by exactly those rules — the only numerator that shares a
    # population with `ratio_flagged`. `alerts.rule` is the source label, so this
    # is a direct match, not a mapping.
    _pop = set(ratio_sources)
    ratio_written = sum((r["n"] or 0) for r in alerts_by_rule if r["rule"] in _pop)
    written_total = written.get("alerts_written_24h")
    written_outside = (
        None if written_total is None else written_total - ratio_written)
    # What is in the unscoped alert count but not in the ratio's population —
    # named and counted, because "the rest" is how the last four defects hid.
    written_outside_detail = sorted(
        ({"rule": r["rule"], "n": r["n"],
          "scanned_counted": _scanned_is_records(r["rule"]),
          "flagged_counted": _flagged_is_filter(r["rule"])}
         for r in alerts_by_rule if r["rule"] not in _pop),
        key=lambda r: -(r["n"] or 0))

    # Every source excluded from EITHER sum, with what its counters really are.
    excluded_detail = []
    for r in per_source:
        sc_ok, fl_ok = _scanned_is_records(r["source"]), _flagged_is_filter(r["source"])
        if sc_ok and fl_ok:
            continue
        if not ((r["scanned"] or 0) or (r["flagged"] or 0) or (r["emitted_counter"] or 0)):
            continue
        meaning = _meaning(r["source"])
        excluded_detail.append({
            "source": r["source"],
            "scanned": r["scanned"], "flagged": r["flagged"],
            "emitted_counter": r["emitted_counter"],
            "scanned_means": meaning[0] if meaning else None,
            "flagged_means": meaning[1] if meaning else None,
            "emitted_means": meaning[2] if meaning else None,
            "scanned_counted": sc_ok,
            "flagged_counted": fl_ok,
            "is_detection_rule": _is_rule_source(r["source"]),
        })
    excluded_detail.sort(key=lambda r: -((r["scanned"] or 0) + (r["flagged"] or 0)))

    # The emitted counter's non-rule contributors, which is WHY it is not an
    # alert count. Split by the same explicit classifier.
    non_rule_emitted = [r for r in emit_by_src if not _is_rule_source(r["source"])]
    rule_emitted     = sum(r["emitted"] or 0 for r in emit_by_src if _is_rule_source(r["source"]))

    # Duplicate source labels for the same rule — a real data-quality issue that
    # makes a per-source table list one rule twice. Disclosed, never merged.
    #
    # ⚠️ Computed over ALL-TIME distinct sources, not over the 24 h window. The
    # space-spelled labels are LEGACY: they exist in the table but have not been
    # written recently, so a window-scoped check returns empty and would report
    # "no collision" about a table that contains several. Scoping this to the
    # window would have made the disclosure quietly false.
    seen: dict[str, list[str]] = {}
    for r in (row["source"] for row in all_sources):
        seen.setdefault(_norm_source(r), []).append(r)
    variants = {k: v for k, v in seen.items() if len(v) > 1}

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    # Which BLOCK depends on which SQL, so a failed read marks the exact card the
    # page renders. Explicit rather than inferred from the `sql` strings: a block
    # that quietly stopped being marked would put us straight back to rendering
    # zeros for a metric that never loaded.
    _BLOCK_SQL = {
        "ingest_hourly_24h":            ["SQL_HOURLY"],
        "per_source_24h":               ["SQL_PER_SOURCE"],
        "heat_grid_24h":                ["SQL_HEAT"],
        "scale_reference_30d":          ["SQL_PEAK"],
        "run_split_24h":                ["SQL_PER_SOURCE"],
        "source_label_variants":        ["SQL_ALL_SOURCES"],
        "alerts_written_24h":           ["SQL_ALERTS_WRITTEN"],
        "rule_funnel_24h":              ["SQL_PER_SOURCE", "SQL_ALERTS_WRITTEN",
                                         "SQL_ALERTS_BY_RULE"],
        "emitted_counter_truth":        ["SQL_EMITTED_TRUTH", "SQL_EMITTED_NON_RULE"],
        "funnel_monotonic_violations":  ["SQL_FUNNEL_VIOLATIONS"],
        "scheduler_failures":           ["SQL_FAILURES"],
        "scheduler_failure_detail_7d":  ["SQL_FAILURE_DETAIL"],
        "alerts_per_rule":              ["SQL_ALERTS_PER_RULE"],
        "severity_24h":                 ["SQL_SEVERITY"],
        "signed_leg_verdict_rule06":    ["SQL_SIGNED", "SQL_CORROBORATES_POPULATED"],
        "corroborations":               ["SQL_CORROBORATIONS"],
        "corroboration_detail":         ["SQL_CORROBORATION_DETAIL"],
        "scoring_backlog":              ["SQL_BACKLOG"],
        "outcome_labeling":             ["SQL_OUTCOMES", "SQL_OUTCOME_REASONS"],
        "coverage":                     ["SQL_COVERAGE"],
    }

    payload = {
        # `as_of` is the moment THIS response was computed. The page prints it on
        # every refresh; it is not a capture timestamp baked into a file.
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "query_ms": elapsed_ms,
        "poll_hint_seconds": 60,
        # Non-empty only when a block could not be read. Surfaced on the page as
        # "unavailable", because a metric that failed to load is not a metric that
        # measured zero. Keyed by SQL constant so the page can mark the exact card;
        # `degraded_reasons` is the flat list for the status bar.
        "degraded": dict(_DEGRADED),
        "degraded_reasons": sorted(set(_DEGRADED.values())),
        "_block_sql": _BLOCK_SQL,
        "metrics": {
            "ingest_hourly_24h": {
                "sql": _flat(SQL_HOURLY),
                "fields": ("activity_log.events_scanned / events_flagged / "
                           "alerts_emitted / run_at"),
                "note": ("The first and last buckets are PARTIAL clock hours, clipped by "
                         "the rolling 24 h window edge — not comparable to a full hour. "
                         "Hours in which NOTHING ran are returned explicitly with "
                         "no_runs=true rather than omitted, so the axis stays a real time "
                         "axis: GROUP BY emits only populated hours, and a chart that "
                         "spaces bars by index would render a 4-hour outage identically to "
                         "a 1-hour gap. 'nothing ran' and 'ran and found nothing' are "
                         "different facts and are drawn differently."),
                "rows": hourly,
            },
            "per_source_24h": {
                "sql": _flat(SQL_PER_SOURCE),
                "fields": "activity_log.source + counters + duration_seconds",
                "note": ("avg_duration_s is how long a RULE RUN took. It is NOT request "
                         "latency — Scope stores no request timings at all. "
                         "duration_missing counts rows where the column is NULL."),
                "rows": per_source,
            },
            "heat_grid_24h": {
                "sql": _flat(SQL_HEAT),
                "fields": "activity_log.source x hour bucket of run_at, SUM(events_scanned)",
                "rows": heat,
            },
            "scale_reference_30d": {
                "sql": _flat(SQL_PEAK),
                "why": ("Scope declares NO throughput capacity, so the only honest "
                        "full-scale mark for a gauge is the peak hour actually observed "
                        "in the last 30 days. It is a high-water mark, not a limit."),
                "row": peak,
            },
            "run_split_24h": {
                "sql": _flat(SQL_PER_SOURCE) + "  -- classified in Python, see below",
                "why": ("'Rule runs' must exclude infrastructure. Classification is "
                        "explicit in telemetry._is_rule_source because SQL's "
                        "LIKE 'RULE_%' treats _ as a WILDCARD and would also match "
                        "unrelated sources; prod additionally holds space-spelled "
                        "labels (RULE ADSB) beside underscore ones (RULE_ADSB)."),
                "row": {"rule_runs": rule_runs,
                        "non_rule_runs": all_runs - rule_runs,
                        "all_runs": all_runs},
            },
            "source_label_variants": {
                "sql": _flat(SQL_ALL_SOURCES) + "  -- collision detected in Python",
                "why": ("The SAME rule is logged under two different source labels in "
                        "prod (underscore and space spellings). Reported so a per-source "
                        "table can say so, rather than merging two labels into an "
                        "identity nobody verified. Computed ALL-TIME, not over the 24 h "
                        "window: the space spellings are legacy, so a window-scoped check "
                        "would report no collision about a table that has several."),
                "row": variants,
            },
            "alerts_written_24h": {
                "sql": _flat(SQL_ALERTS_WRITTEN),
                "why": ("🔴 THE FUNNEL'S LAST STAGE IS THIS, NOT SUM(alerts_emitted). "
                        "The counter is not an alert count — see emitted_counter_truth."),
                "row": written,
            },
            "rule_funnel_24h": {
                "sql": _flat(SQL_PER_SOURCE) + "  -- summed over DETECTION rules only, in Python",
                "why": ("🔴 SCOPED BY SEMANTICS, NOT BY SOURCE TYPE — and that correction is "
                        "the FOURTH time this surface shipped a real number under a false "
                        "label. Scoping to detection rules was the previous fix; a verifier "
                        "showed it left a LARGER error than it removed, because "
                        "`events_flagged` is a genuine filter output in only 5 sources. "
                        "RULE_01B/RULE_09/RULE_10/RULE_15/RULE_ANOMALY pass the SAME value to "
                        "scanned and flagged; RULE_11/RULE_16 pass a DB-write count; "
                        "RULE_02/RULE_CLUSTER pass an aggregate in a different unit. "
                        "RULE_ANOMALY is the sharpest case: it writes scanned=flagged=emitted, "
                        "structurally identical to decay_alerts.py, which this module cites as "
                        "its reason for excluding NON-rule sources — and being a rule, the old "
                        "scoping let it through. Each column is now summed over exactly the "
                        "sources where that column means what its name says; every exclusion "
                        "is listed with what it actually counts; and the figures the old "
                        "scoping would have produced are returned alongside so the size of "
                        "the correction is visible rather than quietly restated."),
                "row": {"rule_scanned": rule_scanned, "rule_flagged": rule_flagged,
                        "alerts_written": written.get("alerts_written_24h"),
                        "excluded_non_rule_scanned": non_scanned,
                        "excluded_non_rule_flagged": non_flagged,
                        "scanned_if_scoped_by_source_type_only": old_rule_scanned,
                        "flagged_if_scoped_by_source_type_only": old_rule_flagged,
                        # the only three numbers between which a ratio is defined
                        "ratio_scanned": ratio_scanned,
                        "ratio_flagged": ratio_flagged,
                        "ratio_written": ratio_written,
                        "alerts_outside_ratio_population": written_outside},
                "ratio_population": ratio_sources,
                "ratio_population_note": (
                    "Ratios are computed ONLY over these sources — the ones where "
                    "`scanned` really is a record count AND `flagged` really is a "
                    "filter output. The three headline totals are each summed over a "
                    "different (correct) set, so dividing one by another would count "
                    "sources in the numerator that the denominator excludes."),
                "alerts_outside_ratio_population_detail": written_outside_detail,
                "excluded_sources": excluded_detail,
                "counter_meaning_note": (
                    "scanned_means / flagged_means / emitted_means are read off each "
                    "writer's own record_activity() call site. A source with null "
                    "meanings is NOT documented — it is never given another source's "
                    "gloss, because doing exactly that produced a false label here."),
            },
            "emitted_counter_truth": {
                "sql": _flat(SQL_EMITTED_TRUTH),
                "by_source_sql": _flat(SQL_EMITTED_NON_RULE),
                "why": ("activity_log.alerts_emitted is documented as 'alerts inserted'. "
                        "Measured against prod that is FALSE: it means 'units of work "
                        "this job reported producing', which for REFRESH_TICKERS is a "
                        "refreshed ticker, for LABEL_OUTCOMES a labelled outcome and for "
                        "DB_BACKUP a snapshot FILE. It is not an alert count in either "
                        "direction — non-rule jobs inflate it, while activity_log starts "
                        "later than the alerts table so the rule-only subtotal "
                        "under-counts all-time."),
                "row": emit_truth,
                "non_rule_contributors": non_rule_emitted,
                "rule_emitted_all_time": rule_emitted,
            },
            "funnel_monotonic_violations": {
                "sql": _flat(SQL_FUNNEL_VIOLATIONS),
                "why": ("scanned -> flagged -> written reads as a SUBSET chain. It is not "
                        "strictly one: these sources have logged flagged > scanned. The "
                        "page must therefore not call it a strict funnel."),
                "rows": violations,
            },
            "scheduler_failures": {
                "sql": _flat(SQL_FAILURES),
                "fields": ("activity_log rows with source='SCHEDULER_JOB_FAILURE' — the "
                           "universal safety net, so no scheduled-job failure is silent"),
                "row": failures,
            },
            "scheduler_failure_detail_7d": {
                "sql": _flat(SQL_FAILURE_DETAIL), "rows": fail_detail,
            },
            "alerts_per_rule": {
                "sql": _flat(SQL_ALERTS_PER_RULE),
                "fields": "alerts.rule + alerts.created_at",
                "rows": per_rule,
            },
            "severity_24h": {
                "sql": _flat(SQL_SEVERITY),
                "note": ("severity is MUTABLE — scripts/decay_alerts.py runs nightly and "
                         "can change it, so this mix is a current reading, not a "
                         "historical record of what fired at what severity."),
                "rows": severity,
            },
            "signed_leg_verdict_rule06": {
                "sql": _flat(SQL_SIGNED),
                "populated_by_rule_sql": _flat(SQL_CORROBORATES_POPULATED),
                "fields": ("alerts.corroborates (m014) — the signed-leg verdict. "
                           "TRI-STATE: 1 corroborates, 0 does not, NULL = UNKNOWN and "
                           "fails closed."),
                "why": ("Scoped to RULE_06 on purpose: SIGNED_RULES is {'RULE_06'}, but "
                        "the column is also POPULATED on RULE_01B (written but inert — "
                        "the gate ignores it for an unsigned rule). A verdict computed "
                        "over every populated row would fold in RULE_01B as if it had "
                        "been adjudicated. The rate's denominator excludes NULLs because "
                        "unknown is not the same as no."),
                "row": signed,
                "populated_by_rule": corr_pop,
            },
            "corroborations": {
                "sql": _flat(SQL_CORROBORATIONS),
                "why": ("A COUNT, never a rate. With this few RULE_10 rows a percentage "
                        "would be a one-row statistic dressed as a trend."),
                "row": corr,
            },
            "corroboration_detail": {
                "sql": _flat(SQL_CORROBORATION_DETAIL),
                "fields": ("alerts.tags (instruments[] + instrument_count, written by the "
                           "gate) + evidence_confidence. Never re-derived here — the "
                           "corroboration model must not be reimplemented outside it."),
                "rows": corr_detail,
            },
            "scoring_backlog": {
                "sql": _flat(SQL_BACKLOG),
                "fields": "alerts.opportunity_score / evidence_confidence",
                "why": "The MONITOR_ENRICH_STALL criterion verbatim.",
                "row": backlog,
            },
            "outcome_labeling": {
                "sql": _flat(SQL_OUTCOMES),
                "reasons_sql": _flat(SQL_OUTCOME_REASONS),
                "fields": "alert_outcomes.status, written only by scripts/label_outcomes.py",
                "why": ("Labelling PROGRESS only. No win rate is derived from it here — "
                        "see the omissions table on the page."),
                "status_meaning": {
                    "complete":    "a 20-trading-day close exists, so the forward return is measured",
                    "pending":     "the +20-day horizon has not elapsed yet",
                    "unavailable": ("no usable single-equity symbol, or no price series — see the "
                                    "reason breakdown; it is NOT simply 'unpriceable'"),
                    "excluded":    ("QUARANTINED: the alert's rule had known-bad attribution, so "
                                    "measuring it would score the wrong company"),
                },
                "rows": outcomes,
                "reasons": out_reasons,
                "note": ("Statuses are rendered from the data, not from a hardcoded list of "
                         "three — `excluded` existed in the writer and in no version of this "
                         "page until a re-audit found it."),
            },
            "coverage": {
                "sql": _flat(SQL_COVERAGE), "row": coverage,
            },
        },
    }

    # A block whose SQL failed carries `unavailable` with the reason. The page must
    # render that as "unavailable", never as a measured zero.
    #
    # 🔴 MARKING THE BLOCK WAS NOT ENOUGH, AND A TEST WRITTEN FOR THE PAGE CAUGHT IT
    # HERE INSTEAD. A failed read leaves `per_source` as an empty list, and every
    # figure derived from it — `SUM(...)` over nothing, `len(...)` of nothing — is a
    # perfectly well-formed **0**. So the payload handed the page a real zero for a
    # measurement that never happened, and the page had no way to tell it from a
    # genuine quiet window. The `unavailable` flag existed; the zero sat right beside
    # it and won, because a renderer prints the number in front of it.
    #
    # Derived scalars on an unavailable block are therefore nulled: unknown is None,
    # never 0. This is the same defect as the labels — a real-looking number standing
    # in for something that is not that — reached through arithmetic instead of prose.
    for block, labels in _BLOCK_SQL.items():
        reasons = [_DEGRADED[l] for l in labels if l in _DEGRADED]
        if not (reasons and block in payload["metrics"]):
            continue
        blk = payload["metrics"][block]
        blk["unavailable"] = "; ".join(sorted(set(reasons)))
        if isinstance(blk.get("row"), dict):
            blk["row"] = {k: None for k in blk["row"]}
        for k, v in list(blk.items()):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                blk[k] = None
    return payload


