#!/usr/bin/env python3
"""
RULE_01B — the direction is READ from the transaction, never assumed.

Runs under pytest:  pytest tests/test_rule01b_direction.py

Why these fixtures exist. The headline was the hardcoded string "opens new position"
on every alert, while the stored corpus was purchase 104 / sale 52 / sale_partial 31 /
exchange 5 — so **88 of 192 (45.8%) described a disposal as opening a position**.
ABT #706 headlined "opens new position in ABT" over a detail reading "Transaction:
sale partial": one alert contradicting itself.

⚠️ THIS UNBLOCKS SIGNING RULE_01B; IT DOES NOT SIGN IT. `corroborates` is written but
inert — `alert_corroborates` short-circuits for any rule outside `SIGNED_RULES`. That
invariant is asserted here (`test_this_session_changed_NO_gate_behaviour`) so a future
signing session is a deliberate act and not something this change did by accident.
"""
import importlib.util
import os
import sys
from datetime import date, timedelta

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


r01b = _load(_RULE_PATH, "rule_01b_dir")

import jpt_common  # noqa: E402

MEMBER = "A000001"
TODAY = date.today()
RECENT = TODAY - timedelta(days=10)
BIG = "$15,001 - $50,000"

# The complete distinct set in `transactions.transaction_type`: 9967 rows, 0 NULL,
# 0 empty, exactly these four. Verified, not assumed — an unhandled fifth value is
# exactly the failure `test_an_unrecognised_type_claims_no_direction` guards.
REAL_TX_TYPES = ["purchase", "sale", "sale_partial", "exchange"]
DISPOSALS = ["sale", "sale_partial", "exchange"]


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
                     [("ABT", "Abbott"), ("NVDA", "NVIDIA")])
    conn.commit()
    conn.close()
    yield


def _tx(tx_id, ticker, tx_type, band=BIG):
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO transactions (id, member_id, raw_ticker_string, transaction_type, "
        "amount_band, transaction_date, filing_date) VALUES (?,?,?,?,?,?,?)",
        (tx_id, MEMBER, ticker, tx_type, band, RECENT.isoformat(), TODAY.isoformat()),
    )
    conn.commit()
    conn.close()


def _one():
    conn = jpt_common.db_connection()
    rows = conn.execute(
        "SELECT ticker, headline, detail, why_matters, corroborates, corroboration_note, tags "
        "FROM alerts WHERE rule='RULE_01B'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"expected exactly one alert, got {len(rows)}"
    return rows[0]


# ─────────────────────────────────────────────────────────────────────────────
# 1. EVERY TYPE IS DESCRIBED HONESTLY
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tx_type,phrase,corr", [
    ("purchase",     "opens new position in",                        1),
    ("sale",         "exits a previously undisclosed position in",   0),
    ("sale_partial", "reduces a previously undisclosed position in", 0),
    ("exchange",     "discloses an exchange in",                     0),
])
def test_the_headline_and_the_verdict_match_the_transaction(tx_type, phrase, corr):
    _tx(100, "ABT", tx_type)

    r01b.run(emit=True)

    row = _one()
    assert phrase in row["headline"], f"{tx_type} headlined {row['headline']!r}"
    assert row["corroborates"] == corr
    assert row["corroboration_note"]


@pytest.mark.parametrize("tx_type", DISPOSALS)
def test_no_disposal_is_ever_described_as_opening(tx_type):
    """The property, not the case list — this is the 88/192 defect stated directly."""
    _tx(100, "ABT", tx_type)

    r01b.run(emit=True)

    row = _one()
    blob = f"{row['headline']} {row['why_matters']}".lower()
    for word in ("opens", "opened", "opening", "new position"):
        assert word not in blob, (
            f"a {tx_type} is described with {word!r}: {row['headline']!r} / "
            f"{row['why_matters']!r}"
        )


