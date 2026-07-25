---
type: session-summary
stage: iPhone-1
status: completed
priority: critical
tags: [session, work-log, rule10, convergence, trace, read-only]
related: [[Current Blockers]], [[RULE Design Decisions]], [[Scoring System]]
date-created: 2026-07-25
---

# Session: RULE_10 convergence trace — never generated, not destroyed

**Date:** 2026-07-25
**Duration:** ~1 hour
**Branches:** `trace/rule10-convergence` (findings file only — no code, no data changes)
**Status:** Completed

## Goal

Answer exactly one question: is convergence output (RULE_10 alerts, themes,
theme_signals) **never generated**, or **generated and then destroyed**?

## Outcome

**Done.** The question is answered definitively, from the local snapshot plus git
history, without needing production. The prior DATA-LOSS suspicion is **retracted**.

## What changed

**Nothing. Read-only pass.** No code edits, no DB writes, no migrations run, no
fixes applied. The only artifact is this file. Historical DB versions were
extracted from git into a scratchpad directory for querying; the working-tree DB
was opened `mode=ro` throughout.

---

## Findings — CONFIRMED

All observations are from the local snapshot `Scope/data/jpt.db` (branch
`trace/rule10-convergence`, identical to `main`), or from earlier versions of
that same file extracted from git history. **None are from production.**

### C1. Every RULE_10 alert ever emitted was a synthetic test fixture

`tests/test_war_rooms.py:108-133` and `tests/test_phase3.py:56` both import the
**real** RULE_10 entry point and run it against the **real** database via
`db_connection()` — there is no fixture DB, no temp file, no transaction rollback:

```python
# tests/test_war_rooms.py:108-116
from scripts.rule_10_corroboration import run as run_r10
conn = db_connection()
conn.execute("DELETE FROM alerts WHERE ticker='ZWAR'")
for rule in ("RULE_01B", "RULE_06", "RULE_08", "RULE_11"):
    conn.execute("INSERT INTO alerts (rule, ticker, severity, headline, created_at) "
                 "VALUES (?, 'ZWAR', 'HIGH', ?, datetime('now'))", (rule, f"{rule} ZWAR"))
conn.commit(); conn.close()
run_r10(dry_run=False, window_hours=24)
```

The test seeds exactly 4 eligible-rule alerts on ticker `ZWAR`, which is precisely
`MIN_DISTINCT_RULES = 4` (`scripts/rule_10_corroboration.py:82`), fires real
RULE_10, asserts a theme was created, then deletes the theme, its theme_signals,
and the ZWAR alerts (`tests/test_war_rooms.py:128-133`).

**The arithmetic closes exactly.** From the snapshot:

```sql
SELECT (SELECT seq FROM sqlite_sequence WHERE name='themes')        AS themes_seq,
       (SELECT seq FROM sqlite_sequence WHERE name='theme_signals') AS tsig_seq,
       (SELECT SUM(alerts_emitted) FROM activity_log WHERE source='RULE_10') AS r10_emits;
-- 28 | 140 | 28
```

- **28 RULE_10 emits** logged in `activity_log`, all time.
- **28 themes** ever created (`sqlite_sequence`), 0 rows remain — 1 theme per emit.
- **140 theme_signals** ever created, 0 rows remain — **28 × 5**, where 5 =
  1 corroboration alert + the 4 ZWAR evidence alerts linked at
  `rule_10_corroboration.py:239-246`.

Every single emit is accounted for by the ZWAR fixture. There is no residue of any
real-ticker convergence.

### C2. No real ticker has ever met the firing gate

RULE_10 requires 4+ distinct **eligible** rules (excluding RULE_07 / RULE_OSINT /
RULE_REDDIT / RULE_ANOMALY / RULE_10) on the same ticker, HIGH/CRITICAL, within
24h (`rule_10_corroboration.py:85-128`). Replaying that exact gate over every 24h
window in the snapshot:

```sql
WITH cand AS (
  SELECT ticker, rule, created_at FROM alerts
   WHERE ticker IS NOT NULL AND ticker!=''
     AND rule NOT IN ('RULE_07','RULE_OSINT','RULE_REDDIT','RULE_ANOMALY','RULE_10')
     AND severity IN ('HIGH','CRITICAL')),
w AS (SELECT a.ticker, COUNT(DISTINCT b.rule) dr FROM cand a JOIN cand b
       ON b.ticker=a.ticker AND b.created_at >= a.created_at
      AND b.created_at < datetime(a.created_at,'+24 hours')
     GROUP BY a.ticker, a.created_at)
SELECT COUNT(*) FROM w WHERE dr >= 4;
-- 0
```

