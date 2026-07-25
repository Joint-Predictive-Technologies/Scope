---
aliases: [Generic Ticker Surfacing Diagnosis, Convergence Diagnosis]
type: diagnosis
status: complete
priority: high
date: 2026-07-25
branch: diagnose/generic-ticker-surfacing
tags: [scoring, surfacing, convergence, calibration, diagnosis]
related: [[Scoring System]], [[RULE Design Decisions]], [[Current Blockers]]
---

# Diagnosis — is convergence measuring signal, or popularity?

Read-only pass. No scoring, rule, threshold, or surfacing code was changed.
Every claim below is backed by the query that produced it.

## ⚠️ Two caveats that bound everything in this report

**1. This is NOT the production DB.** No Railway CLI is installed and no
production credentials are present in this environment. All queries ran against
the local snapshot `Scope/data/jpt.db` (5.7 MB, last alert `2026-07-20 13:25:14`
— 5 days stale as of writing). Numbers should be treated as directionally
indicative of prod, not as prod's actual state.

**2. The snapshot is missing ~62% of the alert rows it once held.**

```sql
SELECT MIN(id), MAX(id), COUNT(*), MAX(id)-MIN(id)+1-COUNT(*) missing FROM alerts;
-- min_id=1  max_id=8874  rows=3347  missing=5527
```

Alert ids are contiguous *within* each day but gap heavily across days (e.g.
2026-07-08 spans ids 704–2742 but holds only 145 rows). Concentration figures
computed on a 38%-complete population carry real uncertainty. Re-running this
against prod is a prerequisite for treating any number here as final.

---

## 1. Scoring components as-built

### Opportunity score — `jpt_common.py:769-785`

```
opportunity = novelty*40 − (absorption_pct/100)*30 + horizon*20 + win_rate*10
              then × liquidity_score, clamped 0–100
horizon_scores = {IMMEDIATE:1.0, SHORT:0.85, MEDIUM:0.65, LONG:0.45}
```

- `win_rate` is a **fixed 0.5 placeholder → +5.0 on every alert** (documented as
  reserved for realized per-rule win rate from `alert_outcomes`).
- `liquidity_score` defaults **1.0** and is never passed a real value — no caller
  supplies it.

### Evidence confidence — `jpt_common.py:751-766`

```
base = 0 | 40 (drc>=4) | 60 (drc>=5) | 75 (drc>=6)
base += avg_source_quality_weight * 20        # Primary 1.0 / Secondary 0.6 / Derived 0.3
if has_conflict: base *= 0.7
```

### Novelty — `jpt_common.py:807-822`

```sql
SELECT COUNT(*) FROM alerts
 WHERE rule = ?
   AND (headline LIKE '%anchor%' OR COALESCE(why_matters,'') LIKE '%anchor%')
   AND created_at >= datetime('now','-30 days')
```
`novelty = 1.0` if count==0, else `max(0.1, 1/(1+ln(count+1)))`. The anchor is
the ticker (or a cluster fingerprint for RULE_CLUSTER). Note this is a **LIKE
substring match on free text**, not a ticker equality join.

### DB columns as they actually exist

`alerts`: `novelty_score`, `absorption_pct`, `time_horizon`, `evidence_confidence`,
`opportunity_score`, `source_quality`, `lifecycle_stage`, `theme_id`.

**`distinct_rule_count` is not a stored column.** It exists only as an
`insert_alert()` argument and is unrecoverable after write except by inverting
`evidence_confidence`.

### What actually surfaces (Step 0, the part that reframes the whole question)

