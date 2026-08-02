#!/usr/bin/env python3
"""
RULE_01B — "first" must mean CHRONOLOGICALLY EARLIEST, not lowest insertion id.

Runs under pytest:  pytest tests/test_rule01b_first_touch_chronology.py

Why these fixtures exist. RULE_01B's whole claim is "this member has no prior
disclosed trade in this ticker", and it used to decide that with `t2.id < t.id`
(pre-fix `scripts/rule_01b_first_touch.py:52`). Ingestion batches do not arrive in
trade-date order — a later batch routinely carries older trades — so on the
2026-07-28 snapshot 780 member+ticker pairs had a lowest-id row that was not the
earliest, and 39/192 stored alerts (20.3%) asserted "no prior disclosed trade"
about a member who had one.

RULE_01B is a live gate leg (`RULE_10_INSTRUMENTS["RULE_01B"] = "congressional"`),
so a false first-touch is a false corroborating leg.

Every id below is assigned EXPLICITLY, because the id/date inversion IS the bug.
A fixture that lets SQLite autoincrement would order ids and dates the same way
and would prove nothing.
"""
import importlib.util
import os
import re
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO = os.path.join(os.path.dirname(__file__), "..")
_RULE_PATH = os.path.join(_REPO, "scripts", "rule_01b_first_touch.py")


def _load(path, name="rule_01b"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r01b = _load(_RULE_PATH)

import jpt_common  # noqa: E402

MEMBER = "A000001"
TODAY = date.today()
RECENT = TODAY - timedelta(days=10)     # inside the rule's 90-day window
MIDDLE = TODAY - timedelta(days=40)     # inside the window, but earlier
ANCIENT = TODAY - timedelta(days=500)   # outside the window — the ABT shape

# Above $15k so the emitted alert is HIGH, i.e. gate-reaching. A MEDIUM alert
# would still be wrong, but HIGH is the class that actually reaches RULE_10.
BIG = "$15,001 - $50,000"


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO members (bioguide_id, full_name, party, state, chamber, is_current) "
        "VALUES (?,?,?,?,?,1)",
        (MEMBER, "Alpha, Aaron", "Democratic", "NJ", "House of Representatives"),
    )
    # Every symbol these chronology fixtures use must be VALID, or the ticker-validation
    # guard (added later, for audit defect #4) blanks `alerts.ticker` and these tests fail
    # for a reason that has nothing to do with chronology. Seeded so the two fixes stay
    # independently testable; validation itself is covered by
    # tests/test_rule01b_ticker_validation.py.
    conn.executemany(
        "INSERT INTO tickers (symbol, company_name) VALUES (?,?)",
        [(s, s) for s in ("ABT", "TPH", "NVDA", "MSFT", "PINS", "DVN", "AMT")],
    )
    conn.commit()
    conn.close()
    yield


def _tx(tx_id, ticker, when, ttype="purchase", band=BIG):
    """Seed one transaction at an EXPLICIT id. `when=None` seeds a NULL date."""
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO transactions (id, member_id, raw_ticker_string, transaction_type, "
        "amount_band, transaction_date, filing_date) VALUES (?,?,?,?,?,?,?)",
        (tx_id, MEMBER, ticker, ttype, band,
         when.isoformat() if when else None,
         TODAY.isoformat()),
    )
    conn.commit()
    conn.close()


def _alerts():
    conn = jpt_common.db_connection()
    rows = conn.execute(
        "SELECT id, ticker, tags, severity, detail FROM alerts WHERE rule='RULE_01B'"
    ).fetchall()
    conn.close()
    return rows


def _claimed_date(row):
    """tags = full_name|tx_type|delay_days|transaction_date|filing_date — field 3.

    Split on the pipe. The 2026-07-28 audit had to retract a figure because it
    read this with a fixed substr offset, which mis-parses whenever an earlier
    field changes width.
    """
    return row["tags"].split("|")[3]


# ─────────────────────────────────────────────────────────────────────────────
# 1. THE ABT SHAPE — the regression that shipped
# ─────────────────────────────────────────────────────────────────────────────

def test_a_later_trade_is_not_emitted_as_first_touch():
    """The exact Gottheimer/ABT inversion: later trade, LOWER id.

    Real ids: the 2026-05-27 sale was id 40206; the true earliest, 2025-01-28,
    was id 43602. Lowest id, latest date.
    """
    _tx(40206, "ABT", RECENT, ttype="sale_partial")   # later trade, LOWER id
    _tx(43602, "ABT", ANCIENT)                        # true earliest, HIGHER id

    r01b.run(emit=True)

    assert _alerts() == [], (
        "RULE_01B emitted a first-touch for a member with a provably earlier "
        "trade in the same ticker — this is the 39/192 class"
    )


