#!/usr/bin/env python3
"""
RULE_01B — the window is WHEN THE TRADE BECAME PUBLIC, not when it happened.

Runs under pytest:  pytest tests/test_rule01b_window_basis.py

Why these fixtures exist. The candidate window filtered `transaction_date`, and PTRs disclose
30-45 days after the trade — often much later. So a late-filed disclosure was stale on arrival
and never revisited: **932 of 9967 transactions (9.4%) are filed >90d after the trade, 582 of
those are genuine first touches, and none ever alerted as itself.** Late filers are precisely
this rule's target population — a trade that was hidden and has just become public.

Two properties under test beyond "late filers now emit":

  * COALESCE. 441 rows have a NULL `filing_date`. A bare `filing_date` window keeps only 21 of
    the 109 candidates the rule sees today, silently dropping 88, because `date(NULL)` removes
    a row from the comparison without a trace.
  * The anti-join stays UNWINDOWED. "First" is only correct because the NOT EXISTS scans all of
    `transactions`, not just the window. Widening the window must not widen that.
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


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r01b = _load(_RULE_PATH, "rule_01b_win")

import jpt_common  # noqa: E402

MEMBER = "A000001"
TODAY = date.today()
BIG = "$15,001 - $50,000"


def _d(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO members (bioguide_id, full_name, party, state, chamber, is_current) "
        "VALUES (?,?,?,?,?,1)",
        (MEMBER, "Alpha, Aaron", "Democratic", "NJ", "House of Representatives"),
    )
    conn.executemany("INSERT INTO tickers (symbol, company_name) VALUES (?,?)",
                     [("ABT", "Abbott"), ("NVDA", "NVIDIA"), ("MSFT", "Microsoft")])
    conn.commit()
    conn.close()
    yield


def _tx(tx_id, ticker, traded_days_ago, filed_days_ago):
    """`traded_days_ago=None` / `filed_days_ago=None` seed the respective NULL."""
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO transactions (id, member_id, raw_ticker_string, transaction_type, "
        "amount_band, transaction_date, filing_date) VALUES (?,?,?,'purchase',?,?,?)",
        (tx_id, MEMBER, ticker, BIG,
         _d(traded_days_ago) if traded_days_ago is not None else None,
         _d(filed_days_ago) if filed_days_ago is not None else None),
    )
    conn.commit()
    conn.close()


def _alerts():
    conn = jpt_common.db_connection()
    rows = conn.execute(
        "SELECT ticker, tags, headline FROM alerts WHERE rule='RULE_01B' ORDER BY id").fetchall()
    conn.close()
    return rows


def _tickers():
    return [r["ticker"] for r in _alerts()]


# ─────────────────────────────────────────────────────────────────────────────
# 1. THE DEFECT — a late-filed first touch was invisible
# ─────────────────────────────────────────────────────────────────────────────

def test_a_late_filed_first_touch_now_emits():
    """Traded 200 days ago, disclosed 10 days ago. The disclosure is the news."""
    _tx(100, "ABT", traded_days_ago=200, filed_days_ago=10)

    r01b.run(emit=True)

    assert _tickers() == ["ABT"], (
        "a trade that became public 10 days ago was invisible because it happened 200 days "
        "ago — this is the 582-row class the rule was structurally blind to"
    )


def test_a_normally_filed_first_touch_is_unchanged():
    _tx(100, "ABT", traded_days_ago=10, filed_days_ago=5)

    r01b.run(emit=True)

    assert _tickers() == ["ABT"]


def test_a_genuinely_stale_disclosure_still_does_not_emit():
    """Traded 200d ago AND filed 200d ago — old news, correctly out of the window.

    Without this, "window on filing_date" could be satisfied by removing the window.
    """
    _tx(100, "ABT", traded_days_ago=200, filed_days_ago=200)

    r01b.run(emit=True)

    assert _tickers() == []


def test_trade_age_is_unbounded():
    """A 540-day-old trade, newly disclosed, fires. Pins the human decision.

    The signal is the disclosure becoming public, not the trade being recent. If a recency
    bound is ever added it must be a deliberate edit, not a drift.
    """
    _tx(100, "ABT", traded_days_ago=540, filed_days_ago=10)

    r01b.run(emit=True)

    assert _tickers() == ["ABT"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. THE NULL FALLBACK — the 88-row class
# ─────────────────────────────────────────────────────────────────────────────

def test_a_null_filing_date_falls_back_and_is_not_dropped():
    """441 rows have no filing_date. `date(NULL)` deletes a row from the comparison.

    A bare `filing_date` window keeps only 21 of the 109 candidates the rule sees today.
    This is the single most dangerous way to get this change wrong, because the candidate
    count barely moves (109 -> 104) while 88 rows are silently swapped out.
    """
    _tx(100, "ABT", traded_days_ago=10, filed_days_ago=None)

    r01b.run(emit=True)

    assert _tickers() == ["ABT"], (
        "a first touch with no filing_date vanished — a missing filing date must never "
        "silently delete a disclosure"
    )


def test_a_null_filing_date_outside_the_window_still_does_not_emit():
    """The fallback must not become an escape hatch that ignores the window entirely."""
    _tx(100, "ABT", traded_days_ago=200, filed_days_ago=None)

    r01b.run(emit=True)

    assert _tickers() == []


def test_an_undated_trade_with_a_recent_filing_date_still_cannot_claim_first_touch():
    """The guard that this change made LOAD-BEARING, covered where the change lives.

    `AND date(t.transaction_date) IS NOT NULL` used to be redundant: the old
    `transaction_date` window already excluded NULLs, and the chronology session's comment
    said exactly that, flagging that the honesty guarantee must not depend on a window
    basis that was a queued change. This IS that change. With COALESCE, a row with no
    transaction date but a recent filing date now passes the window, and only that guard
    stops it asserting "no prior disclosed trade" about a trade whose date is unknown.

    A verification pass found the guard had no fixture in this file — its only coverage was
    incidental, in the chronology suite, and would have vanished if that helper were ever
    tidied to seed a NULL filing date alongside the NULL transaction date.
    """
    _tx(100, "ABT", traded_days_ago=None, filed_days_ago=5)

    r01b.run(emit=True)

    assert _tickers() == [], (
        "a trade with no transaction_date claimed first touch because its filing date was "
        "recent — the date(t.transaction_date) IS NOT NULL guard is gone or bypassed"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. THE INTERACTION MOST LIKELY TO BREAK SILENTLY
# ─────────────────────────────────────────────────────────────────────────────

def test_the_anti_join_is_still_unwindowed():
    """A prior trade OUTSIDE the window must still block a first-touch claim.

    "First" is only true because the NOT EXISTS scans ALL of `transactions`. If widening the
    window ever leaked into the anti-join, the rule would call a trade "first" whenever the
    real first one was old enough — reintroducing defect #1 through the back door.
    """
    _tx(100, "ABT", traded_days_ago=900, filed_days_ago=900)   # ancient, out of window
    _tx(200, "ABT", traded_days_ago=10, filed_days_ago=5)      # in window, NOT the first

    r01b.run(emit=True)

    assert _tickers() == [], (
        "a trade was called a first touch even though an older trade in the same ticker "
        "exists — the anti-join is being windowed"
    )


def test_a_late_filer_is_not_sorted_behind_recent_trades():
    """ORDER BY must agree with the window, or the LIMIT cuts the target population first."""
    _tx(100, "ABT", traded_days_ago=200, filed_days_ago=1)     # newest DISCLOSURE, oldest trade
    _tx(200, "NVDA", traded_days_ago=5, filed_days_ago=5)
    _tx(300, "MSFT", traded_days_ago=2, filed_days_ago=2)

    r01b.run(emit=True)

    assert _tickers()[0] == "ABT", (
        f"emitted in order {_tickers()} — the most recently DISCLOSED row should sort first "
        "when the window is on the filing date; otherwise late filers are what LIMIT drops"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. MUTATION
# ─────────────────────────────────────────────────────────────────────────────

def test_restoring_the_transaction_date_window_turns_the_late_filer_red(tmp_path):
    src = open(_RULE_PATH).read()
    target = ("AND date(COALESCE(t.filing_date, t.transaction_date)) "
              ">= date('now', '-90 days')")
    assert target in src, (
        "the window clause this mutation targets is no longer in the source — the mutation "
        "test has gone stale and must be updated"
    )
    mutant_path = tmp_path / "rule_01b_win_mutant.py"
    mutant_path.write_text(src.replace(
        target, "AND date(t.transaction_date) >= date('now', '-90 days')", 1))
    mutant = _load(str(mutant_path), "rule_01b_win_mutant")

    _tx(100, "ABT", traded_days_ago=200, filed_days_ago=10)
    mutant.run(emit=True)

    assert _tickers() == [], (
        "the late-filed fixture still emits under the restored transaction_date window, so "
        "it does not discriminate and proves nothing about the fix"
    )


def test_the_window_and_the_sort_use_the_same_expression():
    """Coupling: if one basis is changed and the other is not, late filers hit the LIMIT first."""
    src = open(_RULE_PATH).read()
    window = re.search(r"AND date\((COALESCE\([^)]*\))\)\s*>=\s*date\('now'", src)
    order = re.search(r"ORDER BY date\((COALESCE\([^)]*\))\)\s*DESC", src)
    assert window and order, "window or ORDER BY no longer uses a COALESCE date expression"
    assert window.group(1) == order.group(1), (
        f"window sorts on {order.group(1)} but filters on {window.group(1)} — they must "
        "agree or the LIMIT drops the rows the window was widened to include"
    )
