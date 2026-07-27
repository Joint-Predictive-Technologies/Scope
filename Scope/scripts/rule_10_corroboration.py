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
from jpt_common import (RULE_10_EXCLUDED, RULE_10_MIN_INSTRUMENTS, db_connection,
                        insert_alert, rule10_instruments, score_alert_fields)


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
# evidence_confidence, because :311 passes distinct_rule_count = rule *names*.
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
    excluded = ",".join(f"'{r}'" for r in EXCLUDED_FROM_CORROBORATION)
    return conn.execute(
        f"""
        SELECT id, ticker, rule, severity, headline, created_at
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


def instruments_for(alerts) -> list[str]:
    """Distinct instruments represented by a group of alerts.

    The single most important line in this file: rules that read the same source
    collapse to one entry, so the congressional trio cannot satisfy the gate by
    itself.
    """
    return rule10_instruments({a["rule"] for a in alerts})


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
        rules_fired = ",".join(sorted({a["rule"] for a in alerts}))
        rule_count = len({a["rule"] for a in alerts})
        instruments = instruments_for(alerts)
        instrument_count = len(instruments)
        severities = {a["severity"] for a in alerts}

        print(f"  [{instrument_count} instruments / {rule_count} rules] {ticker}  "
              f"instruments={','.join(instruments)}  rules={rules_fired}")

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
        distinct_rules = sorted({a["rule"] for a in alerts})
        tags = json.dumps({
            "rules": distinct_rules,
            "rule_count": rule_count,
            "rules_fired": rules_fired,
            # D2 — the count the gate actually used, recorded so the later
            # candidate/strong tier needs no recomputation and no schema change.
            "instruments": instruments,
            "instrument_count": instrument_count,
        })

        # Insert via the scoring wrapper so the corroboration carries real
        # evidence/opportunity/novelty, and capture its id for theme linking.
        alert_id = insert_alert(
            conn, rule=RULE, ticker=ticker, severity=severity,
            headline=headline, detail=narrative, tags=tags,
            distinct_rule_count=rule_count,
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
        conn.execute("INSERT INTO theme_signals (theme_id, alert_id) VALUES (?, ?)",
                     (theme_id, alert_id))
        for a in alerts:
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