| Surface | Ranked by |
|---|---|
| morning_brief HERO (`scripts/morning_brief.py:131`) | distinct source-type count → HIGH/CRIT count → volume. **No score** |
| morning_brief OVERNIGHT SIGNALS (`:199-207`) | severity bucket → recency, then diversity round-robin. **No score** |
| morning_brief HEADLINE (`:172-182`) | opportunity_score → evidence_confidence → recency |
| morning_brief ACTIVE THESES (`:234-240`) | opportunity_score DESC — **reads `themes`, which has 0 rows** |
| generate_brief top-20 (`scripts/generate_brief.py:27-45`) | severity → **hardcoded rule priority** → recency. **No score** |
| send_digest top-5 email (`scripts/send_digest.py:41-51`) | severity → hardcoded rule priority → recency. **No score** |
| War room: cluster index/detail (`api/routers/warroom.py:58-64`, `:118-124`) | **recency only** (scores selected and displayed, never ordered on) |
| War room: thesis list (`api/routers/themes.py:34-42`) | opportunity_score DESC — **reads `themes`, 0 rows** |
| War room: sector (`api/routers/intel.py:42-51`) | severity + recency |
| Ticker tape (`api/main.py:593-601`) | severity + recency + rule blocklist |

**Every surface that is ranked by `opportunity_score` reads the `themes` table,
which is empty. Every surface a user actually scans is severity + recency +
hardcoded rule priority.** The scoring engine described above has, in practice,
no influence on what gets surfaced.

### Source feeds and their ticker columns

| Table | Rows | Ticker column | Notes |
|---|---|---|---|
| `transactions` | 9,967 | `ticker_id`→`tickers.symbol`, or `raw_ticker_string` | 8,249 FK-resolved |
| `lobbying_filings` | 2,113 | `ticker` | only 1,573 non-empty, **26 distinct tickers** |
| `contracts` | 171 | `ticker` | only 64 of 171 populated, **13 distinct tickers** |
| `reddit_posts` | 93 | `ticker` | extraction is broken — see §3 |
| `earnings_sentiment` | 73 | `ticker` | 20 distinct |
| `fara_filings` | 3 | **none** | no ticker column at all |
| `gdelt_events` | 273 | **none** | schema is `(id, event_id, ingested_at)` only — no payload persisted |
| `patent_filings` | **0** | `ticker` | empty |
| `price_action` | **0** | `symbol` | empty |
| `ticker_meta` | **0** | `symbol` | empty — see Gaps |
| Polymarket (RULE_07) | — | none | never persisted; collapses straight into `alerts.ticker` |

---

## 2. Findings

### Finding 0 — the convergence layer emits nothing. This is the headline.

```sql
SELECT COUNT(*) FROM alerts WHERE rule='RULE_10';   -- 0
SELECT COUNT(*) FROM themes;                        -- 0
SELECT COUNT(*) FROM theme_signals;                 -- 0
SELECT COUNT(*) FROM alerts WHERE theme_id IS NOT NULL;  -- 0
SELECT COUNT(*) FROM alerts WHERE headline LIKE '%CORROBORATION%'; -- 0
```

There is not one corroboration alert, theme, or theme-signal link in the
database. RULE_CLUSTER has exactly **one** row (`SPCX`, 2026-07-20).

**The premise of the brief — "is convergence measuring signal or popularity?" —
does not yet apply, because nothing is being surfaced *as* convergence.** What
the daily report and war room show is single-rule alerts ordered by severity and
recency.

There is also an unexplained discrepancy worth a separate look:

```sql
SELECT date(run_at) d, COUNT(*) runs, SUM(alerts_emitted) FROM activity_log
 WHERE source='RULE_10' GROUP BY d;
-- 2026-07-10:1/0  07-11:3/3  07-13:1/1  07-14:1/1  07-19:1/1  07-20:24/22
```

`activity_log` records **28 RULE_10 alerts emitted**, all *after* migration
`m002_purge_invalid_rule10` ran (`2026-07-10 15:26:37`, per `scope_migrations`),
so m002 does not explain their absence. `rule_10_corroboration.py:221-248` calls
`insert_alert` and writes `theme_signals` rows before incrementing `emitted`, so
a logged emit implies a committed insert. 28 committed alerts, 0 present. Same
shape for RULE_CLUSTER (19 logged emits, 1 row). This is consistent with the
62% row loss noted above and is a **DATA-LOSS-class item that needs its own
human-gated investigation** — it is out of scope for this pass.

