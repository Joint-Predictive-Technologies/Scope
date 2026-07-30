#!/usr/bin/env python3
"""THE RTX REGRESSION — the false convergence this change exists to prevent, end to end.

Prod theme **id 1**, `"Convergence: RTX — 3 instruments aligned"`, RULE_10 alert 32990
emitted 2026-07-29 01:24:06 with `instruments=[contracts,earnings,insider]` and
`instrument_count=3` — exactly the threshold, zero margin. Its three legs:

  insider    RULE_06 32926 — Brunk Troy D, "sold $1.8M (2.3x avg)". An EXERCISE-AND-SELL.
  earnings   RULE_15 27951 — "+2557% QoQ", whose denominator is another company's 8-K
                             where "RTX" means *rituximab*.
  contracts  RULE_11 x4    — real award ids, but `date_signed` 2005/2014/2020/2024 and
                             lifetime cumulative amounts; 0 new awards in the window.

**One of three legs survives scrutiny.** This file closes the insider one, which is the
only one in this session's scope — RULE_15 is blocked behind its attribution repair, and
the contracts leg still counts (at a reduced weight) because a real award did exist.

⚠️ WHY THIS IS A FIXTURE AND NOT A REPLAY. The local DB has **0 themes, 0 theme_signals
and 0 RULE_10 alerts**; RTX sits at 1 instrument there and no ticker in the database can
fire the gate. Alert id 32926 is a PROD id (local max is 8874). So the case is rebuilt
from `vault/Scope/02_Sessions/SESSION-2026-07-30-rtx-convergence-audit.md`, which recorded
the ids, timestamps, tags and per-leg verdicts.

⚠️ AND THE XML IS A RECONSTRUCTION, NOT A CAPTURED DOCUMENT. The audit recorded the four
transaction rows and the `aff10b5One` value; the filing's actual bytes were not kept. So
`_rtx_form4()` below is built from those recorded values and is labelled as such — it is
evidence about the SHAPE the audit measured, not a primary source. The one real captured
Form 4 in this repo (`fixtures/form4_aapl_0001140361-26-025622.xml`) is used alongside it
as an independent real-world control, because it happens to be an exercise filing too.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common                                                     # noqa: E402
import rule_06_form4 as r6                                            # noqa: E402
from jpt_common import db_connection                                  # noqa: E402
from scripts import rule_10_corroboration as r10                      # noqa: E402

REAL_AAPL = os.path.join(os.path.dirname(__file__), "fixtures",
                         "form4_aapl_0001140361-26-025622.xml")

# The four non-derivative rows the audit recorded for accession 0001225208-26-006773,
# and `<aff10b5One>0</aff10b5One>` — NOT a pre-planned trade, which is the one point in
# the leg's favour and the reason a 10b5-1 check alone would not have caught it.
RTX_ROWS = [("M", "A", 12600, 97.65),      # SAR exercise — 87% of the shares disposed
            ("S", "D", 1811, 210.6450),
            ("D", "D", 5854, 210.1700),    # net settlement back to the issuer
            ("S", "D", 6746, 210.2000)]
RTX_S_ONLY_DOLLARS = 1811 * 210.6450 + 6746 * 210.2000     # the "$1.8M" headline


def _rtx_form4(rows=None, aff="0") -> str:
    """RECONSTRUCTED from the audit's recorded rows — see the module docstring."""
    rows = RTX_ROWS if rows is None else rows
    txns = "".join(f"""
      <nonDerivativeTransaction>
        <securityTitle><value>Common Stock</value></securityTitle>
        <transactionDate><value>2026-07-24</value></transactionDate>
        <transactionCoding><transactionCode>{c}</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>{s}</value></transactionShares>
          <transactionPricePerShare><value>{p}</value></transactionPricePerShare>
          <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
        </transactionAmounts>
      </nonDerivativeTransaction>""" for c, ad, s, p in rows)
    return f"""<?xml version="1.0"?><ownershipDocument>
      <periodOfReport>2026-07-24</periodOfReport>
      <aff10b5One>{aff}</aff10b5One>
      <issuer><issuerCik>0000101829</issuerCik><issuerName>RTX Corp</issuerName>
        <issuerTradingSymbol>RTX</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId>
        <rptOwnerCik>0002031955</rptOwnerCik><rptOwnerName>Brunk Troy D</rptOwnerName>
        </reportingOwnerId><reportingOwnerRelationship>
        <isOfficer>true</isOfficer>
        <officerTitle>President, Collins Aerospace</officerTitle>
        </reportingOwnerRelationship></reportingOwner>
      <nonDerivativeTable>{txns}</nonDerivativeTable></ownershipDocument>"""