**Zero.** Best-ever real ticker is `SPCX` at **3** distinct eligible rules
(2026-07-20); then `HII`, `LMT`, `TSM` at 2. The gate has never been met by real
data, so `found=1` on every logged RULE_10 run is the ZWAR fixture and nothing else
— consistent with `events_scanned=1` on all 28 rows.

### C3. The migration hypothesis is ruled out

Only two production statements can delete from `alerts`, both inside
`_initialize_schema`:

- `jpt_common.py:324` (m001) — `DELETE FROM alerts WHERE rule='RULE_10' AND tags LIKE '%GDELT%'`
- `jpt_common.py:340` (m002) — deletes RULE_10 rows failing `rule10_is_valid`

Both are **guarded** by a `scope_migrations` lookup (`jpt_common.py:314-316`,
`:330-332`) and both are recorded as applied on **2026-07-10** (m001 10:35:12,
m002 15:26:37). All 28 emits post-date them. Even if m002 re-ran, it would not
delete these rows: `_candidate_alerts` already excludes ineligible rules, so the
`tags.rules` list is 4+ eligible entries and `rule10_is_valid` returns True.

There is **no** DELETE statement against `themes` or `theme_signals` anywhere in
production code — only in `tests/test_phase3.py:23-24` and
`tests/test_war_rooms.py:130-131`. Since 28 themes were created and 0 remain, the
deleter must be the tests. It cannot be a migration.

### C4. The rows were committed, then deleted — by the test teardown, in-process

`sqlite_sequence` for an AUTOINCREMENT table only advances on a **committed**
insert, and a rollback reverts it. Across committed versions of the DB file:

| commit | date (local) | `seq` alerts | `max(id)` alerts | gap |
|---|---|---|---|---|
| 487d401 | 07-20 15:25:34 | 8874 | 8874 | 0 |
| b55e88c | 07-20 18:34:08 | 8900 | 8874 | 26 |
| 7298829 | 07-20 20:56:28 | 8926 | 8874 | 52 |
| 9f77654 | 07-21 13:01:23 | 9004 | 8874 | 130 |

The gap grows monotonically while `max(id)` is frozen: rows were genuinely
committed and then removed. Combined with C1/C3, the remover is the test
teardown, which runs in the same process moments after the insert.

### C5. `activity_log` "emitted" counts are real but describe test runs

The emit path is ordered correctly — `insert_alert` commits at
`jpt_common.py:1040`, the `theme_signals` writes commit at
`rule_10_corroboration.py:247`, and only then does `emitted += 1` (`:248`) and
`record_activity` fire (`:251`). **A logged emit does imply a committed row.** The
brief's step-3 claim holds. What it does *not* imply is a *production* row.

The burst signature confirms test provenance. On 2026-07-20 the only sources
logging after 13:25 were RULE_CLUSTER and RULE_10, in tight repeating groups:

```
319 RULE_CLUSTER 16:13:37   |  330 RULE_CLUSTER 18:50:18
320 RULE_10      16:13:38   |  331 RULE_10      18:50:18
321 RULE_10      16:13:39   |  332 RULE_10      18:50:20
```

One RULE_CLUSTER + **two** RULE_10 rows per burst — exactly the two test files
that invoke RULE_10 (`test_phase3.py:56`, `test_war_rooms.py:108`) plus the one
that invokes RULE_CLUSTER (`test_phase3.py:31`). No scheduled rule ran in that
window (no RULE_07 at 20 min, no RULE_ADSB at 5 min), so these were **local test
suite runs, not the scheduler** — matching `Scope/CLAUDE.md`'s "tests must pass
before commit" convention and the commits at 16:34 and 18:56.

Both RULE_10 rows in each pair report `emitted=1`. Had the first genuinely
persisted a row, the second — one second later — would have been filtered by
`_already_corroborated`'s 7-day dedup (`rule_10_corroboration.py:100-111`,
verified present at commit `b55e88c`) and logged `emitted=0`. It didn't, because
the teardown had already removed it.

### C6. The "~62% of alert rows missing" figure is explained, not alarming

