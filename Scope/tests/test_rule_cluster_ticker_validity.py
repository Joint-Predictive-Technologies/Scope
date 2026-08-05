"""RULE_CLUSTER must not turn an unvalidated parse string into a corroboration key.

`_gather` keys on `normalize_ticker(resolved_symbol or raw_ticker_string)`
(`rule_cluster.py:129`). The `or` is the defect: when the ingestion linker CORRECTLY
declines to link a row (`ticker_id` NULL), the fallback resurrects the raw parse
string the linker just rejected.

Measured on the working DB before this fix — of 59 all-time qualifying clusters,
exactly one keys on a symbol absent from `tickers`:

    US  3 members, every row "Treasury Bill [GS]", ticker_id NULL, raw='US'
        (213 such rows across 11 members)

`US` is not a symbol, so the whitelist bars it.

⚠️ IT DOES NOT CLOSE THE WIDER CLASS. The whitelist validates the SYMBOL, not the
INSTRUMENT, and most state abbreviations the parser lifts out of bond descriptions
ARE real tickers — TX (Ternium), OR (OR Royalties), GO (Grocery Outlet), ST
(Sensata), AA (Alcoa), BC (Brunswick), AD (Array Digital). An Arlington
municipal-bond cluster keys as Ternium and validates. That limit is pinned at the
bottom of this file and needs the PARSER, not this rule.

⭐ THE FALLBACK IS KEPT, NOT REMOVED. `SPCX` reaches RULE_CLUSTER through it — all
four of its rows have `ticker_id` NULL — and SPCX is a real symbol present in
`tickers`. Deleting the `or` would kill a genuine cluster. What changes is that only
a symbol IN `tickers` may become a corroboration key.

⭐ KEY REMOVAL IS THE MECHANISM, NOT RETRACTION. RULE_CLUSTER is unsigned, so
`alert_corroborates` short-circuits True regardless of `lifecycle_stage`. What the
gate filters on is `_candidate_alerts`' `ticker IS NOT NULL AND ticker != ''`.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection  # noqa: E402
from scripts import rule_cluster as rc  # noqa: E402


def _members(conn, n, prefix="M"):
    for i in range(n):
        conn.execute("INSERT INTO members (bioguide_id, full_name) VALUES (?,?)",
                     (f"{prefix}{i:06d}", f"Member {i}"))


def _txns(conn, member_prefix, n, raw, ticker_id, ttype="purchase", desc=None, days_apart=1):
    """n distinct members trading `raw` inside one 72h window."""
    for i in range(n):
        conn.execute(
            "INSERT INTO transactions (member_id, ticker_id, raw_ticker_string, "
            "raw_description, transaction_type, transaction_date) "
            "VALUES (?,?,?,?,?, date('now', ?))",
            (f"{member_prefix}{i:06d}", ticker_id, raw, desc, ttype,
             f"-{2 + i*days_apart} days"),
        )


def _run(conn):
    """Commit the fixture, then run. `rc.run()` opens its OWN connection, so
    uncommitted fixture rows are invisible to it."""
    conn.commit()
    result = rc.run()
    conn.commit()          # end our snapshot so we can read rc's committed writes
    return result


def _emitted(conn):
    conn.commit()
    return conn.execute(
        "SELECT id, ticker, severity, lifecycle_stage, why_matters, headline "
        "FROM alerts WHERE rule='RULE_CLUSTER' ORDER BY id"
    ).fetchall()


# --------------------------------------------------------------------------
# The Treasury cluster: still forms, no longer a key
# --------------------------------------------------------------------------

def test_the_US_treasury_cluster_forms_but_is_not_a_corroboration_key():
    with db_connection() as conn:
        _members(conn, 3)
        _txns(conn, "M", 3, "US", None, desc="Treasury Bill [GS]")
        assert "US" not in rc._validity_set(conn), "fixture assumes US is not a symbol"
        _run(conn)
        rows = _emitted(conn)
        assert len(rows) == 1, "the cluster must still FORM — visibility, not deletion"
        r = rows[0]
        assert r["ticker"] == "", "an unvalidated symbol must not be a corroboration key"
        assert r["lifecycle_stage"] == "review"
        assert "US" in r["why_matters"], "the symbol must be preserved for triage"
        assert "UNVERIFIED->no corroboration" in r["why_matters"]
        assert "US" in r["headline"], "the symbol stays visible in the headline"

        # Ask the GATE itself, not a re-implementation of its predicate.
        import scripts.rule_10_corroboration as r10
        assert [c["ticker"] for c in r10._candidate_alerts(conn, 72)] == []


def test_a_state_abbreviation_from_a_bond_description_is_not_a_key():
    """The parser lifts `TX` out of 'Arlington, Municipal Bond'."""
    with db_connection() as conn:
        _members(conn, 3)
        conn.execute("INSERT INTO tickers (id, symbol) VALUES (1,'NVDA')")
        _txns(conn, "M", 3, "PA", None, desc="Philadelphia, Municipal Bond")
        _run(conn)
        rows = _emitted(conn)
    assert len(rows) == 1 and rows[0]["ticker"] == ""


# --------------------------------------------------------------------------
# The control: real clusters are untouched
# --------------------------------------------------------------------------

def test_a_valid_symbol_cluster_still_corroborates_unchanged():
    with db_connection() as conn:
        _members(conn, 3)
        conn.execute("INSERT INTO tickers (id, symbol) VALUES (1,'NVDA')")
        _txns(conn, "M", 3, "NVDA", 1)
        _run(conn)
        rows = _emitted(conn)
        assert len(rows) == 1
        r = rows[0]
        assert r["ticker"] == "NVDA"
        assert r["lifecycle_stage"] in (None, "", "created")
        assert "UNVERIFIED" not in (r["why_matters"] or "")
        import scripts.rule_10_corroboration as r10
        assert [c["ticker"] for c in r10._candidate_alerts(conn, 72)] == ["NVDA"]


def test_the_SPCX_case_a_real_symbol_reached_only_via_the_raw_fallback():
    """All four real SPCX rows have `ticker_id` NULL — it arrives through the `or`.

    Proof the fallback must be VALIDATED, not deleted: removing it would kill this
    genuine cluster outright.
    """
    with db_connection() as conn:
        _members(conn, 3)
        conn.execute("INSERT INTO tickers (id, symbol, company_name) "
                     "VALUES (1,'SPCX','SPACE EXPLORATION TECHNOLOGIES CORP')")
        _txns(conn, "M", 3, "SPCX", None)      # ticker_id NULL -> raw fallback
        _run(conn)
        rows = _emitted(conn)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "SPCX", "a real symbol must keep its key even unlinked"


def test_a_real_symbol_ABSENT_from_tickers_is_flagged_not_dropped_and_not_fuzzed():
    """The SPCX lesson: absence from `tickers` is a coverage gap, not fakeness.

    Real and currently missing from the 10,619-row table: FI (Fiserv — the table
    still holds the pre-2023 FISV), CTRA (Coterra), NSRGY (Nestlé ADR, an OTC line
    the SEC US-registrant feed does not carry).
    """
    with db_connection() as conn:
        _members(conn, 3)
        conn.execute("INSERT INTO tickers (id, symbol) VALUES (1,'FISV'), (2,'NVDA')")
        _txns(conn, "M", 3, "FI", None)        # Fiserv's current symbol; table is stale
        _run(conn)
        rows = _emitted(conn)
    assert len(rows) == 1, "must be KEPT, not dropped"
    assert rows[0]["ticker"] == "", "and barred from the gate — errs toward not corroborating"
    assert "FI" in rows[0]["why_matters"]
    assert rows[0]["ticker"] != "FISV", "must NOT be fuzzy-resolved to a near neighbour"


# --------------------------------------------------------------------------
# Canonicalisation, and the things that must not change
# --------------------------------------------------------------------------

def test_the_validity_set_canonicalises_the_tickers_side():
    """`tickers` stores dash-form class shares (551 of them) and zero dot-form."""
    with db_connection() as conn:
        conn.execute("INSERT INTO tickers (id, symbol) VALUES (1,'BRK-B')")
        valid = rc._validity_set(conn)
    assert "BRK.B" in valid, "the tickers side must be canonicalised, not just the raw side"


def test_a_dash_dot_class_share_keeps_its_key():
    with db_connection() as conn:
        _members(conn, 3)
        conn.execute("INSERT INTO tickers (id, symbol) VALUES (1,'BRK-B')")
        _txns(conn, "M", 3, "BRK.B", None)
        _run(conn)
        rows = _emitted(conn)
    assert len(rows) == 1 and rows[0]["ticker"] == "BRK.B"


def test_dedup_looks_up_the_STORED_ticker():
    """`_prior_cluster_alerts` filters `WHERE ticker = ?`.

    Querying the group key while storing '' would never find the prior unvalidated
    cluster, and the same identity would re-emit on every run.
    """
    with db_connection() as conn:
        _members(conn, 3)
        _txns(conn, "M", 3, "US", None, desc="Treasury Bill")
        _run(conn)
        assert len(_emitted(conn)) == 1
        _run(conn)
        assert len(_emitted(conn)) == 1, "second run must dedup, not re-emit"


def test_cluster_direction_is_untouched_and_still_unanimity():
    """Out of scope for this fix — pinned so a keying change cannot drift it."""
    assert rc._cluster_direction({"a": "buy", "b": "buy"}) == "consensus_buy"
    assert rc._cluster_direction({"a": "sell", "b": "sell"}) == "consensus_sell"
    assert rc._cluster_direction({"a": "buy", "b": "sell"}) == "mixed"
    assert rc._cluster_direction({"a": "buy", "b": "mixed"}) == "mixed", "unanimity, not majority"


def test_novelty_is_anchored_on_the_fingerprint_not_the_blanked_ticker(monkeypatch):
    """Capture the anchor actually handed to `insert_alert`.

    An earlier version asserted `novelty_score == 1.0` on a first-ever alert in an
    empty DB — which is true under ANY anchor, and passed with the fingerprint
    deliberately replaced by the blanked ticker. It proved nothing. The real
    mechanism is `jpt_common.py:1537` (`anchor = novelty_key or (ticker or ...)`),
    so this reads the argument instead of a downstream score.
    """
    seen = {}
    real = rc.insert_alert

    def _spy(conn, **kw):
        seen.update(kw)
        return real(conn, **kw)

    monkeypatch.setattr(rc, "insert_alert", _spy)
    with db_connection() as conn:
        _members(conn, 3)
        _txns(conn, "M", 3, "US", None, desc="Treasury Bill")
        _run(conn)
    assert seen.get("ticker") == "", "the stored key is blanked"
    assert str(seen.get("novelty_key", "")).startswith("CLUSTER::"), \
        "novelty must anchor on the cluster fingerprint, never on the blanked ticker"
    assert "US" in seen["novelty_key"], "the fingerprint keeps the group symbol"


# --------------------------------------------------------------------------
# The shared '' namespace — the regression the verifier found
# --------------------------------------------------------------------------

def test_two_unvalidated_symbols_do_not_dedup_against_each_other():
    """Both store `ticker=''`, and the identity test does not include the symbol.

    `_prior_cluster_alerts` narrows on the stored ticker, so without a symbol check
    an FI cluster and a CTRA cluster share one namespace: the second is silently
    suppressed even though it is a different company. Caught by the verifier; both
    symbols are real ones genuinely absent from the 10,619-row table.
    """
    with db_connection() as conn:
        _members(conn, 4)
        # FI: 4 members. CTRA: a SUBSET of the same members -> identical/subset
        # identity, which is exactly what the dedup and supersede tests compare.
        _txns(conn, "M", 4, "FI", None)
        _txns(conn, "M", 3, "CTRA", None)
        _run(conn)
        rows = _emitted(conn)
    keys = sorted((r["ticker"], r["headline"].split()[-4]) for r in rows)
    assert len(rows) == 2, (
        f"both unvalidated clusters must emit; got {len(rows)}: "
        f"{[r['headline'] for r in rows]}"
    )
    assert all(r["ticker"] == "" for r in rows)
    assert {"FI", "CTRA"} <= {w for r in rows for w in r["headline"].split()}


def test_one_unvalidated_symbol_does_not_supersede_another():
    """A cluster on one symbol must never be marked superseded by another symbol's."""
    with db_connection() as conn:
        _members(conn, 4)
        _txns(conn, "M", 3, "FI", None)
        _run(conn)
        _txns(conn, "M", 4, "CTRA", None)
        _run(conn)
        rows = _emitted(conn)
    superseded = [r for r in rows if r["lifecycle_stage"] == "superseded"]
    assert superseded == [], (
        "a different company's cluster must not supersede this one: "
        f"{[(r['ticker'], r['headline'], r['lifecycle_stage']) for r in rows]}"
    )