def test_the_chronologically_earliest_row_is_the_one_identified():
    """When the true earliest IS inside the window, it is what gets emitted."""
    _tx(100, "TPH", RECENT)     # later trade, lower id
    _tx(200, "TPH", MIDDLE)     # earliest, higher id

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1, f"expected exactly one first-touch, got {len(rows)}"
    assert _claimed_date(rows[0]) == MIDDLE.isoformat(), (
        f"emitted the wrong row as 'first': claimed {_claimed_date(rows[0])}, "
        f"true earliest {MIDDLE.isoformat()}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. THE FIX MUST NOT SILENCE THE RULE
# ─────────────────────────────────────────────────────────────────────────────

def test_a_genuine_first_touch_still_emits():
    """One transaction, no siblings — the rule's actual job. Still fires."""
    _tx(100, "NVDA", RECENT)

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1, "the fix silenced a genuine first touch"
    assert rows[0]["ticker"] == "NVDA"
    assert rows[0]["severity"] == "HIGH"
    assert "no prior disclosed trade" in rows[0]["detail"]


def test_a_later_sibling_does_not_block_a_genuine_first_touch():
    """A member who buys, then buys again: the FIRST buy is still a first touch."""
    _tx(100, "MSFT", MIDDLE)    # the genuine first touch
    _tx(200, "MSFT", RECENT)    # a later follow-on

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1
    assert _claimed_date(rows[0]) == MIDDLE.isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# 3. NULL DATES — a trade of unknown date cannot be proven first
# ─────────────────────────────────────────────────────────────────────────────

def test_an_undated_sibling_blocks_the_no_prior_trade_claim():
    """The live hazard. NULL fails every `<` comparison SILENTLY.

    Without the explicit NULL arms, an undated sibling is invisible to the
    anti-join and the later trade claims first-touch anyway. Unknown order
    cannot be ruled out, so it must block the claim rather than be ignored.
    """
    _tx(100, "PINS", RECENT)    # looks like a first touch
    _tx(200, "PINS", None)      # ...but this one's date is unknown

    r01b.run(emit=True)

    assert _alerts() == [], (
        "emitted 'no prior disclosed trade' while an undated trade in the same "
        "ticker existed — the claim is not provable"
    )


def test_an_undated_candidate_never_claims_first_touch():
    """A trade with no date of its own cannot be asserted to be first."""
    _tx(100, "DVN", None)

    r01b.run(emit=True)

    assert _alerts() == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. DETERMINISM
# ─────────────────────────────────────────────────────────────────────────────

def test_a_same_date_pair_emits_exactly_one_row_the_lower_id():
    """Two trades on the same day: the tiebreak must pick one, and always the same one."""
    _tx(300, "AMT", RECENT)
    _tx(100, "AMT", RECENT)

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1, (
        f"a same-date pair produced {len(rows)} first-touch alerts — the "
        "tiebreak is not deterministic"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. MUTATION — prove the fixtures actually discriminate
# ─────────────────────────────────────────────────────────────────────────────

def test_restoring_the_id_comparison_turns_the_abt_fixture_red(tmp_path):
    """Re-introduce the defect and confirm test #1 goes red.

    A single-token mutation of the exact defective comparison. If the ABT
    fixture still passed against this, it would be proving nothing.
    """
    src = open(_RULE_PATH).read()
    target = re.compile(r"date\(t2\.transaction_date\)\s*<\s*date\(t\.transaction_date\)")
    assert target.search(src), (
        "the chronological comparison this mutation targets is no longer in the "
        "source — the mutation test has gone stale and must be updated"
    )
    mutant_src = target.sub("t2.id < t.id", src, count=1)

    mutant_path = tmp_path / "rule_01b_mutant.py"
    mutant_path.write_text(mutant_src)
    mutant = _load(str(mutant_path), name="rule_01b_mutant")

    # the exact fixture from test #1
    _tx(40206, "ABT", RECENT, ttype="sale_partial")
    _tx(43602, "ABT", ANCIENT)

    mutant.run(emit=True)

    assert len(_alerts()) == 1, (
        "the ABT fixture PASSES against the reinstated `t2.id < t.id` defect, so "
        "it does not discriminate and proves nothing about the fix"
    )
