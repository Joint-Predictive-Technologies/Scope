#!/usr/bin/env python3
"""
RULE_01B — only a VALIDATED ticker may become a corroboration key.

Runs under pytest:  pytest tests/test_rule01b_ticker_validation.py

Why these fixtures exist. RULE_01B writes the raw parsed ticker string from the PTR
straight into `alerts.ticker`, and the gate groups corroboration candidates by that
column — so an unvalidated string is a live convergence KEY. On the 2026-07-28
snapshot 41 of 192 alerts (21.4%) carried a ticker absent from `tickers`, 22 of them
HIGH, and `NY` spanned TWO members (C001123, P000608): a cross-member "convergence"
on a string that is not a company.

The invariant under test, in both directions:
  * unvalidated  -> emitted, flagged, but NEVER a corroboration key
  * validated    -> completely unchanged
  * and NOTHING is deleted, and nothing is resolved to a "close" symbol.

⚠️ Absent from `tickers` is NOT the same as fake. `tickers` is measurably incomplete,
so a shape rule cannot split artifacts from coverage gaps — 40 of the 41 match
^[A-Z]{1,5}$ and the one that does not, BRK.B, is the REAL one. That asymmetry is
pinned by `test_a_coverage_gap_is_flagged_not_deleted` and `test_brk_b_validates_...`.
"""
import importlib.util
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO = os.path.join(os.path.dirname(__file__), "..")
_RULE_PATH = os.path.join(_REPO, "scripts", "rule_01b_first_touch.py")
_GATE_PATH = os.path.join(_REPO, "scripts", "rule_10_corroboration.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r01b = _load(_RULE_PATH, "rule_01b_tv")

import jpt_common  # noqa: E402

from datetime import date, timedelta  # noqa: E402

TODAY = date.today()
RECENT = TODAY - timedelta(days=10)
BIG = "$15,001 - $50,000"          # -> HIGH, i.e. above the gate's severity floor

MEMBERS = [("A000001", "Alpha, Aaron"), ("B000002", "Bravo, Bianca")]

# Deliberately small: the fixture DB carries only what each test declares valid, so a
# test can never pass because the real 10,619-row table happened to contain the symbol.
SEED_TICKERS = [("ABT", "Abbott Laboratories"), ("NVDA", "NVIDIA CORP"),
                ("BRK-B", "Berkshire Hathaway Inc Class B")]


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    conn = jpt_common.db_connection()
    conn.executemany(
        "INSERT INTO members (bioguide_id, full_name, party, state, chamber, is_current) "
        "VALUES (?,?,?,?,?,1)",
        [(b, n, "Democratic", "NJ", "House of Representatives") for b, n in MEMBERS],
    )
    conn.executemany("INSERT INTO tickers (symbol, company_name) VALUES (?,?)", SEED_TICKERS)
    conn.commit()
    conn.close()
    yield


def _tx(tx_id, ticker, member="A000001", when=None, band=BIG):
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO transactions (id, member_id, raw_ticker_string, transaction_type, "
        "amount_band, transaction_date, filing_date) VALUES (?,?,?,?,?,?,?)",
        (tx_id, member, ticker, "purchase", band,
         (when or RECENT).isoformat(), TODAY.isoformat()),
    )
    conn.commit()
    conn.close()


def _alerts():
    conn = jpt_common.db_connection()
    rows = conn.execute(
        "SELECT id, ticker, member_id, tags, severity, headline, detail, lifecycle_stage "
        "FROM alerts WHERE rule='RULE_01B' ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def _raw_of(row):
    """The raw parsed string, appended as tags field 5."""
    parts = row["tags"].split("|")
    return parts[5] if len(parts) > 5 else None


def _gate_candidates():
    """Rows the gate would actually consider — its own query, not a paraphrase."""
    conn = jpt_common.db_connection()
    rows = conn.execute(
        "SELECT id, ticker, rule, member_id FROM alerts "
        "WHERE ticker IS NOT NULL AND ticker != '' "
        "  AND severity IN ('HIGH','CRITICAL')"
    ).fetchall()
    conn.close()
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 1. THE LIVE FALSE-CONVERGENCE KEY
# ─────────────────────────────────────────────────────────────────────────────

def test_NY_no_longer_forms_a_cross_member_corroboration_key():
    """The exact prod shape: `NY` held by two different members.

    Two members, same artifact string, both HIGH — the ingredients of a cross-member
    convergence on something that is not a company.
    """
    _tx(100, "NY", member="A000001")
    _tx(200, "NY", member="B000002")

    r01b.run(emit=True)

    assert len(_alerts()) == 2, "both alerts should still exist — nothing is deleted"

    keys = [c["ticker"] for c in _gate_candidates()]
    assert "NY" not in keys, "`NY` is STILL a corroboration key — the gate can converge on it"
    assert keys == [], f"an artifact reached the gate's candidate set: {keys}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. UNVALIDATED — barred from the gate, but not deleted and not resolved
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("artifact", ["GROUP", "THE", "LLC", "CORP", "SIMON"])
def test_an_artifact_is_emitted_but_is_not_a_corroboration_key(artifact):
    _tx(100, artifact)

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1, "the alert was dropped — it should be flagged, not deleted"
    assert rows[0]["ticker"] == "", "the artifact is still a live corroboration key"
    assert rows[0]["lifecycle_stage"] == "review"
    assert _raw_of(rows[0]) == artifact, "the raw string was lost, not preserved"
    assert "UNVERIFIED SYMBOL" in rows[0]["headline"]
    assert _gate_candidates() == []


def test_a_coverage_gap_is_flagged_not_deleted_and_never_fuzzy_resolved():
    """The SPCX lesson. `CTRA` (Coterra) is real but absent from this fixture's `tickers`.

    It must be treated exactly like an artifact for GATE purposes — absence is not
    proof of reality either — but it must survive as a row, keep its own string, and
    NEVER be rewritten to a near neighbour.
    """
    _tx(100, "CTRA")

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1, "a real-but-uncovered symbol was DELETED — the SPCX failure"
    assert _raw_of(rows[0]) == "CTRA"
    # The seeded table contains NVDA/ABT/BRK-B. A fuzzy resolver would have reached for
    # one of them; an honest one leaves the string alone and the key blank.
    assert rows[0]["ticker"] == "", "an unvalidated symbol became a corroboration key"
    for near in ("ABT", "NVDA", "BRK-B", "BRK.B"):
        assert near not in (rows[0]["ticker"] or ""), f"fuzzy-resolved to {near}"
        assert _raw_of(rows[0]) != near, f"the raw string was rewritten to {near}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. VALIDATED — completely unchanged
# ─────────────────────────────────────────────────────────────────────────────

def test_a_valid_ticker_is_untouched_and_still_a_corroboration_key():
    _tx(100, "ABT")

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "ABT"
    assert rows[0]["lifecycle_stage"] == "created"
    assert "UNVERIFIED" not in rows[0]["headline"]
    assert [c["ticker"] for c in _gate_candidates()] == ["ABT"]


def test_brk_b_validates_against_a_tickers_row_stored_as_brk_dash_b():
    """`tickers` stores 551 class shares in the DASH form and ZERO in the dot form.

    `normalize_ticker` canonicalises to the dot, which is what reaches `alerts.ticker`.
    A naive `ticker IN (SELECT symbol FROM tickers)` would therefore reject every one of
    those 551 REAL symbols. Both sides must be normalised.
    """
    _tx(100, "BRK.B")

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "BRK.B", (
        "BRK.B was rejected as unvalidated even though `tickers` holds BRK-B — the "
        "validity set is not normalised on both sides"
    )
    assert rows[0]["lifecycle_stage"] == "created"


# ─────────────────────────────────────────────────────────────────────────────
# 4. THE FORCED DEDUP CONSEQUENCE
# ─────────────────────────────────────────────────────────────────────────────

def test_two_different_artifacts_for_one_member_do_not_collapse():
    """With a blank key, `(rule, ticker, member_id)` dedups them onto one slot.

    Measured on the snapshot: 11 of the 13 affected members hold more than one
    unvalidated alert (P000608 holds 11), so a naive blank key loses ~28 of 41.
    """
    _tx(100, "GROUP")
    _tx(200, "SIMON")

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 2, (
        f"expected 2 alerts, got {len(rows)} — the blank ticker collapsed the dedup key"
    )
    assert {_raw_of(r) for r in rows} == {"GROUP", "SIMON"}


def test_a_legacy_artifact_keyed_alert_does_not_re_emit():
    """Stored rows predate the fix and hold the raw string in `ticker`.

    If dedup only looked at the new tags field, the entire stored corpus would re-emit
    as a duplicate set on the next run.
    """
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO alerts (rule, headline, severity, tags, ticker, member_id) "
        "VALUES ('RULE_01B','First Touch — legacy','HIGH',"
        "        'Alpha, Aaron|purchase|7|2026-01-01|2026-01-08','GROUP','A000001')"
    )
    conn.commit()
    conn.close()

    _tx(100, "GROUP")
    r01b.run(emit=True)

    assert len(_alerts()) == 1, "the legacy corpus re-emitted as duplicates"


# ─────────────────────────────────────────────────────────────────────────────
# 5. THE COUPLING THE WHOLE MECHANISM RESTS ON
# ─────────────────────────────────────────────────────────────────────────────

def test_an_empty_tickers_table_fails_CLOSED_and_says_so():
    """Infrastructure failure must not read as "100% of symbols are artifacts".

    If `tickers` is empty the validity set is empty and every symbol fails — which is the
    right DIRECTION (a missing validity set must never wave everything through) but would
    dark the entire gate leg. It has to be loud, not silent.
    """
    conn = jpt_common.db_connection()
    conn.execute("DELETE FROM tickers")
    conn.commit()
    conn.close()

    _tx(100, "ABT")          # a genuinely real, normally-valid symbol
    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1, "the alert was dropped rather than flagged"
    assert rows[0]["ticker"] == "", "an empty validity set waved a symbol through"
    assert _gate_candidates() == []

    conn = jpt_common.db_connection()
    note = conn.execute(
        "SELECT notes FROM activity_log WHERE source='RULE_01B' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert note and "tickers_table_empty" in (note["notes"] or ""), (
        "an empty `tickers` table darkened the whole leg with nothing in activity_log "
        "to distinguish it from a run where every symbol really was an artifact"
    )


def test_the_gate_itself_cannot_see_a_blank_ticker_alert():
    """Blank-key exclusion is not our invariant — it is the GATE's, and we depend on it.

    Nothing in RULE_01B enforces "unvalidated cannot corroborate". It works only because
    `_candidate_alerts` filters `ticker != ''`. If that filter is ever relaxed, every
    flagged alert silently becomes a corroboration key again with no test failing
    anywhere near this rule.

    ⚠️ THIS CALLS THE GATE, IT DOES NOT GREP IT. The first version of this test searched
    the gate's source for the two literal predicates, and a verification pass defeated it
    in one line — `WHERE (ticker IS NOT NULL AND ticker != '' OR rule = 'RULE_01B')`
    re-admits every blank-keyed alert and still contains both strings. A source-presence
    test cannot express a behavioural coupling.
    """
    gate = _load(_GATE_PATH, "rule_10_coupling")

    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO alerts (rule, headline, severity, ticker, member_id, lifecycle_stage) "
        "VALUES ('RULE_01B','flagged','HIGH','','A000001','review')"
    )
    conn.execute(
        "INSERT INTO alerts (rule, headline, severity, ticker, member_id) "
        "VALUES ('RULE_01B','valid','HIGH','ABT','A000001')"
    )
    conn.commit()
    candidates = gate._candidate_alerts(conn, 336)
    conn.close()

    tickers = sorted(c["ticker"] for c in candidates)
    assert tickers == ["ABT"], (
        f"the gate's own candidate query returned {tickers} — a blank-ticker alert is "
        "visible to corroboration, so unvalidated RULE_01B symbols can complete a "
        "convergence again. Restore the filter, or give RULE_01B a real per-alert "
        "eligibility mechanism (it cannot use `corroborates` until it is signed)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5b. REGRESSIONS FOUND BY VERIFICATION — both were live defects in this change
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pair", [("AXB", "A_B"), ("AXB", "A%B"), ("ABCD", "A__D")])
def test_a_LIKE_metacharacter_in_a_raw_symbol_does_not_swallow_a_distinct_alert(pair):
    """`_` and `%` are LIKE metacharacters, and this branch handles PDF-parse garbage.

    The dedup was `tags LIKE '%|' || ?` with the raw string as the pattern, so `A_B`
    matched the row written for `AXB` and the second alert vanished — order-dependent,
    silent, no counter. A DATA-LOSS path introduced by the validation change itself and
    caught by a verification pass. Zero such strings exist in the corpus today, so
    nothing was actually lost.
    """
    first, second = pair
    _tx(100, first)
    _tx(200, second)

    r01b.run(emit=True)

    raws = sorted(_raw_of(r) for r in _alerts())
    assert raws == sorted(pair), (
        f"expected both {pair} to emit, got {raws} — a LIKE metacharacter in a parsed "
        "symbol silently swallowed a distinct alert"
    )


def test_triaging_a_symbol_into_tickers_does_not_duplicate_its_alert():
    """The documented remediation path must not re-emit.

    The flagged alert tells a human to add the symbol to `tickers`. Once they do, the
    candidate takes the VALID branch — which used to key only on `ticker=?` and so could
    not see the existing blank-keyed row, emitting a second alert for the same
    member+ticker: one 'review' with a blank key, one 'created' with the real one.
    """
    _tx(100, "CTRA")
    r01b.run(emit=True)
    assert len(_alerts()) == 1

    conn = jpt_common.db_connection()          # the human triages it in
    conn.execute("INSERT INTO tickers (symbol, company_name) VALUES ('CTRA','Coterra')")
    conn.commit()
    conn.close()

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1, (
        f"triaging CTRA into `tickers` produced a duplicate: "
        f"{[(r['ticker'], r['lifecycle_stage']) for r in rows]}"
    )


def test_a_validated_symbol_is_stored_in_its_CANONICAL_form():
    """Validation runs on the normalised string, so storage must too.

    Otherwise `$ABT` / `brk-b` / ` ABT ` validate and then reach the gate under a key
    that is not in `tickers` — the invariant would be literally false for them.
    """
    _tx(100, " abt ")

    r01b.run(emit=True)

    rows = _alerts()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "ABT", (
        f"stored {rows[0]['ticker']!r} — a validated alert reached the gate under a key "
        "that is not the canonical symbol"
    )
    assert [c["ticker"] for c in _gate_candidates()] == ["ABT"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. MUTATION — prove the fixtures discriminate
# ─────────────────────────────────────────────────────────────────────────────

def test_removing_the_validation_lets_NY_reach_the_gate_again(tmp_path):
    """Force `is_valid` true and confirm the NY fixture goes red.

    A single-token mutation of the exact predicate. If NY still failed to reach the gate
    against this, the fixture would be proving nothing.
    """
    src = open(_RULE_PATH).read()
    target = "is_valid = norm in valid_symbols"
    assert target in src, (
        "the validation predicate this mutation targets is no longer in the source — "
        "the mutation test has gone stale and must be updated"
    )
    mutant_path = tmp_path / "rule_01b_mutant.py"
    mutant_path.write_text(src.replace(target, "is_valid = True", 1))
    mutant = _load(str(mutant_path), "rule_01b_tv_mutant")

    _tx(100, "NY", member="A000001")
    _tx(200, "NY", member="B000002")

    mutant.run(emit=True)

    keys = [c["ticker"] for c in _gate_candidates()]
    assert keys == ["NY", "NY"], (
        f"expected the unfixed code to put NY in the gate's candidate set twice, got "
        f"{keys} — the fixture does not discriminate and proves nothing about the fix"
    )
