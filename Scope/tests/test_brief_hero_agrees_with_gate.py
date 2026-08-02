#!/usr/bin/env python3
"""
The landing page's hero may only say "converges" where RULE_10 would fire.

The hero sat on one ticker for days announcing a convergence that did not exist,
because it counted with a taxonomy local to morning_brief.py at a threshold of 2.
The first attempt at the fix counted gate INSTRUMENTS but still diverged from the
gate on three other axes — it split basket tickers, took every severity, and
looked back 7 days instead of 14 — so it could still print "converges" on a
population RULE_10 refuses. These tests pin the agreement itself rather than any
one of those axes, so a future edit to either side has to keep them consistent.
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location(
    "morning_brief", os.path.join(_REPO, "scripts", "morning_brief.py"))
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)

import jpt_common  # noqa: E402
from scripts.rule_10_corroboration import (  # noqa: E402
    CONVERGENCE_WINDOW_DAYS, find_corroborated_tickers)


def _seed(conn, rows):
    """rows: (ticker, rule, severity[, days_ago[, corroborates]])

    ⚠️ A SIGNED RULE'S LEG DEFAULTS TO A GENUINE BUY. Since 2026-07-30 the gate asks a
    per-alert question as well as a per-rule one, and a NULL verdict fails closed — so
    leaving it unset would silently remove the insider leg from these fixtures and the
    two-way agreement below would be asserted over a different population than intended.
    The rejected direction is exercised explicitly by
    `test_the_hero_refuses_a_convergence_whose_insider_leg_is_a_SALE`.
    """
    for row in rows:
        ticker, rule, sev = row[0], row[1], row[2]
        days = row[3] if len(row) > 3 else 1
        corroborates = row[4] if len(row) > 4 else (
            1 if rule.upper() in jpt_common.SIGNED_RULES else None)
        conn.execute(
            "INSERT INTO alerts (rule, headline, severity, ticker, created_at, "
            f"corroborates) VALUES (?,?,?,?,datetime('now','-{int(days)} days'),?)",
            (rule, f"{rule} on {ticker}", sev, ticker, corroborates))
    conn.commit()


def _gate_fires_on(conn):
    return set(find_corroborated_tickers(
        conn, window_hours=CONVERGENCE_WINDOW_DAYS * 24).keys())


@pytest.mark.parametrize("label,rows", [
    # The three null controls the verifier used to overturn the first attempt.
    ("basket promotes a ticker the gate never credits", [
        ("LMT RTX NOC", "RULE_08", "MEDIUM"),
        ("LMT", "RULE_11", "HIGH"),
        ("LMT", "RULE_09", "HIGH"),
    ]),
    ("three instruments but all below the gate's severity floor", [
        ("ACME", "RULE_06", "LOW"),
        ("ACME", "RULE_09", "LOW"),
        ("ACME", "RULE_11", "LOW"),
    ]),
    ("three instruments but outside the gate's 14-day window", [
        ("ACME", "RULE_06", "HIGH", 30),
        ("ACME", "RULE_09", "HIGH", 30),
        ("ACME", "RULE_11", "HIGH", 30),
    ]),
    ("noise only, however much of it", [
        ("NOI", "RULE_OSINT", "CRITICAL"), ("NOI", "RULE_07", "CRITICAL"),
        ("NOI", "RULE_ANOMALY", "CRITICAL"), ("NOI", "RULE_REDDIT", "CRITICAL"),
    ]),
    ("congressional trio is ONE instrument (D1)", [
        ("ACME", "RULE_01B", "HIGH"), ("ACME", "RULE_02", "HIGH"),
        ("ACME", "RULE_CLUSTER", "HIGH"),
    ]),
])
def test_hero_never_claims_a_convergence_the_gate_refuses(label, rows):
    conn = jpt_common.db_connection()
    _seed(conn, rows)
    hero = mb._synthesize_headline(conn)
    fires = _gate_fires_on(conn)
    conn.close()
    assert not fires, f"precondition: gate should refuse {label}"
    assert hero["mode"] != "converge", (
        f"hero claimed a convergence the gate refuses ({label}): {hero}")


def test_hero_does_claim_a_convergence_the_gate_accepts():
    """The other direction — the honest heading must not be unconditional."""
    conn = jpt_common.db_connection()
    # RULE_09 was the third leg here until it was excluded as CONTEXT (2026-08-02).
    # RULE_15 is an eligible instrument, so this is still a genuine 3-instrument fire.
    _seed(conn, [("ACME", "RULE_06", "HIGH"),
                 ("ACME", "RULE_15", "HIGH"),
                 ("ACME", "RULE_11", "HIGH")])
    hero = mb._synthesize_headline(conn)
    fires = _gate_fires_on(conn)
    conn.close()
    assert "ACME" in fires, "precondition: the gate should fire here"
    assert hero["mode"] == "converge" and hero["ticker"] == "ACME", hero
    assert len(hero["types"]) >= 3, hero


def test_the_hero_refuses_a_convergence_whose_insider_leg_is_a_SALE():
    """THE SIGNED LEG, on the brief. Same three rules as the test above, but the insider
    SOLD — so the gate refuses, and the hero must refuse with it.

    ⚠️ `morning_brief` INLINES ITS OWN COPY of the gate's severity floor and window, which
    is why this pair of tests exists at all. It is therefore exactly where a per-alert
    decision goes stale: if the brief stopped consulting the verdict it would announce
    "RTX converges" on the strength of an executive selling $1.8M of stock, which is the
    real headline this whole change exists to prevent.
    """
    conn = jpt_common.db_connection()
    _seed(conn, [("SELLCO", "RULE_06", "HIGH", 1, 0),   # insider, but a SALE
                 ("SELLCO", "RULE_09", "HIGH"),
                 ("SELLCO", "RULE_11", "HIGH")])
    hero = mb._synthesize_headline(conn)
    fires = _gate_fires_on(conn)
    conn.close()
    assert "SELLCO" not in fires, "precondition: the gate must refuse this"
    assert not (hero.get("mode") == "converge" and hero.get("ticker") == "SELLCO"), (
        "the hero announced a convergence the gate refuses — on an insider SALE")


def test_the_hero_reads_the_same_window_and_floor_as_the_gate():
    """Pins the three axes the first attempt silently diverged on."""
    src = open(os.path.join(_REPO, "scripts", "morning_brief.py")).read()
    fn = src[src.index("def _synthesize_headline"):src.index("def _activity_strip")]
    assert "CONVERGENCE_WINDOW_DAYS" in fn, "hero must reuse the gate's window"
    assert "'HIGH','CRITICAL'" in fn or '"HIGH","CRITICAL"' in fn, \
        "hero must reuse the gate's severity floor"
    assert ".split()" not in fn, \
        "hero must key on the raw ticker, as the gate does — no basket splitting"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_an_EXCLUDED_rules_VOLUME_cannot_choose_the_headline(monkeypatch):
    """THE TIEBREAK. The earlier fix stopped an excluded rule CLAIMING a convergence;
    it did not stop one CHOOSING the headline. `hi` breaks the tie when two tickers
    hold the same number of gate instruments, and it counted every alert — so measured
    on the real corpus, LMT tied SPCX and HII at 2 instruments and won with hi=34, of
    which **25 (73.5%) were RULE_OSINT**. Strip them and LMT falls to 9.

    Here: both tickers hold the SAME two real instruments, and the noisy one carries a
    pile of excluded-rule alerts on top. The honest winner is decided by the real
    signals, so the excluded volume must not move it.
    """
    conn = jpt_common.db_connection()
    # BOTH hold exactly TWO gate instruments, so the ranking falls through to the
    # `hi` tiebreak — which is the thing under test. QUIET has one more REAL alert;
    # NOISY has thirty excluded ones.
    rows = [("NOISY", "RULE_01B", "HIGH"), ("NOISY", "RULE_06", "HIGH"),
            ("QUIET", "RULE_01B", "HIGH"), ("QUIET", "RULE_06", "HIGH"),
            ("QUIET", "RULE_06", "HIGH")]
    rows += [("NOISY", "RULE_OSINT", "HIGH")] * 30
    _seed(conn, rows)
    hero = mb._synthesize_headline(conn)
    conn.close()
    assert hero.get("ticker") == "QUIET", (
        f"an excluded rule's volume chose the headline: {hero}")
