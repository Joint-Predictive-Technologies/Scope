---
aliases: [Reading List]
type: reference
status: living
priority: medium
tags: [reference, reading-list, learning]
related: [[Master Plan]], [[Scoring System]], [[Outcome Tracking Status]], [[RULE Design Decisions]]
last-reviewed: 2026-07-23
---

# Reading List — building Scope

A curated, opinionated reading list for developing Scope. **Not** a generic
finance/ML bibliography — every entry maps to a real part of this system (a rule,
the scoring model, the outcome moat, the reasoning layer, or the stack) so the
reading pays back directly. Tiered by leverage; start at the top.

> How to use: pick from **Tier 1** first — those three shape the moat and the
> scoring model, which is where Scope's defensibility lives ([[Master Plan]]).
> Each entry says *why for Scope* and which feature it informs.

---

## Tier 1 — Shapes the moat & scoring (read these first)

1. **Philip Tetlock & Dan Gardner — _Superforecasting_ (2015).**
   *Why for Scope:* the discipline of scoring your own predictions, calibration,
   Brier scores, and updating. This IS the outcome-tracking moat — turning
   `alert_outcomes` into per-rule *realized* win rates and, eventually, a
   published track record. → informs **Master Plan Phase 1 (calibration)**,
   [[Outcome Tracking Status]], the `win_rate` placeholder in [[Scoring System]].

2. **Marcos López de Prado — _Advances in Financial Machine Learning_ (2018).**
   *Why for Scope:* the rigorous version of what `label_outcomes.py` does.
   Triple-barrier labeling, meta-labeling, sample uniqueness, and — critically —
   **backtest overfitting** (deflated Sharpe, purged/embargoed CV). The guardrail
   against reading noise from small per-rule outcome samples. Read ch. 3
   (labeling), 4 (sample weights), 7 (CV), 11–12 (backtest pitfalls). → informs
   forward-return labeling, the calibration report, `run_backtest.py`.

3. **Richards J. Heuer Jr. — _Psychology of Intelligence Analysis_ (CIA, free PDF).**
   *Why for Scope:* the tradecraft behind RULE_10. Analysis of Competing
   Hypotheses, evidence weighting, and the finding that *more information raises
   confidence faster than accuracy* — the exact reason Scope keeps **Evidence
   Confidence and Opportunity as two independent scores**. → informs the
   corroboration engine and [[Scoring System]].

---

## Tier 2 — Domain: why the edge exists, and how big it really is

4. **Grossman & Stiglitz (1980), _On the Impossibility of Informationally
   Efficient Markets_ (AER).** *Why:* the theoretical basis that information
   gathering earns a return *because* prices aren't perfectly efficient — Scope's
   entire reason to exist. → the North Star / positioning.

5. **Ziobrowski et al. — Senate (2004, JFQA) & House (2011) abnormal-returns
   studies.** *Why:* the empirical literature on congressional trading returns.
   Sets a realistic prior for what RULE_01B / RULE_CLUSTER signals are worth —
   sanity-check calibration against it. → congressional rules, calibration priors.

6. **Wolfers & Zitzewitz (2004), _Prediction Markets_ (J. Econ. Perspectives).**
   *Why:* reading market prices as probabilities, the favorite–longshot bias, and
   liquidity effects — directly how to interpret a RULE_07 Polymarket move. →
   RULE_07.

7. **The STOCK Act & PTR disclosure mechanics** (House Clerk / Senate eFD filing
   rules; primary sources). *Why:* the 30–45 day disclosure lag is the reason
   RULE_CLUSTER windows on *trade proximity* not wall-clock. Know the filing
   rules before touching congressional ingestion. → ingestion, cluster windowing.

---

## Tier 3 — Building it well (the stack & data engineering)

8. **Martin Kleppmann — _Designing Data-Intensive Applications_ (2017).** *Why:*
   immutability, idempotency, event logs, dataflow — the conceptual backbone for
   **immutable detection-time scores**, additive-only migrations, and treating
   `activity_log` as an event log. → architecture, [[RULE Design Decisions]].

9. **SQLite docs + Owens, _The Definitive Guide to SQLite_.** *Why:* the DB is
   SQLite. Understand WAL, the **online-backup API** (used by `db_backup.py`),
   `PRAGMA integrity_check` (the restore drill), and the query planner. →
   database, backups/restore.

10. **FastAPI docs + APScheduler docs.** *Why:* the actual runtime. Dependency
    injection, background tasks, and scheduler **misfire/coalesce** handling
    (relevant to Railway container restarts). → app + scheduler.

---

## Tier 4 — The reasoning layer (the iPhone-8+ frontier)

11. **Judea Pearl & Dana Mackenzie — _The Book of Why_ (2018).** *Why:* causal vs
    correlational reasoning — so corroboration is never oversold as causation, and
    so regime/analogue features are designed on solid ground. → reasoning layer,
    regime recognition ([[Master Plan]] Phase 2–3).

12. **Nate Silver — _The Signal and the Noise_ (2012).** *Why:* accessible
    synthesis of base rates, Bayesian updating, and signal-vs-noise across
    domains. Good intuition for novelty/absorption and calibration. → scoring
    intuition.

13. **Daniel Kahneman — _Thinking, Fast and Slow_** *(or Gary Klein, _Sources of
    Power_).* *Why:* how humans actually judge under uncertainty — informs the
    "excellent analyst who shows evidence chains" persona and the receipts UX. →
    product persona, alert provenance.

---

## Tier 5 — Alt-data & OSINT craft

14. **Denev & Amen — _The Book of Alternative Data_ (2020).** *Why:* sourcing,
    cleaning, and *legally/ethically* backtesting alt-data signals. → OSINT,
    ADS-B, patents, FARA rules.

15. **Michael Bazzell — _Open Source Intelligence Techniques_.** *Why:* practical
    OSINT collection tradecraft. → GDELT / Telegram / ADS-B rules.

---

## Optional / narrative context

- **Michael Lewis — _The Big Short_** (thesis construction) & **_Flash Boys_**
  (microstructure). **Scott Patterson — _Dark Pools_.** Texture, not method.

---

*Living document — add an entry when a reading changes how a part of Scope gets
built, and note which feature it touched. See [[Ideas Backlog]] for ideas these
readings spark.*