def test_the_conviction_framing_appears_on_purchases_and_nowhere_else():
    """'High conviction' is a claim about an opening buy. It must not ride on a disposal."""
    _tx(100, "ABT", "purchase")
    r01b.run(emit=True)
    assert "conviction" in _one()["why_matters"].lower()

    for tx_type in DISPOSALS:
        conn = jpt_common.db_connection()
        conn.execute("DELETE FROM alerts")
        conn.execute("DELETE FROM transactions")
        conn.commit()
        conn.close()
        _tx(100, "ABT", tx_type)
        r01b.run(emit=True)
        assert "conviction" not in _one()["why_matters"].lower(), \
            f"conviction framing survived on a {tx_type}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. UNKNOWN TYPES FAIL HONESTLY, NEVER TO "OPENS"
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 1b. BOTH WRITERS' VOCABULARIES — found by verification
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tx_type,phrase,corr", [
    # parse_house_pdfs.normalize_transaction_type — SCHEDULED every 4h (api/main.py:124).
    # "S (full)" -> "sale_full" (:262). Absent from `transactions` today, fully producible.
    ("sale_full",      "exits a previously undisclosed position in",   0),
    # ingest_senate.py:94 stores the API's RAW string, unnormalised. transaction_verb
    # (:129-137) shows the shapes: "Purchase", "Sale (Full)", "Sale (Partial)".
    ("Sale (Full)",    "exits a previously undisclosed position in",   0),
    ("Sale (Partial)", "reduces a previously undisclosed position in", 0),
    ("Purchase",       "opens new position in",                        1),
    # raw PTR codes, in case a path ever writes them straight through
    ("S (full)",       "exits a previously undisclosed position in",   0),
    ("P",              "opens new position in",                        1),
    ("E",              "discloses an exchange in",                     0),
    # case and whitespace must not decide a direction
    ("  SALE  ",       "exits a previously undisclosed position in",   0),
    ("sale   partial", "reduces a previously undisclosed position in", 0),
])
def test_the_other_writers_vocabularies_are_understood(tx_type, phrase, corr):
    """A full sale is the least ambiguous disposal there is.

    It must never emit as "the transaction type was not recognised" — that is fail-safe
    but it is a lie by omission about a real, named, producible category. The original
    claim that the four observed values were "the complete set" was true of the DATA and
    false of the WRITERS.
    """
    _tx(100, "ABT", tx_type)

    r01b.run(emit=True)

    row = _one()
    assert phrase in row["headline"], f"{tx_type!r} headlined {row['headline']!r}"
    assert row["corroborates"] == corr
    if corr != 1:
        assert "conviction" not in (row["why_matters"] or "").lower()


@pytest.mark.parametrize("tx_type", ["receive", "", None, "PURCHASE_OR_SOMETHING"])
def test_an_unrecognised_type_claims_no_direction(tx_type):
    """A fifth value must not inherit the old hardcoded default."""
    _tx(100, "ABT", tx_type)

    r01b.run(emit=True)

    row = _one()
    assert "first disclosed trade in" in row["headline"]
    assert "opens" not in row["headline"].lower()
    assert row["corroborates"] is None, (
        "an unrecognised transaction type produced a definite direction verdict; "
        "NULL is the UNKNOWN value that fails closed once RULE_01B is signed"
    )
    assert "conviction" not in (row["why_matters"] or "").lower()