### Step 1 — concentration

**Fired layer:**

```sql
-- all alerts with a ticker
SELECT ticker, COUNT(*) FROM alerts WHERE ticker IS NOT NULL AND ticker!=''
 GROUP BY ticker ORDER BY 2 DESC;
```

| Population | Alerts | Distinct tickers | top5 | top10 | top20 | Gini |
|---|---|---|---|---|---|---|
| All fired | 2,897 | 606 | 24.2% | 32.6% | 38.0% | 0.459 |
| Fired, RULE_10-eligible rules only | 848 | 557 | 7.4% | 11.8% | 17.8% | **0.291** |
| Fired, HIGH/CRITICAL only | 909 | 414 | 21.6% | 29.8% | 36.5% | 0.480 |

Top fired: `SPY(183)`, `COIN MSTR IBIT(164)`, `USO(155)`, `USO XLE(110)`,
`XOM(90)`, `LMT(71)`.

**Surfaced layer** (simulated — see Gaps; `daily_briefs`, `digests` and `themes`
are empty and `briefs` holds one row, so no historical surfaced set is
persisted). Each surfacing query was re-run anchored at each of the 14 days that
have alerts:

| Surface | Slots | Distinct | top5 | top10 | top20 | Gini |
|---|---|---|---|---|---|---|
| Brief top-20 | 194 | 89 | 23.7% | **37.6%** | 53.1% | 0.401 |
| Email digest top-5 | 57 | 37 | 33.3% | 50.9% | 70.2% | 0.283 |
| Ticker tape top-60 | 468 | 198 | 10.5% | 17.1% | 29.9% | 0.411 |

Brief top-15 by frequency: `RTX(12)`, `BA(9)`, `SPCX(9)`, `LMT(9)`, `NOC(7)`,
`HII(7)`, `DELL(6)`.

**Verdict:** surfacing **amplifies**. The brief's top-10 share is 37.6% against
11.8% in the eligible-rule fired population it draws from — a **3.2× increase in
concentration introduced by the ranking layer, not the rules.** The defense
primes the operator complains about (RTX/BA/LMT/NOC/HII) take 44/194 = **22.7%
of brief slots** while being **33/848 = 3.9% of eligible fired alerts** — a 5.8×
over-representation introduced purely by ranking:

```sql
SELECT COUNT(*) FROM alerts WHERE ticker IN ('RTX','BA','LMT','NOC','HII')
  AND rule NOT IN ('RULE_07','RULE_OSINT','RULE_REDDIT','RULE_ANOMALY');  -- 33 of 848
```

The mechanism is identifiable. `scripts/generate_brief.py:36-41` hardcodes
`RULE_11` (federal contracts) to sort priority 2, ahead of everything except the
never-firing RULE_10. RULE_11's entire ticker universe is 13 names:

```sql
SELECT ticker, COUNT(*) n, ROUND(100.0*COUNT(*)/48,1) pct FROM alerts
 WHERE rule='RULE_11' AND ticker IS NOT NULL AND ticker!='' GROUP BY ticker ORDER BY n DESC;
-- RTX 8 (16.7%) | BA 7 (14.6%) | NOC 5 (10.4%) | LMT 5 (10.4%) | HII 5 (10.4%)
-- SPCX 3 | HUM 3 | TXT 2 | PSN 2 | OSK 2 | LDOS 2 | HON 2 | AMTM 2
```

62.5% of the contracts feed is the five defense primes, and the brief promotes
that feed to near-top priority by rule name. This is a hardcoded editorial
preference, not a measurement.

### Step 2 — base rate / surprise

**Formula used** (stated as required; this is an approximation, not a rigorous
PMI):

```
p(t)      = alerts on t / all alerts with a ticker            (popularity prior)
m_R(t)    = alerts of rule R on t / alerts of rule R with a ticker
lift_R(t) = m_R(t) / p(t)
CL(t)     = geometric mean of lift_R(t) over the rules R that fired on t
```