def test_dedup_still_works_within_one_unvalidated_symbol():
    """The symbol check must not disable dedup for the SAME unvalidated symbol."""
    with db_connection() as conn:
        _members(conn, 3)
        _txns(conn, "M", 3, "US", None, desc="Treasury Bill")
        _run(conn)
        assert len(_emitted(conn)) == 1
        _run(conn)
        assert len(_emitted(conn)) == 1, "same symbol, same identity must still dedup"


# --------------------------------------------------------------------------
# Observability, and the limit of what a symbol whitelist can do
# --------------------------------------------------------------------------

def test_the_unvalidated_counter_is_reported():
    """Untested until the verifier removed the increment and nothing went red."""
    with db_connection() as conn:
        _members(conn, 3)
        _txns(conn, "M", 3, "US", None, desc="Treasury Bill")
        res = _run(conn)
    assert res["unvalidated"] == 1
    assert res["emitted"] == 1


def test_an_empty_tickers_table_is_reported_as_CRITICAL(capsys):
    """The worst case, and the first version of this guard SUPPRESSED it.

    The condition read `if valid_symbols and ...`, so an empty `tickers` — which
    un-keys the entire corpus — printed no warning at all.
    """
    with db_connection() as conn:
        _members(conn, 3)
        _txns(conn, "M", 3, "ANY", None)
        conn.execute("DELETE FROM tickers")
        _run(conn)
    out = capsys.readouterr().out
    assert "CRITICAL:tickers_table_empty" in out


