"""`check_convergence.py` had no tests. It is read-only, so nothing it does can corrupt
data — but it is a DIAGNOSTIC, and a diagnostic that misreports the gate is worse than
no diagnostic: it sends a human looking for a bug that isn't there, or reassures them
about one that is.

Two things are pinned here.

1. THE DEDUP. Counting instruments is only half of what RULE_10 does — a ticker already
   corroborated inside DEDUP_WINDOW_DAYS emits nothing. The script used to print
   "*** FIRE" for those, i.e. claim the gate should have fired when the gate was
   correctly silent.

2. THE COUPLING. Every decision the script makes is the GATE's, by import — including
   which alerts are candidates at all. It used to keep its own SELECT that merely
   resembled `_candidate_alerts`; that is asserted gone, by identity of the function
   object and by the absence of any `FROM alerts` literal in the module.

3. THAT IT ONLY EVER READS.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection, RULE_10_MIN_INSTRUMENTS  # noqa: E402
from scripts import check_convergence as cc  # noqa: E402
from scripts import rule_10_corroboration as r10  # noqa: E402

TRIO_PLUS = ["RULE_01B", "RULE_11", "RULE_06"]      # 3 DISTINCT instruments


def _seed_three_instruments(conn, ticker):
    for rule in TRIO_PLUS:
        conn.execute(
            "INSERT INTO alerts (rule, ticker, severity, headline, created_at) VALUES "
            "(?, ?, 'HIGH', 'x', datetime('now','-1 days'))", (rule, ticker))
    conn.commit()


# ── the dedup, which is what the script was getting wrong ────────────────────

def test_live_mode_reports_a_genuine_fire():
    conn = db_connection()
    _seed_three_instruments(conn, "FIREME")
    rows = {r[0]: r for r in cc.scan(conn, 14, live_only=True)}
    conn.close()
    ticker, n, insts, _when, suppressed = rows["FIREME"]
    assert n >= RULE_10_MIN_INSTRUMENTS and not suppressed


def test_live_mode_marks_a_dedup_suppressed_ticker_instead_of_calling_it_a_fire():
    """The whole point. Same three instruments, but already corroborated 2 days ago."""
    conn = db_connection()
    _seed_three_instruments(conn, "DEDUPME")
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, created_at) VALUES "
        "('RULE_10','DEDUPME','HIGH','[CORROBORATION] prior', datetime('now','-2 days'))")
    conn.commit()

    rows = {r[0]: r for r in cc.scan(conn, 14, live_only=True)}
    conn.close()
    _t, n, _i, _w, suppressed = rows["DEDUPME"]
    assert n >= RULE_10_MIN_INSTRUMENTS, "fixture must still reach the threshold"
    assert suppressed, (
        "a ticker corroborated inside the dedup window was reported as a FIRE — the "
        "gate would emit nothing for it")


def test_a_corroboration_older_than_the_dedup_window_does_not_suppress():
    """The control: without it, `suppressed` could just be always-True."""
    conn = db_connection()
    _seed_three_instruments(conn, "OLDCORR")
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, created_at) VALUES "
        "('RULE_10','OLDCORR','HIGH','[CORROBORATION] ancient', "
        f"datetime('now','-{r10.DEDUP_WINDOW_DAYS + 5} days'))")
    conn.commit()

    rows = {r[0]: r for r in cc.scan(conn, 14, live_only=True)}
    conn.close()
    assert not rows["OLDCORR"][4]


def test_best_mode_never_claims_suppression():
    """Dedup is meaningless across historical windows — must always be False there."""
    conn = db_connection()
    _seed_three_instruments(conn, "BESTMODE")
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, created_at) VALUES "
        "('RULE_10','BESTMODE','HIGH','[CORROBORATION] prior', datetime('now','-1 days'))")
    conn.commit()
    rows = cc.scan(conn, 14, live_only=False)
    conn.close()
    assert all(r[4] is False for r in rows)


# ── the drift guard on the one thing still restated ──────────────────────────

def test_the_candidate_selector_IS_the_gates_not_a_copy():
    """Identity, not equivalence. The two names must be the SAME function object.

    This file used to carry its own CANDIDATES SELECT that merely resembled
    `_candidate_alerts`. Equivalent-today is not coupled: the moment the gate changed
    which alerts are candidates, the watch tool would have gone on reporting the old
    set while claiming to show what the gate sees.
    """
    assert cc._candidate_alerts is r10._candidate_alerts


def test_no_candidate_sql_is_restated_in_this_script():
    """Belt and braces: a future edit must not reintroduce the copy."""
    import inspect
    src = inspect.getsource(cc)
    body = "\n".join(l.split("#")[0] for l in src.splitlines())
    upper = body.upper()
    assert "FROM ALERTS" not in upper, (
        "check_convergence is selecting from `alerts` itself again — it must go through "
        "the gate's _candidate_alerts")
    assert "SEVERITY IN" not in upper, "the gate's severity predicate has been copied back in"


def test_live_mode_sees_exactly_what_the_gate_selects():
    """Behavioural: the ticker set must equal the gate's own candidate set."""
    conn = db_connection()
    _seed_three_instruments(conn, "AGREE")
    # an excluded rule and a MEDIUM alert: the gate drops both, so this must too
    conn.execute("INSERT INTO alerts (rule, ticker, severity, headline, created_at) "
                 "VALUES ('RULE_07','NOISE','HIGH','x', datetime('now','-1 days'))")
    conn.execute("INSERT INTO alerts (rule, ticker, severity, headline, created_at) "
                 "VALUES ('RULE_06','MEH','MEDIUM','x', datetime('now','-1 days'))")
    conn.commit()

    gate_tickers = {r["ticker"] for r in r10._candidate_alerts(conn, 14 * 24)}
    scan_tickers = {r[0] for r in cc.scan(conn, 14, live_only=True)}
    conn.close()

    assert scan_tickers == gate_tickers, (
        f"watch tool and gate disagree about candidates: "
        f"only-scan={scan_tickers - gate_tickers}, only-gate={gate_tickers - scan_tickers}")
    assert "NOISE" not in scan_tickers and "MEH" not in scan_tickers