# ── 1. the filing itself ─────────────────────────────────────────────────────

def test_the_audits_numbers_are_reproduced_from_the_filing():
    """Before asserting a verdict, confirm the fixture reproduces what was MEASURED —
    otherwise this file could pass against a filing the audit never saw."""
    p = r6.parse_form4(_rtx_form4())
    assert p is not None and p.ticker == "RTX"
    assert p.issuer_cik == "0000101829"      # RTX Corp, cross-checked in the audit
    assert p.is_10b5_1 == 0, "the audit recorded aff10b5One=0 — NOT a planned trade"
    # the headline figure, to the cent
    assert round(p.ps_value, 2) == round(RTX_S_ONLY_DOLLARS, 2) == 1_799_487.29
    # ...and RULE_06's own P/S list still cannot see the exercise or the settlement
    assert sorted(t.code for t in p.transactions) == ["S", "S"]
    assert sorted({t.code for t in p.all_transactions}) == ["D", "M", "S"]


def test_the_exercise_and_sell_does_NOT_corroborate():
    """THE VERDICT. Derived from the filing, not asserted as a constant."""
    ok, why = r6.parse_form4(_rtx_form4()).corroboration_verdict()
    assert ok is False
    assert "no genuine open-market buy" in why
    assert "M" in why, "the reason should name the codes a human can check"


def test_the_stored_sale_purchase_TAG_IS_NOT_AN_ACCEPTABLE_FALLBACK():
    """Why the obvious shortcut — "just read the tag RULE_06 already writes" — is refused.

    On RTX the tag says "sale", which is the right DIRECTION, so it is tempting to conclude
    the tag would have caught this. It would not, and these are the cases that show why:
    `majority_action` classifies on the transaction CODE alone, over a P/S-only list. It
    never reads `transactionAcquiredDisposedCode` and never sees a 10b5-1 flag.

      * `P`/`D` — code P but DISPOSED. A real shape, and one of the eight rejections
        `test_insider_clusters.py::test_what_is_NOT_a_qualifying_buy` already documents.
        The tag calls it a purchase because the letter is P.
      * a 10b5-1 planned `P`/`A` buy. The tag calls it a purchase; it carries no timing
        conviction, so it is not corroboration.

    In both the tag says "purchase" and the verdict says no. A gate trusting that word
    would count them.
    """
    assert r6.parse_form4(_rtx_form4()).majority_action == "sold"

    p_disposed = r6.parse_form4(_rtx_form4(rows=[("P", "D", 1000, 50.0)]))
    assert p_disposed.majority_action == "bought", (
        "control drifted — the point is that the TAG reads this as a purchase")
    assert p_disposed.corroboration_verdict()[0] is False, "code P but DISPOSED is not a buy"

    planned = r6.parse_form4(_rtx_form4(rows=[("P", "A", 1000, 50.0)], aff="1"))
    assert planned.majority_action == "bought", "the tag cannot see the 10b5-1 flag"
    assert planned.corroboration_verdict()[0] is False
    assert "10b5-1" in planned.corroboration_verdict()[1]


def test_an_exercise_and_HOLD_is_not_a_buy_either():
    """The acquisition half on its own. `majority_action` cannot even classify this — its
    P/S list is empty, so buys and sells tie at zero and it falls through to "sold" — while
    the verdict answers it directly from the codes."""
    hold = r6.parse_form4(_rtx_form4(rows=[("M", "A", 12600, 97.65)]))
    assert hold.transactions == [], "the P/S list should not see an M row"
    ok, why = hold.corroboration_verdict()
    assert ok is False and "no genuine open-market buy" in why


def test_a_REAL_captured_exercise_filing_also_does_not_corroborate():
    """Independent of the reconstruction above: the only real Form 4 kept in this repo is
    an `M/A + F/D + M/D` exercise, and it must reach the same verdict."""
    ok, why = r6.parse_form4(open(REAL_AAPL, encoding="utf-8").read()).corroboration_verdict()
    assert ok is False, why


# ── 2. the gate, on the prod theme's three legs ──────────────────────────────