def test_a_bogus_but_REAL_symbol_still_reaches_the_gate_KNOWN_LIMIT():
    """⚠️ The whitelist validates the SYMBOL, not the INSTRUMENT.

    The parser lifts state abbreviations out of municipal-bond descriptions, and
    most of them ARE real tickers: TX (Ternium), OR (OR Royalties), GO (Grocery
    Outlet), ST (Sensata), AA (Alcoa), BC (Brunswick), AD (Array Digital). So an
    Arlington municipal-bond cluster keys as Ternium and validates.

    An earlier version of this file claimed the state-abbreviation class was closed
    and "proved" it with `PA` — the one abbreviation absent from `tickers`. That was
    a selection effect. This pins the real, UNCLOSED limit: fixing it needs the
    parser, which is out of scope here.
    """
    with db_connection() as conn:
        _members(conn, 3)
        conn.execute("INSERT INTO tickers (id, symbol, company_name) "
                     "VALUES (1,'TX','Ternium S.A.')")
        _txns(conn, "M", 3, "TX", None, desc="JT Arlington Cnty 5.00% 7/01/35")
        _run(conn)
        rows = _emitted(conn)
    assert rows[0]["ticker"] == "TX", (
        "documents the LIMIT, not the desired end state: a bond keyed on a real "
        "symbol still corroborates. Should fail once the parser is fixed."
    )
