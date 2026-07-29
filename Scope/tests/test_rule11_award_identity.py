#!/usr/bin/env python3
"""
RULE_11 award identity — the defects the 2026-07-28 repair fixed.

Every test here encodes a defect that was live in production, each one traced to
a single root cause: `rule_11_contracts.py` requested `Action Date`, which is not
a valid Contract Award field on `spending_by_award`, so it always came back
`null` and `award_date` fell back to the RUN date.

Runs under pytest. Each test gets a fresh temp DB (see tests/conftest.py); the
USASpending call is always stubbed, so the suite makes no network requests.
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location(
    "rule_11_contracts", os.path.join(_REPO, "scripts", "rule_11_contracts.py"))
r11 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r11)

import jpt_common  # noqa: E402


def _award(award_id, recipient, amount, base_date, piid="PIID1",
           agency="Department of Defense"):
    """A `spending_by_award` result row, shaped exactly like the live API's."""
    return {
        "generated_internal_id": award_id,
        "Award ID": piid,
        "Recipient Name": recipient,
        "Award Amount": amount,
        "Awarding Agency": agency,
        "Base Obligation Date": base_date,
        "Start Date": base_date,
        "End Date": "2030-01-01",
        "Description": "test award",
        "Contract Award Type": "DEFINITIVE CONTRACT",
    }


@pytest.fixture
def seeded(monkeypatch):
    """A DB with a ticker the resolver maps, and no watchlist sweep."""
    conn = jpt_common.db_connection()
    conn.execute("INSERT OR IGNORE INTO tickers (symbol, company_name) "
                 "VALUES ('LMT', 'LOCKHEED MARTIN CORP')")
    conn.commit()
    conn.close()
    return True


def _stub_fetch(monkeypatch, large, small=None):
    """Stub the paged fetch; returns (records, truncated=False)."""
    def fake(start_date, end_date, min_amount, max_pages, archive_dir=None):
        if min_amount >= r11.LARGE_THRESHOLD:
            return list(large), False
        return list(small or []), False
    monkeypatch.setattr(r11, "_fetch_awards", fake)


def test_two_awards_to_one_recipient_on_one_day_both_persist(seeded, monkeypatch):
    """THE drop bug.

    `UNIQUE(recipient_name, award_date)` + `INSERT OR IGNORE` meant the second
    award to a recipient on a given date was silently discarded, and because
    every award_date was the RUN date, that collapsed to "one award per recipient
    per run". Measured on a real 270-award YTD sweep: the unique key alone loses
    17 (6.3%) even with correct dates; as the bug actually shipped it lost 106 of
    270 (39.3%), i.e. everything past each recipient's first award.
    """
    _stub_fetch(monkeypatch, [
        _award("CONT_AWD_AAA", "LOCKHEED MARTIN CORP", 900_000_000, "2026-03-31", "AAA"),
        _award("CONT_AWD_BBB", "LOCKHEED MARTIN CORP", 800_000_000, "2026-03-31", "BBB"),
    ])
    out = r11.run()
    assert out["status"] == "ok"
    assert out["inserted"] == 2, "second same-day award to the same recipient was dropped"

    conn = jpt_common.db_connection()
    rows = conn.execute(
        "SELECT award_id, amount FROM contracts ORDER BY amount DESC").fetchall()
    conn.close()
    assert [r["award_id"] for r in rows] == ["CONT_AWD_AAA", "CONT_AWD_BBB"]
    assert [r["amount"] for r in rows] == [900_000_000, 800_000_000]


def test_award_date_is_never_the_run_date(seeded, monkeypatch):
    """`award_date` must come from the government, or be NULL — never today.

    The old code's `or TODAY` fallback is what made identity collapse; an unknown
    date is now recorded as unknown rather than silently stamped with the run day.
    """
    rec = _award("CONT_AWD_CCC", "LOCKHEED MARTIN CORP", 600_000_000, None, "CCC")
    rec["Start Date"] = None                      # both date sources absent
    _stub_fetch(monkeypatch, [rec])
    r11.run()

    conn = jpt_common.db_connection()
    row = conn.execute("SELECT award_date FROM contracts").fetchone()
    conn.close()
    assert row["award_date"] is None, "an unknown award date must not become the run date"
    assert row["award_date"] != r11.TODAY


def test_reseeing_an_award_updates_it_and_never_duplicates(seeded, monkeypatch):
    """Identity is the award id, so a second sighting is an update."""
    first = _award("CONT_AWD_DDD", "LOCKHEED MARTIN CORP", 500_000_000, "2026-02-01", "DDD")
    _stub_fetch(monkeypatch, [first])
    r11.run()

    grown = _award("CONT_AWD_DDD", "LOCKHEED MARTIN CORP", 650_000_000, "2026-02-01", "DDD")
    _stub_fetch(monkeypatch, [grown])
    out = r11.run()
    assert out["inserted"] == 0 and out["updated"] == 1

    conn = jpt_common.db_connection()
    rows = conn.execute("SELECT amount FROM contracts").fetchall()
    alerts = conn.execute(
        "SELECT COUNT(*) c FROM alerts WHERE rule='RULE_11'").fetchone()["c"]
    conn.close()
    assert len(rows) == 1, "re-seeing an award must update, not duplicate"
    assert rows[0]["amount"] == 650_000_000, "the obligated total must track source"
    assert alerts == 1, "a re-seen award must not re-alert"