def _seed_prod_theme_1(conn, insider_verdict, insider_note="exercise-and-sell"):
    """Prod theme 1's legs, at their recorded ages inside the 14-day window."""
    for rule, age, verdict, note, key in (
            ("RULE_11", "-13 days", None, None, "CONT_AWD_W31P4Q24C0024_9700_-NONE-_-NONE-"),
            ("RULE_15", "-6 days", None, None, None),
            ("RULE_06", "-1 days", insider_verdict, insider_note, None)):
        conn.execute(
            """INSERT INTO alerts (rule, ticker, severity, headline, created_at,
                                   corroborates, corroboration_note, award_key)
               VALUES (?, 'RTX', 'HIGH', ?, datetime('now', ?), ?, ?, ?)""",
            (rule, f"{rule} on RTX", age, verdict, note, key))
    conn.commit()


def _rtx_instruments(conn):
    rows = r10._candidate_alerts(conn, r10.CONVERGENCE_WINDOW_DAYS * 24)
    return r10.instruments_for([r for r in rows if r["ticker"] == "RTX"])


def test_prod_theme_1_would_no_longer_fire():
    """3 instruments -> 2. The gate's own `find_corroborated_tickers`, not a reimplementation."""
    conn = db_connection()
    _seed_prod_theme_1(conn, insider_verdict=0)
    instruments = _rtx_instruments(conn)
    fires = "RTX" in r10.find_corroborated_tickers(conn, r10.CONVERGENCE_WINDOW_DAYS * 24)
    conn.close()

    assert instruments == ["contracts", "earnings"], instruments
    assert len(instruments) < r10.MIN_DISTINCT_INSTRUMENTS
    assert not fires, "the RTX false convergence would fire again"


def test_it_fired_before_the_change_and_the_insider_leg_is_WHY():
    """The discriminator. Same three legs with the insider leg marked as a genuine buy and
    the gate fires — so the refusal above is caused by the direction, not by the fixture
    being one leg short for some unrelated reason (a severity floor, a window edge)."""
    conn = db_connection()
    _seed_prod_theme_1(conn, insider_verdict=1, insider_note="open-market buy")
    instruments = _rtx_instruments(conn)
    fires = "RTX" in r10.find_corroborated_tickers(conn, r10.CONVERGENCE_WINDOW_DAYS * 24)
    conn.close()

    assert instruments == ["contracts", "earnings", "insider"], instruments
    assert fires, "the pre-change behaviour is not reproduced — the regression is unproven"


def test_the_dropped_leg_is_DISCLOSED_not_silently_absent():
    """A gate that hides its rejections is as dishonest as one that miscounts. A human
    reading the theme must be able to see that an insider filed and that they sold."""
    conn = db_connection()
    _seed_prod_theme_1(conn, insider_verdict=0,
                       insider_note="no genuine open-market buy (codes D,M,S)")
    rows = [r for r in r10._candidate_alerts(conn, r10.CONVERGENCE_WINDOW_DAYS * 24)
            if r["ticker"] == "RTX"]
    conn.close()
    dropped = r10.non_corroborating(rows)
    assert [d["rule"] for d in dropped] == ["RULE_06"]
    assert "no genuine open-market buy" in dropped[0]["reason"]


def test_the_emitted_alert_records_both_the_counted_and_the_rejected_legs():
    """`tags.rules` must be the CORROBORATING set — five consumers re-derive the count from
    it — while `rules_present` keeps the provenance."""
    import json
    conn = db_connection()
    # a genuine third leg so something actually fires and can be inspected
    _seed_prod_theme_1(conn, insider_verdict=0)
    conn.execute(
        """INSERT INTO alerts (rule, ticker, severity, headline, created_at)
           VALUES ('RULE_16', 'RTX', 'HIGH', 'RULE_16 on RTX', datetime('now','-2 days'))""")
    conn.commit()
    r10.run(dry_run=False, window_hours=r10.CONVERGENCE_WINDOW_DAYS * 24)
    row = conn.execute("SELECT tags FROM alerts WHERE rule='RULE_10' AND ticker='RTX'"
                       ).fetchone()
    conn.close()
    assert row is not None, "nothing emitted — the fixture no longer reaches the gate"
    tags = json.loads(row["tags"])
    assert "RULE_06" not in tags["rules"], "a rejected leg reached the counted set"
    assert "RULE_06" in tags["rules_present"], "the rejected leg was hidden entirely"
    assert tags["instrument_count"] == len(tags["instruments"]) == 3
    assert [d["rule"] for d in tags["non_corroborating"]] == ["RULE_06"]
    # and the count the scoring path re-derives from those tags agrees with the gate
    assert jpt_common._distinct_rule_count("RULE_10", row["tags"])[0] == 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
