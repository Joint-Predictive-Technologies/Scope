#!/usr/bin/env python3
"""
RULE_10 — Cross-source corroboration.

Fires when **3+ distinct INSTRUMENTS** hit the same ticker inside a **14-day**
window. Noisy sources (Polymarket, OSINT, Reddit, anomaly) are excluded as
corroboration inputs — they're too high-volume and would pair with everything.

This is the gate redesign from 05_Decisions/2026-07-25-gate-redesign.md:

  D1  Count instruments, not rule names. Three views of the congressional feed
      (RULE_01B + RULE_02 + RULE_CLUSTER, all reading `transactions`) are ONE
      instrument, not three. The old gate could be satisfied by a single source
      wearing three rule names, which is why it never represented real
      convergence. The map lives in jpt_common.RULE_10_INSTRUMENTS.
  D2  Threshold 3 instruments (was 4 rules). The instrument count is recorded on
      every corroboration and its theme, so a later 3=candidate / 4=strong tier
      is a labelling change rather than a second gate.
  D4  Window widened 24h -> 14 days, still on INGESTION time (`created_at`).
      The instruments have structurally different disclosure lags — congressional
      PTRs 30-45 days, LDA quarterly, USASpending on award, Form 4 within 2
      business days — so a 24h ingestion window demanded a coincidence rather
      than detecting one.

      FUTURE UPGRADE: event-time windowing is the correct long-term basis and is
      deliberately NOT done here. It is blocked on an `event_date` backfill —
      today that column is populated only for RULE_01B and RULE_11 and is 0 for
      RULE_02, RULE_06, RULE_08, RULE_09 and RULE_CLUSTER, so it cannot yet carry
      the window.

Eligibility is UNCHANGED by this redesign (that is D3, handled separately): the
same rules are excluded as before, only the counting, threshold and window moved.

RULE_10's outcome track RESTARTS under this definition — it is effectively a new
detector, and forward performance must not be pooled with anything the old gate
produced. No historical alert is rewritten or re-scored.

Dedup window is 7 days: once a ticker earns a RULE_10, it won't fire again
for a week even if more signals arrive.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import (RULE_10_EXCLUDED, RULE_10_MIN_INSTRUMENTS, SIGNED_RULES,
                        contract_leg_weight, db_connection, insert_alert,
                        rule10_instruments, score_alert_fields)


def theme_instrument_count(supporting_rules_json: str) -> int:
    """Instrument count for a theme, derived from its stored `supporting_rules`.

    D2 asks for the instrument count to be recorded on the theme. It is derived
    rather than stored in a new column: `themes` has no `instrument_count` field,
    and deriving from the rules already persisted needs **no migration** and
    cannot drift out of sync with the map. A future 3=candidate / 4=strong tier
    reads this.
    """
    try:
        rules = json.loads(supporting_rules_json or "[]")
    except Exception:
        return 0
    return len(rule10_instruments(rules if isinstance(rules, list) else []))


def upsert_theme(conn, ticker, distinct_rules, scores) -> int:
    """Create or evolve a Market Thesis (theme) for this ticker. Returns theme id.

    A corroboration on a ticker that already has an active theme advances its
    lifecycle (Emerging → Developing → Confirmed) and refreshes its scores; a new
    ticker starts an Emerging thesis. (Data hierarchy §3 of the product spec.)

    The title and `what_changed` now name INSTRUMENTS rather than rule names, so
    a thesis opened by three views of one source can no longer read as three
    independent confirmations.
    """
    rules_json = json.dumps(sorted(set(distinct_rules)))
    instruments = rule10_instruments(distinct_rules)
    existing = conn.execute(
        "SELECT id, signal_count FROM themes WHERE primary_ticker = ? "
        "AND status NOT IN ('Resolved','Fading')",
        (ticker,),
    ).fetchone()
    if existing:
        new_count = (existing["signal_count"] or 0) + 1
        status = "Confirmed" if new_count >= 5 else "Developing" if new_count >= 2 else "Emerging"
        conn.execute(
            """UPDATE themes SET
                   signal_count = ?, evidence_confidence = ?, opportunity_score = ?,
                   novelty_score = ?, time_horizon = ?, supporting_rules = ?,
                   what_changed = ?, status = ?, last_updated = datetime('now')
               WHERE id = ?""",
            (new_count, scores["evidence_confidence"], scores["opportunity_score"],
             scores["novelty_score"], scores["time_horizon"], rules_json,
             f"New corroboration: {len(instruments)} instruments "
             f"({', '.join(instruments)})", status,
             existing["id"]),
        )
        return existing["id"]
    cur = conn.execute(
        """INSERT INTO themes (
               title, primary_ticker, affected_tickers, status,
               evidence_confidence, opportunity_score, novelty_score, time_horizon,
               supporting_rules, signal_count, what_changed,
               first_signal_at, last_updated)
           VALUES (?, ?, ?, 'Emerging', ?, ?, ?, ?, ?, 1, ?, datetime('now'), datetime('now'))""",
        (f"Convergence: {ticker} — {len(instruments)} instruments aligned",
         ticker, json.dumps([ticker]),
         scores["evidence_confidence"], scores["opportunity_score"],
         scores["novelty_score"], scores["time_horizon"], rules_json,
         f"Thesis opened from convergence: {len(instruments)} instruments "
         f"({', '.join(instruments)})"),
    )
    return cur.lastrowid

RULE = "RULE_10"

# Rules that may not act as a corroboration source: too noisy / too volume-heavy
# (Polymarket has 578+ alerts, OSINT hundreds — they would pair with everything),
# self-referential (RULE_10), or RETIRED (RULE_12/13/14).
#
# DERIVED from jpt_common.RULE_10_EXCLUDED — ONE source of truth, deliberately.
#
# This was a second, hand-maintained set, and it had silently DIVERGED: RULE_12/13/14
# were retired into RULE_10_EXCLUDED, which stopped them counting as instruments, but
# this set still admitted them as SQL candidates. So a retired rule could not OPEN a
# corroboration yet still landed in `theme_signals` and inflated the corroboration's
# evidence_confidence. (That inflation was measured when this call passed rule NAMES;
# it now passes INSTRUMENTS, but a retired rule leaking in is still a defect.)
# Measured on identical 3-instrument fires: 6.0 with live rules only, 81.0 once
# RULE_12/13/14 were present — a 13x inflation from rules that are supposedly retired.
#
# The two sets serve different mechanisms (this one is the SQL candidate filter,
# RULE_10_EXCLUDED drives rule10_eligible_rules/rule10_instruments) but they answer the
# same question: "may this rule participate in corroboration at all?" One answer.
# tests/test_exclusion_single_source.py fails if they are ever made to disagree.
EXCLUDED_FROM_CORROBORATION = set(RULE_10_EXCLUDED)

DEDUP_WINDOW_DAYS = 7

# D4 — co-occurrence window, on INGESTION time (`created_at`). See the module
# docstring for why event-time is the deferred upgrade rather than the basis here.
CONVERGENCE_WINDOW_DAYS = 14

# D2 — the firing threshold, in distinct INSTRUMENTS. Imported rather than
# redefined so the gate and jpt_common.rule10_is_valid (which the brief and the
# evidence API use to decide whether a corroboration may be cited) can never
# disagree about what "corroborated" means.
MIN_DISTINCT_INSTRUMENTS = RULE_10_MIN_INSTRUMENTS


def _candidate_alerts(conn, window_hours: int) -> list:
    """Gate candidates: non-empty ticker, eligible rule, severity floor, inside the window.

    ⚠️ THE SELECT LIST WAS WIDENED, AND THE WHERE CLAUSE DELIBERATELY WAS NOT. `tags`,
    `corroborates` and `award_key` are now loaded because the gate could not previously SEE
    what a leg said — it selected ticker/rule/severity/created_at and nothing else, so
    RULE_06's direction, which RULE_06 computes and persists, was discarded. That is how
    the RTX exercise-and-sell counted as a bullish insider leg.

    A widening is safe; a filter here would not be. Every consumer indexes these rows by
    name, and the per-alert decision belongs in `instruments_for` — the one choke point
    that reaches the fire decision, the emitted tags, the severity tier and
    `check_convergence` all at once. Filtering in the SQL would leave the three OTHER
    copies of this predicate (`api/routers/forming.py`, `scripts/morning_brief.py`,
    `api/static/ticker.html`) silently disagreeing with the gate.
    """
    excluded = ",".join(f"'{r}'" for r in EXCLUDED_FROM_CORROBORATION)
    return conn.execute(
        f"""
        SELECT id, ticker, rule, severity, headline, created_at,
               tags, corroborates, corroboration_note, award_key
        FROM alerts
        WHERE ticker IS NOT NULL AND ticker != ''
          AND rule NOT IN ({excluded})
          AND severity IN ('HIGH', 'CRITICAL')
          AND created_at >= datetime('now', '-{int(window_hours)} hours')
        ORDER BY created_at DESC
        """
    ).fetchall()


def _already_corroborated(conn, ticker: str) -> bool:
    row = conn.execute(
        """
        SELECT id FROM alerts
        WHERE ticker = ?
          AND rule = 'RULE_10'
          AND created_at >= datetime('now', ? || ' days')
        LIMIT 1
        """,
        (ticker, f"-{DEDUP_WINDOW_DAYS}"),
    ).fetchone()
    return row is not None


def alert_corroborates(alert) -> tuple[bool, str]:
    """Does THIS ALERT corroborate, or is it merely present? Returns (verdict, reason).

    ⚠️ THE SECOND QUESTION THE GATE NOW ASKS. Rule-name eligibility (`RULE_10_EXCLUDED`)
    answers "can this KIND of signal ever corroborate". This answers "does this PARTICULAR
    signal actually say the thing we are counting it as saying". Without it, an insider
    SELL corroborated a bullish thesis exactly as well as a buy — which is how RTX fired
    at exactly 3 instruments on an exercise-and-sell.

    ⚠️ THE BLAST RADIUS IS `SIGNED_RULES` AND IT IS TINY BY DESIGN. Only those rules are
    interrogated. Every other rule returns True unconditionally, so the congressional,
    earnings, 13F and senate-lda legs behave EXACTLY as before — that is what makes the
    "untouched instruments are unchanged" claim provable rather than hopeful.

    ⚠️ FAILS CLOSED, and it has to. A signed rule whose verdict is NULL — every alert
    written before this shipped, and any alert whose filing could not be re-read — does
    NOT corroborate. Falling back to RULE_06's stored `sale`/`purchase` tag would be worse
    than useless: that tag comes from `majority_action`, which only ever saw codes P and S,
    so it reads the RTX exercise-and-sell as a plain "sale" and would read an
    exercise-and-HOLD as a purchase. The disclosed cost is that historical insider legs go
    dark until re-parsed. Absence of evidence is not evidence of a buy.
    """
    rule = (_row_value(alert, "rule") or "").strip().upper()
    if rule not in SIGNED_RULES:
        return True, ""
    verdict = _row_value(alert, "corroborates")
    if verdict is None:
        return False, "no direction on record (pre-signing alert, or filing not re-read)"
    if int(verdict) == 1:
        return True, ""
    return False, (_row_value(alert, "corroboration_note")
                   or "the rule recorded this as non-corroborating")


def _row_value(row, key, default=None):
    """Read a column that may not be in the projection at all.

    `sqlite3.Row` has no `.get()` and raises IndexError on an unknown key, while callers
    also pass plain dicts (`instruments_for([{"rule": r} for r in ...])` is used by tests
    and diagnostics). This tolerates a MISSING COLUMN without ever softening the verdict:
    an absent `corroborates` reads as None, which `alert_corroborates` fails closed on.
    """
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return default


def instruments_for(alerts) -> list[str]:
    """Distinct instruments represented by a group of alerts.

    The single most important line in this file: rules that read the same source
    collapse to one entry, so the congressional trio cannot satisfy the gate by
    itself.

    ⚠️ NOW ALSO DROPS ALERTS THAT DO NOT CORROBORATE, and it is modified IN PLACE rather
    than wrapped: `tests/test_check_convergence.py` asserts `cc.instruments_for is
    r10.instruments_for` by OBJECT IDENTITY, and this is the one choke point every
    consumer of the gate's decision reaches by import — `find_corroborated_tickers`, the
    ranking, the emitter, and `check_convergence`. Putting the per-alert filter anywhere
    else would let the fire decision and the recorded instrument count disagree.
    """
    return rule10_instruments({a["rule"] for a in alerts
                               if alert_corroborates(a)[0]})


def _leg_weights(conn, alerts) -> dict[str, float]:
    """Per-rule weight for the corroborating legs. 1.0 means "no opinion".

    Computed HERE, at gate time, rather than in RULE_11's emit loop: only a converging
    ticker needs a market cap, so the lookups are bounded by the handful of tickers that
    actually reach the gate instead of every award in a 25-page sweep against a cold
    cache. It also leaves RULE_11's emission untouched.

    Only the contracts leg has a weight today. Every other rule is absent from the dict,
    which readers treat as 1.0 — so this cannot quietly change an untouched instrument.
    """
    weights: dict[str, float] = {}
    for a in alerts:
        if (_row_value(a, "rule") or "").strip().upper() != "RULE_11":
            continue
        w = contract_leg_weight(conn, _row_value(a, "ticker"),
                                _row_value(a, "award_key"))
        # Several awards can back one contracts leg; the most conservative wins, so a
        # single routine mega-cap award cannot be outvoted into looking surprising.
        #
        # ⚠️ SEEDED FROM `w`, NOT FROM A 1.0 DEFAULT, AND THAT IS THE WHOLE BUG THIS LINE
        # ONCE HAD. `min(weights.get("RULE_11", 1.0), w)` looks like the same thing and is
        # not: on the FIRST award the default 1.0 is one of the operands, so any weight
        # above neutral was clamped straight back to 1.0. `CONTRACT_WEIGHT_MATERIAL` could
        # therefore never be written, and since this function is the only writer of
        # `tags.leg_weights`, the entire above-neutral path — and with it the whole point of
        # gating a boost on `contractor_attribution_is_exact` — was dead in the product
        # while passing every unit test. A curated 18%-of-cap award and the token-matched
        # SPCX false positive emitted byte-identical output. Found by a verification pass;
        # pinned now by `test_the_emitter_WRITES_the_weight_it_computed`.
        weights["RULE_11"] = w if "RULE_11" not in weights else min(weights["RULE_11"], w)
    return weights


def non_corroborating(alerts) -> list[dict]:
    """The legs that were PRESENT but did not corroborate, with the reason.

    Recorded on the emitted alert so a dropped leg is visible rather than merely absent —
    the gate must not quietly hide that an insider sold.
    """
    out = []
    for a in alerts:
        ok, why = alert_corroborates(a)
        if not ok:
            out.append({"rule": _row_value(a, "rule"), "reason": why})
    return out


SIGNED_RULE_DARK_SOURCE = "MONITOR_SIGNED_RULE_DARK"


def _detect_dark_signed_rules(conn, window_hours: int) -> list[str]:
    """⚠️ OBSERVABILITY ONLY. Writes `activity_log`; the gate reads nothing back.

    A SIGNED rule contributes nothing to corroboration when its `corroborates` column is
    unpopulated — `alert_corroborates` fails closed on NULL, correctly, and silently. The
    signing session's own guard could not detect this: it asserted synthetic dicts don't
    corroborate, which is true whether or not the population path has ever run on a real
    database.

    The hazard is the BACKLOG, not the emit path. Both signed rules write `corroborates` at
    emit time (`rule_01b_first_touch.py:438-444`, `rule_06_form4.py:552`), so new alerts are
    populated and a dark rule self-heals as they land. What goes dark is a corpus written
    BEFORE the rule was signed: RULE_01B's is repaired by `remap_rule01b_direction.py`,
    which is prepared-not-run, and shipping that signing first takes it from 26 corroborating
    legs to 0 with no outward sign. An earlier version of this docstring said the verdicts
    "come from a remap", which is wrong — the remap only covers the backlog. Found by a
    verification pass.

    Two behaviours worth knowing rather than discovering: this writes one row PER RUN, so a
    persistently dark rule produces ~24 rows/day (correct for an activity log, noisy for a
    dashboard); and it does not consult `dry_run`, matching the pre-existing `RULE_10`
    activity row that is also written under `--dry-run`.

    ⚠️ IT KEYS ON `corroborates IS NULL`, NEVER ON THE CORROBORATION BOOLEAN, AND THAT
    DISTINCTION IS THE WHOLE POINT. NULL means "no verdict on record" — the population path
    did not run. A verdict of 0 means "we looked, and this leg does not corroborate". A
    signed rule whose in-window candidates happen to be ALL SALES has every verdict 0 and is
    perfectly HEALTHY; alarming on that would fire loudest exactly when the signing is doing
    its job. Only all-NULL is dark.

    Fires at 100%, not a high ratio: a partially-populated rule is backfilling or rolling
    over, which is a different (and quieter) signal. Zero in-window candidates is silent —
    there is nothing to be NULL.
    """
    from jpt_common import log_activity

    rows = _candidate_alerts(conn, window_hours)
    dark: list[str] = []

    for rule in sorted(SIGNED_RULES):
        candidates = [r for r in rows if (_row_value(r, "rule") or "").strip().upper() == rule]
        if not candidates:
            continue                       # nothing to be NULL
        nulls = [r for r in candidates if _row_value(r, "corroborates") is None]
        if len(nulls) != len(candidates):
            continue                       # any populated verdict means the path is running

        # The cause discriminator. Both shapes look identical in-window and need different
        # responses, so say which one this is.
        #
        # ⚠️ THESE LABELS NAME WHAT IS MEASURED, NOT WHAT IS INFERRED, DELIBERATELY. An
        # earlier draft called them `population_path_never_ran` / `backlog_predates_signing`,
        # which asserts a cause the query cannot see. "No verdict anywhere" is consistent
        # with the population path being broken AND with the rule simply not having emitted
        # since it was signed — RULE_06 on the local snapshot is the latter, and calling
        # that "never ran" would read as an accusation against working code.
        #   no_verdict_anywhere_in_db            -> nothing to roll over; investigate
        #   verdicts_exist_outside_candidate_set -> these rows predate population; self-heals
        #
        # "outside the CANDIDATE SET", not "outside the window": a populated verdict can sit
        # on a row that is in-window but below the severity floor, and calling that "outside
        # the window" would be false. Both edges were found by a verification pass.
        # `UPPER(TRIM())` matches how candidates are normalised above — a case-variant stored
        # rule name with real verdicts was otherwise mislabelled as having none.
        ever = conn.execute(
            "SELECT 1 FROM alerts WHERE UPPER(TRIM(rule))=? "
            "  AND corroborates IS NOT NULL LIMIT 1",
            (rule,),
        ).fetchone() is not None
        cause = ("verdicts_exist_outside_candidate_set" if ever
                 else "no_verdict_anywhere_in_db")

        note = (f"CRITICAL:signed_rule_dark rule={rule} "
                f"candidates={len(candidates)} null={len(nulls)} "
                f"window_hours={window_hours} cause={cause}")
        log_activity(conn, SIGNED_RULE_DARK_SOURCE, scanned=len(candidates),
                     flagged=len(candidates), emitted=0, notes=note)
        print(f"[RULE_10] ⚠️  CRITICAL: {rule} is SIGNED but every one of its "
              f"{len(candidates)} in-window gate candidates has no directional verdict "
              f"on record. It is contributing ZERO corroborating legs. cause={cause}")
        dark.append(rule)

    return dark


def find_corroborated_tickers(conn, window_hours: int) -> dict[str, list]:
    rows = _candidate_alerts(conn, window_hours)

    ticker_alerts: dict[str, list] = defaultdict(list)
    for row in rows:
        ticker_alerts[row["ticker"]].append(row)

    # Require 3+ distinct INSTRUMENTS (not rule names) AND no RULE_10 in 7 days.
    return {
        ticker: alerts
        for ticker, alerts in ticker_alerts.items()
        if len(instruments_for(alerts)) >= MIN_DISTINCT_INSTRUMENTS
        and not _already_corroborated(conn, ticker)
    }


def _build_narrative(ticker: str, alerts: list, rules_fired: str, window_hours: int = 24) -> str:
    headlines = " | ".join(a["headline"] for a in alerts[:6])
    fallback = (
        f"Signals from {rules_fired} converged on {ticker} within {window_hours}h. "
        "See individual rule alerts for details."
    )
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return fallback
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        prompt = f"""You are a political intelligence analyst for macro investors.