def test_amount_and_award_id_always_describe_the_same_award(seeded, monkeypatch):
    """The backfill incoherence.

    The old code matched a row by `(recipient, run-date)` and then wrote a
    DIFFERENT award's id onto it, so `amount` and `award_id` described two
    different contracts — measured on real data: five Boeing rows all carrying
    $31,972,918,249 against four award_ids whose true values were $2.73B-$3.13B.
    """
    _stub_fetch(monkeypatch, [
        _award("CONT_AWD_EEE", "LOCKHEED MARTIN CORP", 300_000_000, "2026-04-01", "EEE"),
        _award("CONT_AWD_FFF", "LOCKHEED MARTIN CORP", 200_000_000, "2026-04-01", "FFF"),
    ])
    r11.run()

    conn = jpt_common.db_connection()
    pairs = {r["award_id"]: r["amount"] for r in
             conn.execute("SELECT award_id, amount FROM contracts").fetchall()}
    conn.close()
    assert pairs == {"CONT_AWD_EEE": 300_000_000, "CONT_AWD_FFF": 200_000_000}, \
        "each row's amount must belong to that row's award"


def test_the_sweep_asks_for_newly_signed_awards_only(seeded, monkeypatch):
    """The real cause of 102/102 CRITICAL — and the only thing that fixes it.

    `_severity`'s thresholds were never wrong; the *inputs* were. With no working
    date filter the sort returned the largest CUMULATIVE contracts (a 1993
    Lockheed award, expired 2017, carrying $48B), so every alert cleared the
    $500M CRITICAL bar. Asserting that a stubbed $900M maps to CRITICAL would
    pass on the broken code too, so this pins the request instead: the sweep must
    filter on `new_awards_only`, which is what makes "awarded $X" a true claim.
    """
    seen: list[dict] = []

    def capture(payload):
        seen.append(payload)
        return {"results": [], "page_metadata": {"hasNext": False}}

    monkeypatch.setattr(r11, "_post", capture)
    r11.run()

    assert seen, "no request was issued"
    for payload in seen:
        window = payload["filters"]["time_period"][0]
        assert window.get("date_type") == "new_awards_only", (
            "without date_type=new_awards_only the window filters transaction "
            "activity, so decades-old mega-contracts return as if newly awarded")


def test_severity_tiers_map_award_values_to_a_spread(seeded, monkeypatch):
    """Companion to the above: the tier table itself, over per-award values.

    This pins the thresholds (unchanged by the repair); it does NOT prove the
    lifetime-vs-event fix — `test_the_sweep_asks_for_newly_signed_awards_only`
    does that.
    """
    _stub_fetch(monkeypatch, [
        _award("CONT_AWD_1", "LOCKHEED MARTIN CORP", 900_000_000, "2026-01-05", "P1"),
        _award("CONT_AWD_2", "LOCKHEED MARTIN CORP", 150_000_000, "2026-01-06", "P2"),
        _award("CONT_AWD_3", "LOCKHEED MARTIN CORP", 60_000_000, "2026-01-07", "P3"),
    ])
    r11.run()

    conn = jpt_common.db_connection()
    sev = {r["severity"]: r["n"] for r in conn.execute(
        "SELECT severity, COUNT(*) n FROM alerts WHERE rule='RULE_11' GROUP BY 1")}
    conn.close()
    assert sev == {"CRITICAL": 1, "HIGH": 1, "MEDIUM": 1}, sev


def test_upstream_failure_is_loud_and_never_looks_like_no_new_awards(seeded, monkeypatch):
    """A fetch failure previously returned [] and exited 0 with scanned=0.

    26 of 29 logged runs were that shape — an outage recorded as a clean scan.
    """
    def boom(*a, **k):
        raise r11.ContractsFetchError("HTTP 503 from spending_by_award")
    monkeypatch.setattr(r11, "_fetch_awards", boom)

    out = r11.run()
    assert out["status"] == "fetch_failed"
    assert out["error"]

    conn = jpt_common.db_connection()
    row = conn.execute(
        "SELECT notes FROM activity_log WHERE source='RULE_11' ORDER BY id DESC"
    ).fetchone()
    conn.close()
    assert row is not None, "a failed run must still record activity"
    assert "fetch_failed" in (row["notes"] or ""), row["notes"]


def test_rule11_is_still_exactly_the_contracts_instrument():
    """The repair must not move RULE_11 in the gate, or invent a phantom leg."""
    assert jpt_common.RULE_10_INSTRUMENTS["RULE_11"] == "contracts"
    assert jpt_common.rule10_instruments(["RULE_11"]) == ["contracts"]
    assert "RULE_11" not in jpt_common.RULE_10_EXCLUDED


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
