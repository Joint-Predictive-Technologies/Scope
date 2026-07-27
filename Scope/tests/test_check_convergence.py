"""`check_convergence.py` had no tests. It is read-only, so nothing it does can corrupt
data — but it is a DIAGNOSTIC, and a diagnostic that misreports the gate is worse than
no diagnostic: it sends a human looking for a bug that isn't there, or reassures them
about one that is.

Two things are pinned here.

1. THE DEDUP. Counting instruments is only half of what RULE_10 does — a ticker already
   corroborated inside DEDUP_WINDOW_DAYS emits nothing. The script used to print
   "*** FIRE" for those, i.e. claim the gate should have fired when the gate was
   correctly silent.

2. THE ONE REMAINING COPY. Every decision the script makes is imported from the gate
   EXCEPT the candidate SELECT, which is restated because `--mode best` scans all
   history and so cannot reuse `_candidate_alerts`' baked-in trailing window. That is a
   genuine drift risk, so the predicate sets are compared here directly: if the gate
   ever changes which alerts are candidates, this test fails and names the drift.
"""
from __future__ import annotations

import os
import re
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

def _predicates(sql):
    """The semantic predicates, normalised away from formatting and alias noise."""
    sql = re.sub(r"\s+", " ", sql).upper()
    return {
        "severity": "SEVERITY IN ('HIGH', 'CRITICAL')" in sql.replace("','", "', '"),
        "ticker_notnull": "TICKER IS NOT NULL" in sql,
        "ticker_nonempty": "TICKER" in sql and "!= ''" in sql,
    }


def test_the_restated_candidate_predicates_still_match_the_gates():
    """If the gate changes WHICH alerts are candidates, this file must change too.

    check_convergence cannot import `_candidate_alerts` — that function bakes a
    trailing-window filter into its SQL, and `--mode best` scans all history. So the
    predicates are written out, and this asserts they have not diverged.
    """
    import inspect
    gate_sql = inspect.getsource(r10._candidate_alerts)
    assert _predicates(cc.CANDIDATES) == _predicates(gate_sql), (
        "check_convergence.CANDIDATES has drifted from rule_10_corroboration."
        "_candidate_alerts — the copy this script's docstring warns about")


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