The following signals have fired on ticker {ticker} within the past 48 hours:

{chr(10).join(f"  - {a['headline']}" for a in alerts[:6])}

Rules triggered: {rules_fired}

In 2-3 sentences, explain why this convergence of signals is notable for an investor watching {ticker}. Be specific. Do not say "you should buy" or give investment advice. Describe what the signals collectively suggest about political/regulatory/insider activity around this stock."""
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:
        print(f"  [warn] LLM call failed for {ticker}: {exc}", file=sys.stderr)
        return fallback


MAX_PER_RUN = 10  # hard cap — prevents any future flood


def run(dry_run: bool, window_hours: int | None = None) -> tuple[int, int]:
    """`window_hours=None` means the D4 default of CONVERGENCE_WINDOW_DAYS."""
    if window_hours is None:
        window_hours = CONVERGENCE_WINDOW_DAYS * 24
    import time as _time
    from jpt_common import record_activity
    _t0 = _time.time()
    load_dotenv()
    conn = db_connection()

    # ⚠️ BEFORE the `not clusters` early return below, deliberately. A signed rule going
    # dark is precisely a rule that stops completing convergences, so `clusters` is empty in
    # exactly the case this alarm exists for — placing it near the normal exit would make it
    # silent whenever it matters most. Observability only; the return value is unused.
    _detect_dark_signed_rules(conn, window_hours)

    clusters = find_corroborated_tickers(conn, window_hours)
    found = len(clusters)
    emitted = 0

    if not clusters:
        print("  No corroboration clusters found.")
        conn.close()
        record_activity("RULE_10", scanned=0, flagged=0, emitted=0,
                        duration_seconds=round(_time.time() - _t0, 2))
        return 0, 0

    # Sort by distinct INSTRUMENT count desc (most corroborated first).
    ranked = sorted(
        clusters.items(),
        key=lambda kv: len(instruments_for(kv[1])),
        reverse=True,
    )[:MAX_PER_RUN]
    if len(clusters) > MAX_PER_RUN:
        print(f"  [{len(clusters)} qualified — capped at {MAX_PER_RUN} per run]")

    for ticker, alerts in ranked:
        # ⚠️ CORROBORATING vs MERELY PRESENT — the distinction this whole change adds.
        # `corroborating` is what the count, the tags and the confidence are built from;
        # `present` is kept only for provenance, so a dropped leg is VISIBLE rather than
        # silently absent.
        corroborating = [a for a in alerts if alert_corroborates(a)[0]]
        dropped = non_corroborating(alerts)
        rules_present = sorted({a["rule"] for a in alerts})
        rules_fired = ",".join(sorted({a["rule"] for a in corroborating}))
        rule_count = len({a["rule"] for a in corroborating})
        instruments = instruments_for(alerts)
        instrument_count = len(instruments)
        severities = {a["severity"] for a in corroborating} or {a["severity"] for a in alerts}
        leg_weights = _leg_weights(conn, corroborating)

        print(f"  [{instrument_count} instruments / {rule_count} rules] {ticker}  "
              f"instruments={','.join(instruments)}  rules={rules_fired}")
        for d in dropped:
            print(f"    dropped {d['rule']}: {d['reason']}")
        for rule, w in sorted(leg_weights.items()):
            if w != 1.0:
                print(f"    weight  {rule}: {w:.2f}")

        if dry_run:
            continue

        narrative = _build_narrative(ticker, alerts, rules_fired, window_hours)
        print(f"    narrative: {narrative[:120]}")

        # 4+ instruments is the "strong" end of the gradient D2 leaves available;
        # 3 is a candidate convergence. Kept as severity for now — the explicit
        # candidate/strong tier is a surfacing change, deliberately not built here.
        severity = (
            "CRITICAL"
            if instrument_count > MIN_DISTINCT_INSTRUMENTS or "CRITICAL" in severities
            else "HIGH"
        )
        headline = (
            f"[CORROBORATION] {ticker}: {instrument_count} independent instruments "
            f"in {window_hours // 24}d ({','.join(instruments)})"
        )
        # ⚠️ `rules` IS NOW THE CORROBORATING SET, AND THAT CHOICE IS LOAD-BEARING.
        # Five consumers re-derive an instrument count from this stored list of rule NAMES
        # rather than from live rows — `jpt_common._distinct_rule_count` (which feeds
        # `evidence_confidence`), `api/routers/evidence.py`, `theme_instrument_count`,
        # `api/receipts.py` and `scripts/generate_brief.py`. If `rules` still held every
        # PRESENT rule, each of them would re-derive the pre-filter count and a re-score
        # would silently re-inflate confidence to include the insider sell the gate just
        # rejected. Making this the filtered set means all five agree with the gate for
        # free, with no second predicate to keep in step.
        #
        # `rules_present` and `non_corroborating` carry the provenance that would otherwise
        # be lost: we must not hide that a leg was there and was rejected.
        distinct_rules = sorted({a["rule"] for a in corroborating})
        tags = json.dumps({
            "rules": distinct_rules,
            "rule_count": rule_count,
            "rules_fired": rules_fired,
            # D2 — the count the gate actually used, recorded so the later
            # candidate/strong tier needs no recomputation and no schema change.
            "instruments": instruments,
            "instrument_count": instrument_count,
            "rules_present": rules_present,
            "non_corroborating": dropped,
            # Per-leg weights, FROZEN at detection time. Detection-time scores are
            # immutable in this project, and a cap ratio recomputed later against a
            # different price would silently rewrite history.
            "leg_weights": leg_weights,
        })

        # Insert via the scoring wrapper so the corroboration carries real
        # evidence/opportunity/novelty, and capture its id for theme linking.
        alert_id = insert_alert(
            conn, rule=RULE, ticker=ticker, severity=severity,
            headline=headline, detail=narrative, tags=tags,
            # INSTRUMENTS, not rule names. `rule_count` is still recorded in tags for
            # provenance, but confidence must reflect how many INDEPENDENT things
            # corroborate — the same question the gate answers when it fires.
            distinct_rule_count=instrument_count,
        )
        conn.execute(
            """UPDATE alerts SET lifecycle_stage = 'corroborated'
               WHERE ticker = ? AND rule != 'RULE_10'
                 AND (lifecycle_stage IS NULL OR lifecycle_stage = 'created')
                 AND datetime(created_at) >= datetime('now', '-48 hours')""",
            (ticker,),
        )

        # Feature 4 — create/evolve the Market Thesis and link the evidence.
        scores = score_alert_fields(conn, RULE, ticker, headline, tags)
        theme_id = upsert_theme(conn, ticker, distinct_rules, scores)
        conn.execute("UPDATE alerts SET theme_id = ? WHERE id = ?", (theme_id, alert_id))
        # Link this corroboration + its contributing signals to the theme.
        #
        # ⚠️ `corroborating`, NOT `alerts`. This linked every PRESENT alert, so a theme
        # whose summary read "3 independent signals converged" listed FOUR items in its
        # receipt — the fourth being the insider sell the gate had just rejected, shown
        # unlabelled alongside the legs that actually counted. Measured by a verification
        # pass. `themes.supporting_rules` was already the corroborating set, so the receipt
        # list was the only place that disagreed with the count printed above it.
        #
        # The rejected leg is NOT thereby hidden: it is recorded on the corroboration itself
        # in `tags.non_corroborating`, with the reason, which is where a reader can see that
        # an insider filed and what they did. `theme_signals` is the EVIDENCE list, and a
        # sell is not evidence for a bullish thesis.
        conn.execute("INSERT INTO theme_signals (theme_id, alert_id) VALUES (?, ?)",
                     (theme_id, alert_id))
        for a in corroborating:
            if a["id"] is not None:
                conn.execute(
                    "INSERT INTO theme_signals (theme_id, alert_id) VALUES (?, ?)",
                    (theme_id, a["id"]),
                )
        conn.commit()
        emitted += 1

    conn.close()
    record_activity("RULE_10", scanned=found, flagged=found, emitted=emitted,
                    duration_seconds=round(_time.time() - _t0, 2))
    return found, emitted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Cross-source corroboration: fire when "
                    f"{RULE_10_MIN_INSTRUMENTS}+ distinct INSTRUMENTS hit the same "
                    f"ticker within {CONVERGENCE_WINDOW_DAYS} days (RULE_10)."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print clusters without writing to DB or calling LLM.")
    parser.add_argument("--window-days", type=int, default=CONVERGENCE_WINDOW_DAYS,
                        help=f"Lookback window in days (default: {CONVERGENCE_WINDOW_DAYS}).")
    # Retained for backward compatibility and for ad-hoc narrowing; when given it
    # overrides --window-days. The scheduler passes neither, so the D4 default applies.
    parser.add_argument("--window-hours", type=int, default=None,
                        help="Lookback window in hours; overrides --window-days if set.")
    # Accepted (and ignored) for scheduler-runner uniformity — the scheduler
    # invokes every job with --emit-alerts; without this, argparse would reject it
    # (exit 2) and RULE_10 would fail on every scheduled run.
    parser.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.dry_run:
        print("Dry run — no DB writes or LLM calls.")
    window_hours = args.window_hours if args.window_hours is not None else args.window_days * 24
    print(f"Scanning for corroboration clusters "
          f"({window_hours // 24}d window, {RULE_10_MIN_INSTRUMENTS}+ instruments) …")
    found, emitted = run(args.dry_run, window_hours)
    print(f"\n{found} cluster(s) found, {emitted} RULE_10 alert(s) emitted")


if __name__ == "__main__":
    main()