Marginals are computed over the **alert stream per rule**, which is the space
RULE_10 actually counts in (it counts distinct rules over `alerts`, not raw feed
records). `CL ≈ 1` ⇒ every source mentions the ticker at exactly its overall
base rate — no surprise. `CL >> 1` ⇒ sources specifically concentrate on it.

**Top fired tickers:**

| ticker | fired | rules | p(t)% | CL |
|---|---|---|---|---|
| SPY | 183 | 2 | 6.32 | **0.37** |
| USO | 155 | 3 | 5.35 | **0.39** |
| XOM | 90 | 4 | 3.11 | **0.41** |
| LMT | 71 | 4 | 2.45 | **0.96** |
| XLE | 38 | 2 | 1.31 | **1.03** |
| RTX | 28 | 3 | 0.97 | 2.56 |
| NOC | 25 | 3 | 0.86 | 2.45 |
| CVX | 22 | 3 | 0.76 | 1.39 |
| SPCX | 12 | 5 | 0.41 | 8.74 |

**Verdict: supported, with a nuance.** The highest-volume fired tickers cluster
at or *below* CL = 1 — SPY 0.37, USO 0.39, XOM 0.41, LMT 0.96, XLE 1.03. These
names are high in the output because they are high everywhere, which is exactly
the "measuring popularity" signature. But the driver is narrower than the
hypothesis assumed: SPY/USO/COIN-MSTR-IBIT volume comes almost entirely from
RULE_07 (Polymarket) and RULE_ANOMALY — **both already excluded from
corroboration**. The genuinely surprising names (SPCX CL=8.74 across 5 rules)
do exist in the data and are not being preferentially surfaced.

**Raw-feed marginal cross-check:**

```
congress_txns   rows=9646  distinct=1379  US 2.2%, MSFT 1.4%, NVDA 1.3%, GS 1.1%, AAPL 1.1%
lobbying        rows=1573  distinct=26    RTX 6.4%, META 4.8%, CMCSA 4.8%, AMGN 4.8%, GD 4.6%
contracts       rows=64    distinct=13    LMT 18.8%, RTX 15.6%, HII 15.6%, NOC 10.9%, BA 10.9%
reddit_posts    rows=93    distinct=47    EBAY 12.9%, BACK 7.5%, HERE 6.5%, POST 5.4%, TECH 4.3%
earnings_sent   rows=73    distinct=20    XOM 5.5%, SAIC 5.5%, PLTR 5.5%, PFE 5.5%, NVDA 5.5%
```

Two feeds are structurally incapable of contributing surprise: `contracts` has a
13-ticker universe and `lobbying_filings` a 26-ticker universe. Any convergence
involving them is near-guaranteed to land on a defense prime.

`reddit_posts` ticker extraction is **broken**: `BACK`, `HERE`, `POST`, `TECH`,
`RYAN`, `OPEN`, `REAL` are English words being parsed as symbols. 7 of the top 10
Reddit "tickers" are not tickers.

### Step 3 — novelty / decay decomposition

**Two of the four opportunity terms are dead constants.**

```sql
SELECT MIN(absorption_pct), MAX(absorption_pct), COUNT(DISTINCT absorption_pct) FROM alerts;
-- 0.0 | 0.0 | 1        (all 3,347 rows)
```
`score_alert_fields` (`jpt_common.py:855`) hardcodes `absorption_pct=0.0`, and
no rule passes a non-zero value to `insert_alert`. **The absorption/decay term
contributes exactly 0 to every alert ever scored.**

```sql
SELECT ROUND(evidence_confidence,1) v, COUNT(*) FROM alerts GROUP BY v;
-- 20.0: 1240 | 12.0: 1076 | 6.0: 1031      (exactly 3 distinct values)
```
Those are precisely `{Primary 1.0, Secondary 0.6, Derived 0.3} × 20`. The
`drc>=4` base (40/60/75) **never fires on a single row**, so
`distinct_rule_count` is never ≥4 anywhere in the DB. **`evidence_confidence` is
a constant lookup of the rule name and carries zero evidence information.**

