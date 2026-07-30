"""
Forming convergences — the near-miss surface.

Tickers with **at least 2 but fewer than the threshold** distinct instruments
inside the convergence window — short of what RULE_10 requires to fire. They are
watch items, not signals, and the surface says so.

Why this exists: RULE_10 has never fired. The gate was redesigned to count
instruments rather than rule names (3 instruments / 14 days), and it will start
firing as RULE_06 accumulates insider alerts. Until then an operator has nothing
to look at, and "nothing" is indistinguishable from "broken". This shows the
pipeline working — patterns forming — without claiming any of them are confirmed.

**Everything here is derived from the gate's own logic by import.** The instrument
map, the exclusion set, the threshold and the window all come from the modules that
actually decide what fires. Re-implementing any of them would let this surface
drift from reality — which is exactly the class of bug that let RULE_01 count as a
second congressional instrument. If the gate changes, this changes with it.

Read-only: this module issues a single SELECT and writes nothing.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from jpt_common import (RULE_10_EXCLUDED, RULE_10_INSTRUMENTS,
                        RULE_10_MIN_INSTRUMENTS, db_connection, rule10_instruments)
# The window belongs to the gate, not to this surface — import it rather than
# restating "14", so a change there moves this too.
from scripts.rule_10_corroboration import CONVERGENCE_WINDOW_DAYS, DEDUP_WINDOW_DAYS
# ⚠️ THE MODULE, NOT THE FUNCTIONS, AND THE DIFFERENCE IS LOAD-BEARING. The gate's per-alert
# decision is resolved at CALL time via `_gate.instruments_for(...)`. Binding the functions
# at import (`from ... import instruments_for`) looks equivalent and is not: anything that
# reloads the gate module — `tests/test_exclusion_single_source.py` does exactly this to
# prove the exclusion set cannot be re-hardcoded — replaces those function objects, leaving
# this surface holding the OLD ones and silently counting by the old rule. Measured: the
# import form passed in isolation and failed in the full suite for precisely that reason.
from scripts import rule_10_corroboration as _gate

router = APIRouter()

# A near-miss is anything with enough instruments to be interesting but not enough
# to fire: FLOOR <= n < threshold.
#
# The upper bound is derived so a threshold change can never leave this surface
# mislabelling a fired convergence as "forming". The floor is a genuine constant —
# two independent instruments is the smallest pattern worth watching; one is just
# an alert.
#
# It is a RANGE, not `== threshold - 1`, deliberately. Today they coincide (3 - 1
# = 2). If the threshold ever rose to 4, exact-equality would show only
# 3-instrument tickers and silently drop the 2-instrument ones — a latent bug
# that would appear as "forming convergences quietly stopped listing things".
NEAR_MISS_FLOOR_INSTRUMENTS = 2

# Every instrument the gate knows about — used to name what a ticker is missing.
ALL_INSTRUMENTS = sorted(set(RULE_10_INSTRUMENTS.values()))


def _candidate_rows(conn, window_days: int):
    """Same eligibility as the gate: eligible rules, HIGH/CRITICAL, in-window.

    Mirrors rule_10_corroboration._candidate_alerts. Kept as its own query
    because that one is private to the rule and returns rows shaped for emission,
    but the WHERE clause must stay identical — a near-miss computed over a
    different candidate set would not be one leg short of anything.

    ⚠️ THE SELECT LIST MUST ALSO MATCH, not just the WHERE clause. The gate now decides
    per ALERT as well as per rule (an insider leg counts only on a genuine open-market
    buy), and it reads `corroborates` to do it. Without these columns this surface would
    call a ticker "2 of 3" where the gate sees 1 — or worse, list a ticker as forming
    while the gate has already fired it.
    """
    excluded = ",".join(f"'{r}'" for r in sorted(RULE_10_EXCLUDED))
    return conn.execute(
        f"""
        SELECT id, ticker, rule, severity, headline, created_at,
               tags, corroborates, corroboration_note, award_key
        FROM alerts
        WHERE ticker IS NOT NULL AND ticker != ''
          AND rule NOT IN ({excluded})
          AND severity IN ('HIGH', 'CRITICAL')
          AND created_at >= datetime('now', '-{int(window_days)} days')
        ORDER BY created_at DESC
        """
    ).fetchall()


def _recently_corroborated(conn) -> set[str]:
    """Tickers that have already FIRED a corroboration recently.

    Without this a ticker that fired RULE_10 re-enters this list as "forming"
    the moment one of its legs ages out of the window — so the same ticker would
    appear as a confirmed convergence in one place and a forming one in another,
    with two different meanings. The gate guards its own re-firing with
    `_already_corroborated` over DEDUP_WINDOW_DAYS; this mirrors it, importing the
    same constant so the two cannot diverge.
    """
    rows = conn.execute(
        """SELECT DISTINCT ticker FROM alerts
           WHERE rule = 'RULE_10' AND ticker IS NOT NULL AND ticker != ''
             AND created_at >= datetime('now', ? || ' days')""",
        (f"-{DEDUP_WINDOW_DAYS}",),
    ).fetchall()
    return {r["ticker"] for r in rows}


def find_near_misses(conn, window_days: int | None = None) -> list[dict]:
    """Tickers in the near-miss band: FLOOR <= instruments < the gate threshold.

    A ticker at or above the threshold is a *convergence*, not a near-miss, and is
    deliberately excluded — showing one here would present a fired signal as a
    forming one. A ticker at 1 instrument is not yet interesting.

    Newest-leg-first, so the list reads as "what moved most recently".
    """
    window_days = window_days if window_days is not None else CONVERGENCE_WINDOW_DAYS
    rows = _candidate_rows(conn, window_days)

    by_ticker: dict[str, list] = {}
    for row in rows:
        by_ticker.setdefault(row["ticker"], []).append(row)

    already_fired = _recently_corroborated(conn)

    out: list[dict] = []
    for ticker, alerts in by_ticker.items():
        if ticker in already_fired:
            continue                      # it fired; it is not "forming"
        # THE GATE'S OWN FUNCTION, imported — not a reimplementation of its body. This
        # line used to read `rule10_instruments({a["rule"] for a in alerts})`, which was
        # `instruments_for`'s body copied by hand. It stayed correct only while the gate
        # counted nothing but rule names; the moment the gate began deciding per alert,
        # a copy would have started disagreeing silently.
        instruments = _gate.instruments_for(alerts)
        if not (NEAR_MISS_FLOOR_INSTRUMENTS
                <= len(instruments) < RULE_10_MIN_INSTRUMENTS):
            continue

        # Only the alerts belonging to the instruments that count — an excluded
        # rule contributes no instrument and must not appear as evidence either,
        # and neither does a leg the gate rejected (an insider sell).
        contributing = [
            a for a in alerts
            if rule10_instruments({a["rule"]}) and _gate.alert_corroborates(a)[0]
        ]
        contributing.sort(key=lambda a: a["created_at"], reverse=True)

        out.append({
            "ticker": ticker,
            "instruments": instruments,
            "instrument_count": len(instruments),
            "needed": RULE_10_MIN_INSTRUMENTS,
            # NOTE: still every instrument in the map, including those no eligible rule
            # can supply (fec, patents). That is a pre-existing defect flagged by the
            # RULE_08 session and left alone here deliberately — it is a surfacing
            # decision, and `tests/test_near_miss_surface.py` currently pins the present
            # behaviour. Nothing in this change alters which instruments are reachable.
            "missing_legs": [i for i in ALL_INSTRUMENTS if i not in instruments],
            # The legs that ARE present but do not corroborate — so the surface can say
            # "an insider filed, but they sold" rather than silently showing nothing.
            "non_corroborating": _gate.non_corroborating(alerts),
            "latest_at": contributing[0]["created_at"] if contributing else None,
            "alerts": [
                {"id": a["id"], "rule": a["rule"], "severity": a["severity"],
                 "headline": a["headline"], "created_at": a["created_at"]}
                for a in contributing
            ],
        })

    out.sort(key=lambda d: (d["latest_at"] or ""), reverse=True)
    return out


@router.get("")
def list_forming(window_days: int = Query(default=None, ge=1, le=90)):
    """Forming convergences — NOT confirmed signals.

    `status` is deliberately literal so no caller can render these as fired.
    """
    conn = db_connection()
    try:
        near = find_near_misses(conn, window_days)
    finally:
        conn.close()

    return {
        "status": "forming",
        "label": (f"forming convergence — fewer than {RULE_10_MIN_INSTRUMENTS} "
                  f"instruments, not a confirmed signal"),
        "window_days": window_days or CONVERGENCE_WINDOW_DAYS,
        "threshold": RULE_10_MIN_INSTRUMENTS,
        "count": len(near),
        "forming": near,
    }


@router.get("/count")
def forming_count():
    """Just the number, for a discoverable badge elsewhere."""
    conn = db_connection()
    try:
        return {"count": len(find_near_misses(conn))}
    finally:
        conn.close()