The prior diagnostic flagged 5,527 missing ids between 1 and 8,874. Git history
attributes these to **deliberate, documented purges**, not loss — e.g. commit
`3dd7df5` *"fix: Rule 10 runaway corroboration — 2134 false alerts purged"*,
`c03c1b3` *"fix: purge bulk-ingestion false flags"*, `6387ff3` *"remove 3
bad-date congress trades"*. Id gaps from intentional deletion are expected.

### C7. `Scope/data/jpt.db` is tracked in git

```
$ git ls-files --error-unmatch Scope/data/jpt.db
Scope/data/jpt.db
```

42 commits touch it. `sqlite_sequence` moves **backward** between two of them
(`7298829` 20:56 → `b113d6e` 21:03 shows alerts seq 8926 → 8913, activity_log
337 → 330), which is impossible in one linear DB history and confirms the binary
file is being restored to older states by ordinary git operations. This does not
cause the RULE_10 finding (C1–C5 settle that independently) but it does make the
committed DB an unreliable historical record.

---

## Findings — HYPOTHESIS (not proven here)

- **H1.** Test pollution of the real DB is likely broader than RULE_10. Tests
  write and delete `alerts`, `transactions`, `themes`, `theme_signals`,
  `alert_votes`, `alert_annotations`, `war_rooms` and `daily_briefs` on the live
  `db_connection()`. The 130-row alert sequence gap by 07-21 is consistent with
  this, but I did not attribute each row individually.
- **H2.** Because the tests exercise RULE_10 against live data with a 24h window,
  a real ticker that *did* qualify while a test ran could have its corroboration
  emitted under test conditions. Not observed (C2 shows the gate was never met),
  but it is a live risk if the gate is ever loosened.

## Findings — FLAGGED FOR LATER

- **F1.** The `RULE_10` docstring (`rule_10_corroboration.py:258-259`) still says
  *"fire when 2+ distinct fundamental rules hit the same ticker within 48h"* while
  the code enforces 4 rules / 24h. Stale, misleading, harmless.
- **F2.** `log_activity` swallows exceptions (`jpt_common.py:1060+`) and
  `record_activity` wraps it in `except Exception: pass`. `activity_log` has real
  id gaps (323, 329, 334 missing on 07-20), so some run records are silently lost.
- **F3.** A tracked binary DB in git (C7) will keep producing confusing history.
- **F4.** RULE_CLUSTER shows the same pattern — 19 logged emits, 1 surviving row —
  and `test_phase3.py` seeds a `ZCLU` cluster fixture the same way.

---

## Verdict

### **(b) Never generated.**

Convergence output for real tickers has **never existed**. It was not destroyed.

The evidence, in order of decisiveness:

1. **The gate has never been met by real data** — 0 ticker/24h-windows ever
   reached 4 distinct eligible rules; the maximum ever observed is 3 (`SPCX`). (C2)
2. **All 28 logged emits are fully accounted for by the ZWAR test fixture** —
   28 themes and 140 = 28 × 5 theme_signals match the fixture's shape exactly,
   with nothing left over. (C1)
3. **The migration cannot be the cause** — m001/m002 are guarded, applied
   2026-07-10 before every emit, and would not match these rows anyway; and no
   production code deletes `themes`/`theme_signals` at all. (C3)
4. **The rows were committed then deleted, in-process, by the test teardown** —
   proven by the `sqlite_sequence`-vs-`max(id)` gap and by the second RULE_10 run
   in each pair failing to dedup against the first. (C4, C5)

**The DATA-LOSS-class flag raised by the 2026-07-24 diagnostic is retracted.** The
28 "vanished" alerts were synthetic test fixtures that the tests deliberately
cleaned up. Nothing was lost. What is real — and worse for the product — is that
**the moat feature has never fired once in production.**

### The timeline (2026-07-20, UTC; commit times converted from +0200)

| time | event |
|---|---|
| 13:25:14 | last real alert written (id 8874) |
| 13:25:34 | commit `487d401` — alerts seq 8874 = max(id), no gap |
| 16:13:37-39 | test run: RULE_CLUSTER + RULE_10 ×2, 3 emits logged |
| 16:33:37-39 | test run: same signature, 3 emits logged |
| 16:34:08 | commit `b55e88c` — **seq 8900, max(id) still 8874: 26 committed rows already gone** |
| 18:50:13-20 | test run: 2 no-op RULE_10 + RULE_CLUSTER + RULE_10 ×2 |
| 18:56:07-09 | test run: RULE_CLUSTER + RULE_10 ×2 |
| 18:56:28 | commit `7298829` — seq 8926, gap 52 |