So in practice: `opportunity = novelty*40 + horizon*20 + 5`, observed range
24.0–65.0 against a nominal 0–100.

**Decomposition, top surfaced tickers** (all `absorption` = −0.0, all `win_rate`
= +5.0):

| ticker | n | novelty | nov×40 | horiz×20 | opp |
|---|---|---|---|---|---|
| DELL | 13 | 0.354 | 14.2 | 13.0 | 33.1 |
| NKE | 6 | 0.534 | 21.4 | 17.0 | 41.4 |
| ASTS | 4 | 0.562 | 22.5 | 17.0 | 42.5 |
| SAIC | 3 | 0.553 | 22.1 | 17.0 | 42.8 |
| AMAT | 4 | 0.476 | 19.1 | 17.0 | 39.1 |

**Does decay bite? Yes — measurably.** Day-over-day for the 5 most-recurring
tickers:

```
SPY:  06-17 nov=0.154 opp=31.2 | 07-09 0.154/31.2 | 07-10 0.188/32.3 | 07-19 0.190/32.6 | 07-20 0.189/32.6
USO:  07-09 0.162/31.5 | 07-10 0.183/32.2 | 07-11 0.159/31.4 | 07-15 0.156/31.2 | 07-20 0.164/31.5
XOM:  06-17 0.591/45.6 | 07-09 0.163/31.5 | 07-14 0.159/31.4 | 07-19 0.154/31.2 | 07-20 0.226/33.5
```

Repeat offenders sit at novelty ≈ 0.15–0.19, near the 0.1 floor, with
opportunity ≈ 31–32 — *below* the 40–43 of the fresher surfaced names. XOM's
first-ever appearance scored 0.591/45.6 and collapsed to 0.163/31.5 within
three weeks.

**Verdict: novelty decay is NOT the problem — it works. It is simply
inert**, because no user-facing list orders on `opportunity_score` (Step 0
table). The engine correctly computes that SPY is stale and then surfaces it
anyway on severity+recency.

One artifact to flag rather than explain away: on 2026-07-17, SPY, USO and XOM
*all* jump to exactly `novelty=0.477` for a single alert each. Identical values
across unrelated tickers suggest the `LIKE '%anchor%'` free-text match, not
ticker identity, is what the count keyed on that day. Not investigated further
in this pass.

### Step 4 — day-over-day repetition

Jaccard of consecutive **days-with-alerts** (not calendar days — the snapshot
has gaps, e.g. 2026-06-23 → 2026-07-08).

```
BRIEF top-20:        mean Jaccard = 0.42
  07-11 vs 07-12: 1.00   shared = BA, HII, HUM, LMT, NOC, RTX, SPCX
  07-12 vs 07-14: 0.88   shared = BA, HII, HUM, LMT, NOC, RTX, SPCX
  06-17 vs 06-18: 1.00

TICKER TAPE top-60:  mean Jaccard = 0.50
  07-11 → 07-12 → 07-14 → 07-15 → 07-17:  0.86, 1.00, 0.96, 1.00, 1.00
```

**Verdict: confirmed.** The surfaced set is near-static across multi-day runs,
and the identical set repeating on the brief for 07-11/07-12/07-14 is exactly
the defense-prime block: BA, HII, HUM, LMT, NOC, RTX, SPCX. This connects
directly to Step 2 (base-rate dominance via the 13-ticker contracts feed) and
Step 3 (novelty computed but never used to rank). The mean of 0.42/0.50 understates
it, because the calendar gaps inject artificial 0.00 transitions.

### Step 5 — liquidity / attention profile

```sql
SELECT COUNT(*) FROM ticker_meta;   -- 0
SELECT COUNT(*) FROM price_action;  -- 0
```

**No market-cap, float, volume, ADV, shares-outstanding, exchange, or sector
field is populated anywhere in the DB.** `ticker_meta.market_cap` exists as an
INTEGER column but the table is empty — it is a lazy write-through cache
(`api/routers/tickers.py:151-188`) populated only when someone hits
`GET /{symbol}/meta`, which has never happened.