def test_every_emitted_alert_sets_why_matters():
    """The gate on the by-rule fallback in api/routers/evidence.py:172.

    That renders `alert.get("why_matters") or _WHY[rule]`, and `_WHY["RULE_01B"]` claims
    the member OPENED a position. It fired on all 192 stored alerts because none set
    `why_matters`. If any path stops setting it, the false claim comes straight back.
    """
    for i, tx_type in enumerate(REAL_TX_TYPES + ["receive"], start=1):
        _tx(100 * i, f"T{i}", tx_type)

    r01b.run(emit=True)

    conn = jpt_common.db_connection()
    rows = conn.execute("SELECT headline, why_matters FROM alerts WHERE rule='RULE_01B'").fetchall()
    conn.close()
    assert len(rows) == 5
    for r in rows:
        assert (r["why_matters"] or "").strip(), (
            f"{r['headline']!r} has no why_matters — evidence.py will fall back to the "
            "by-rule text that says the member opened a position"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. COMPOSITION WITH THE TICKER-VALIDATION SESSION
# ─────────────────────────────────────────────────────────────────────────────

def test_an_unvalidated_ticker_keeps_its_marker_AND_gets_an_honest_direction():
    """Both features write the headline. They must compose, not overwrite each other."""
    _tx(100, "GROUP", "sale")

    r01b.run(emit=True)

    row = _one()
    assert "(UNVERIFIED SYMBOL)" in row["headline"], "the ticker-validation marker was lost"
    assert "exits a previously undisclosed position in" in row["headline"]
    assert row["ticker"] == "", "the ticker-validation blanking was lost"


def test_a_valid_purchase_is_unaffected_by_either_feature():
    _tx(100, "NVDA", "purchase")

    r01b.run(emit=True)

    row = _one()
    assert row["ticker"] == "NVDA"
    assert "UNVERIFIED" not in row["headline"]
    assert "opens new position in NVDA" in row["headline"]
    assert row["corroborates"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. THIS SESSION MUST NOT HAVE SIGNED THE RULE
# ─────────────────────────────────────────────────────────────────────────────

def test_this_session_changed_NO_gate_behaviour():
    """`corroborates` is written but INERT until RULE_01B is deliberately signed.

    Writing the verdict is the precondition for signing, not signing itself. If this
    ever starts mattering without someone editing `SIGNED_RULES` on purpose, a sale
    would begin failing corroboration silently — a real behaviour change smuggled in
    under an attribution fix.
    """
    gate = _load(_GATE_PATH, "rule_10_dir")

    assert jpt_common.SIGNED_RULES == frozenset({"RULE_06"}), (
        f"SIGNED_RULES is {jpt_common.SIGNED_RULES} — this session must not change it; "
        "signing RULE_01B is its own human-gated session"
    )

    _tx(100, "ABT", "sale")
    r01b.run(emit=True)

    conn = jpt_common.db_connection()
    alert = conn.execute(
        "SELECT rule, corroborates, corroboration_note FROM alerts WHERE rule='RULE_01B'"
    ).fetchone()
    conn.close()

    assert alert["corroborates"] == 0, "the bearish verdict was not recorded"
    verdict, _reason = gate.alert_corroborates(alert)
    assert verdict is True, (
        "a RULE_01B sale now fails the gate's per-alert check — this session was supposed "
        "to record the direction, not act on it. RULE_01B is not in SIGNED_RULES."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. MUTATION — prove the fixtures discriminate
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tx_type", ["sale", "sale_partial"])
def test_restoring_the_hardcoded_headline_turns_the_disposal_fixtures_red(tmp_path, tx_type):
    """Put the hardcoded string back and confirm the disposal fixtures go red."""
    src = open(_RULE_PATH).read()
    target = 'headline = f"First Touch — {name} {verb} {ticker}"'
    assert target in src, (
        "the headline construction this mutation targets is no longer in the source — "
        "the mutation test has gone stale and must be updated"
    )
    mutant_path = tmp_path / f"rule_01b_mutant_{tx_type}.py"
    mutant_path.write_text(src.replace(
        target, 'headline = f"First Touch — {name} opens new position in {ticker}"', 1))
    mutant = _load(str(mutant_path), f"rule_01b_dir_mutant_{tx_type}")

    _tx(100, "ABT", tx_type)
    mutant.run(emit=True)

    assert "opens new position" in _one()["headline"], (
        "the hardcoded headline did NOT reappear under mutation, so the disposal "
        "fixtures do not discriminate and prove nothing about the fix"
    )