def test_eligibility_and_threshold_are_imported_not_restated():
    """The rest of the coupling is by import; assert the objects are the SAME ones."""
    import jpt_common
    assert cc.RULE_10_EXCLUDED is jpt_common.RULE_10_EXCLUDED
    assert cc.RULE_10_MIN_INSTRUMENTS == jpt_common.RULE_10_MIN_INSTRUMENTS
    assert cc.rule10_instruments is jpt_common.rule10_instruments
    assert cc._already_corroborated is r10._already_corroborated
    assert cc.DEDUP_WINDOW_DAYS == r10.DEDUP_WINDOW_DAYS


def test_the_script_only_ever_reads():
    """SELECT-only, asserted against the source rather than trusted."""
    import inspect
    src = inspect.getsource(cc)
    body = "\n".join(l.split("#")[0] for l in src.splitlines())
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE "):
        assert verb not in body.upper(), f"{verb.strip()} found in a read-only script"
    assert "mode=ro" in src


def test_a_change_to_the_gates_dedup_window_is_visible_here():
    """ABSOLUTE pin, deliberately NOT derived from DEDUP_WINDOW_DAYS.

    The other dedup tests seed their fixture at `DEDUP_WINDOW_DAYS + 5` days, so they
    slide with the constant and stay green if the gate's window changes — proven by
    mutation (7 -> 99 left them all passing). That is right for stating the invariant
    and useless for detecting drift.

    This script advertises `--mode live` as "what RULE_10 would actually see", so a
    change to the gate's suppression window must force a conscious update here rather
    than silently altering what the watch tool reports. If you widened the window on
    purpose, update the numbers below.
    """
    assert r10.DEDUP_WINDOW_DAYS == 7, (
        "the gate's dedup window changed — check_convergence advertises the gate's "
        "behaviour, so confirm the watch output is still what you mean it to be")

    conn = db_connection()
    _seed_three_instruments(conn, "THIRTYD")
    conn.execute(
        "INSERT INTO alerts (rule, ticker, severity, headline, created_at) VALUES "
        "('RULE_10','THIRTYD','HIGH','[CORROBORATION] 30d ago', datetime('now','-30 days'))")
    conn.commit()
    rows = {r[0]: r for r in cc.scan(conn, 14, live_only=True)}
    conn.close()
    # 30 days > 7, so under the CURRENT window this is not suppressed. Under a 99-day
    # window it would be — which is exactly the drift this test exists to surface.
    assert rows["THIRTYD"][4] is False