As instructed, mention-frequency is used as the attention proxy: the Step 2
`p(t)` column is the size/attention measure throughout this report. Acquiring a
real market-cap or tradability field is a probable prerequisite for the eventual
fix — flagged in Gaps, not invented here.

### Step 6 — outcome table reality check

```sql
SELECT COUNT(*) FROM alert_outcomes;            -- 651
SELECT status, COUNT(*) FROM alert_outcomes GROUP BY status;
-- unavailable: 327 | complete: 324
SELECT COUNT(*) FROM alert_outcomes
 WHERE return_1d IS NOT NULL AND return_5d IS NOT NULL AND return_20d IS NOT NULL;  -- 324
```

Per rule:

| rule | outcomes | status=complete | full 1d/5d/20d |
|---|---|---|---|
| RULE_07 | 435 | 135 | 135 |
| RULE_06 | 121 | 120 | 120 |
| RULE_02 | 76 | 69 | 69 |
| RULE_08 | 19 | 0 | 0 |

Every other rule — RULE_01B, RULE_09, RULE_11, RULE_CLUSTER, RULE_ANOMALY,
RULE_OSINT, RULE_10 — has **zero** outcome rows.

Composition of the 324 complete outcomes:

```sql
SELECT a.rule, o.ticker, COUNT(*) n FROM alert_outcomes o JOIN alerts a ON a.id=o.alert_id
 WHERE o.status='complete' GROUP BY a.rule, o.ticker ORDER BY n DESC LIMIT 6;
-- RULE_07/SPY 135 | RULE_06/DELL 8 | RULE_02/MSFT 6 | RULE_02/AMZN 4
-- RULE_02/NVDA 4 | RULE_02/GS 3
```

Generic (top-20 by `p(t)`) vs non-generic split of the 324 with 20d returns:
**153 generic / 171 non-generic.**

**Verdict: the ranking has no ground truth behind it, and the calibration
runway is being spent badly.**

- **135 of 324 complete outcomes (42%) are a single ticker, SPY, from RULE_07** —
  a rule explicitly excluded from corroboration as noise. The largest tracked
  cell in the dataset measures the forward return of the S&P 500 ETF against a
  prediction-market alert, which is the least informative thing the system could
  be learning.
- The largest **non-SPY** per-(rule, ticker) cell is RULE_06/DELL at **n=8**.
- **No rule has enough complete, non-generic outcomes to compute a
  non-placeholder win rate.** Per the brief's instruction, no win rate is
  computed here — the n is too small. RULE_06 (120 complete) and RULE_02 (69) are
  the only candidates with a non-trivial count, and both are spread thin across
  many mega-cap tickers.
- Meanwhile `historical_win_rate` remains the fixed 0.5 placeholder, adding a
  constant +5 to every alert — so `alert_outcomes` currently feeds nothing.

---

## 3. Which hypotheses hold