Note there is **no migration run and no deploy anywhere in this timeline**. The
deletions happen between an emit and a commit minutes later, inside test runs.

---

## Provenance

- **Every number** in this file comes from `Scope/data/jpt.db` on branch
  `trace/rule10-convergence` (byte-identical to `main`), opened read-only, or from
  earlier versions of that same file extracted via `git show <commit>:Scope/data/jpt.db`
  into a scratchpad. Snapshot's last alert: `2026-07-20 13:25:14` UTC.
- **Production was not reachable** — no Railway CLI, no credentials. Not required
  for this verdict: C1's arithmetic and C2's zero are self-contained, and the
  git-history evidence is independent of the snapshot's completeness.
- **What a prod re-run would confirm** (worth doing before acting):
  ```sql
  -- 1. Has convergence ever fired in prod?
  SELECT COUNT(*) FROM alerts WHERE rule='RULE_10';
  SELECT seq FROM sqlite_sequence WHERE name IN ('themes','theme_signals');
  SELECT SUM(alerts_emitted) FROM activity_log WHERE source='RULE_10';
  -- If themes_seq == r10_emits and theme_signals_seq == 5 * r10_emits, prod is
  -- test-fixture-only too. If themes_seq exceeds emits, prod HAS fired for real.

  -- 2. Has the gate ever been met in prod? (the C2 query, unmodified)
  ```
  The distinguishing signal is whether prod's `theme_signals` seq is exactly
  5× its RULE_10 emit count. Any deviation means real convergence occurred there.

---

## Next thread — what Priority #1 hands to Priority #2

Priority #1 is closed: convergence emits nothing because **the 4-rule / 24h gate
has never been satisfied**, not because output is being lost. That makes
Priority #2 — *why does nothing converge?* — the whole remaining question, and it
is now well-posed rather than speculative.

The gap to close is **3 → 4 distinct eligible rules**. Concretely, Priority #2
should test whether the gate is unreachable because of key fragmentation rather
than genuine absence of signal:

- **Multi-symbol ticker strings.** 511 alerts store composites like
  `LMT RTX NOC` and `COIN MSTR IBIT` as single opaque keys (RULE_07 439,
  RULE_08 72). `normalize_ticker` normalizes token-wise but never splits, so
  `LMT` and `LMT RTX NOC` are different tickers to the corroboration GROUP BY.
  RULE_08 is an *eligible* rule, so its 72 composite rows are directly suppressing
  the count.
- **The severity gate.** `_candidate_alerts` requires HIGH/CRITICAL, cutting the
  eligible pool to 909 of 2,897 tickered alerts.
- **Cadence mismatch.** RULE_09 is daily, RULE_12 weekly, RULE_14 twice-weekly —
  four *distinct* rules landing inside the same 24h window may be structurally
  near-impossible regardless of key quality.
- **Broken Reddit extraction** (`BACK`, `HERE`, `POST` stored as tickers) is a
  data-quality issue but does **not** affect this gate — RULE_REDDIT is excluded.

**Does this reorder the queue?** Yes, mildly. The retraction of the data-loss flag
removes the emergency. But C2's zero raises the stakes on Priority #2: the
convergence moat is not degraded, it is **inert**, and has been for the entire
life of the dataset. The honest-UI question from the earlier diagnosis (labelling
surfaced items as uncalibrated) now has a sharper edge — the product's
differentiator currently produces no output at all.

A separate, smaller thread worth queueing: **tests mutate the production database**
(H1). That is the reason this investigation was needed, and it will keep
manufacturing phantom data-loss signals until addressed.

---

## Human-gated

Per `Scope/CLAUDE.md`, anything touching scoring, corroboration logic, rule
scripts, ingestion, or migrations is a **manual, human-gated session**. This pass
deliberately made **no** changes. All of the following are deferred to a
human-run session and were **not** done here:

- Changing the RULE_10 threshold, window, eligibility set, or severity gate.
- Splitting multi-symbol tickers or any ticker-normalization change.
- Isolating tests from the production DB.
- Untracking `Scope/data/jpt.db` from git.
- Fixing the stale RULE_10 docstring (F1) or the swallowed `log_activity`
  exceptions (F2).

---

### Related

[[Current Blockers]], [[RULE Design Decisions]], [[Scoring System]]