| Hypothesis | Verdict | The number that decides it |
|---|---|---|
| **Rules fire on popular names** | **Partially supported** | The top fired tickers have CL ≤ 1 (SPY 0.37, USO 0.39, XOM 0.41, LMT 0.96, XLE 1.03) — popularity, not surprise. But at the eligible-rule layer concentration is *low* (Gini 0.291, top-10 = 11.8%). The popularity is concentrated in RULE_07/RULE_ANOMALY, which corroboration already excludes. |
| **Surfacing amplifies popularity** *(added — it is the dominant effect)* | **Supported** | Brief top-10 share **37.6%** vs **11.8%** in the fired population it draws from — 3.2× concentration added by the ranking layer. Driven by `generate_brief.py:36-41` hardcoding RULE_11 to priority 2, over a feed whose 13-ticker universe is 62.5% defense primes. |
| **Novelty-decay not biting** | **Not supported** | Novelty decays correctly: SPY 0.154, USO 0.16, XOM 0.163 (floor 0.1), and XOM fell 0.591→0.163 in three weeks. The term works. It is *inert* — no user-facing list orders on `opportunity_score`; the only surfaces that do read `themes`, which has 0 rows. |
| **No calibration ground truth** | **Supported** | 324 complete outcomes; **135 (42%) are SPY from RULE_07 alone**; largest non-SPY cell is n=8. No rule has enough complete non-generic outcomes for a win rate. `historical_win_rate` is still the fixed 0.5 placeholder. |
| **Convergence measures popularity** *(the framing question)* | **Cannot be tested — no convergence output exists** | `RULE_10` alerts = **0**. `themes` = **0**. `theme_signals` = **0**. Nothing is surfaced as convergence, so there is no convergence ranking to characterize. |
| **Scoring terms are partly dead** *(added)* | **Supported** | `absorption_pct` = 0.0 on **all 3,347 rows** (1 distinct value). `evidence_confidence` has exactly **3 distinct values** (6/12/20) = source-quality weight × 20 — the `drc>=4` base never fires, so it encodes only the rule's name. |

---

## 4. Gaps — what could not be measured

1. **Production DB is inaccessible.** No Railway CLI, no credentials. Everything
   here is the local snapshot, 5 days stale. **All numbers must be re-run
   against prod before being acted on.**
2. **The snapshot is ~62% incomplete** (5,527 of 8,874 alert ids absent).
   Concentration and Gini figures are computed on a partial population.
3. **No persisted surfacing history.** `daily_briefs` = 0 rows, `digests` = 0
   rows, `themes` = 0 rows, `briefs` = 1 row (2026-07-20). Steps 1b and 4 were
   produced by **re-running the surfacing SQL anchored at each historical day**,
   not by reading what was actually shown. `briefs.meta_json` carries no ticker
   list, and `daily_briefs.alert_ids`/`evidence_json` — the only structured
   record — is empty (wiped by `m006_invalidate_stale_briefs`,
   `jpt_common.py:421-431`). Persisting the surfaced set per day is a
   prerequisite for measuring this properly.
4. **No liquidity, market-cap, float, or tradability field exists in populated
   form.** `ticker_meta` (0 rows) is the only place `market_cap` could live.
   Mention-frequency `p(t)` was used as the attention proxy throughout.
5. **`distinct_rule_count` is not stored on `alerts`** — it is an `insert_alert`
   argument only. It was recovered by inverting `evidence_confidence`, which
   works only because the value space happens to be 3 discrete points.
6. **Two feeds cannot contribute ticker-keyed evidence at all**: `gdelt_events`
   (273 rows, schema is `id/event_id/ingested_at` — no payload persisted) and
   `fara_filings` (3 rows, no ticker column). RULE_OSINT's 387 alerts derive
   from a source whose content is not retained.
7. **`reddit_posts` ticker extraction is broken** — `BACK`, `HERE`, `POST`,
   `TECH`, `RYAN`, `OPEN`, `REAL` are being stored as tickers. Any Reddit
   marginal is unreliable.
8. **511 alerts carry multi-symbol ticker strings** (`COIN MSTR IBIT`,
   `LMT RTX NOC`, `GOOGL META AMZN AAPL MSFT`) from RULE_07 (439) and RULE_08
   (72), across 31 distinct composite values. `normalize_ticker` normalizes these
   token-wise but does **not** split them, so `LMT` and `LMT RTX NOC` are
   distinct keys for corroboration, novelty and outcome purposes. Per-ticker
   marginals for those names are understated by an unmeasured amount.
9. **28 RULE_10 emissions logged in `activity_log` have no corresponding alert
   rows**, and they post-date the only migration that deletes RULE_10 records.
   Unexplained. Flagged as DATA-LOSS-class; needs a separate human-gated pass.

---

*No recommendations section by design — the fix is a separate, reviewed
decision. See [[Scoring System]] and [[RULE Design Decisions]] for the
as-designed intent this diagnosis measures against.*
