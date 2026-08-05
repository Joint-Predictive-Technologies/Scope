#!/usr/bin/env python3
"""
Tests for RULE_CLUSTER (congressional trading clusters).

Runs under pytest or standalone:  python3 tests/test_rule_cluster.py

Each test gets a fresh temp DB (DATABASE_PATH) with the full app schema, seeds
members + transactions, and drives rule_cluster.run() end to end.
"""
import importlib.util
import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location(
    "rule_cluster", os.path.join(_REPO, "scripts", "rule_cluster.py"))
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

import jpt_common  # noqa: E402

MEMBERS = [
    ("A000001", "Alpha, Aaron"),
    ("B000002", "Bravo, Bianca"),
    ("C000003", "Charlie, Cara"),
    ("D000004", "Delta, Dan"),
    ("E000005", "Echo, Ed"),
]

D0 = date.today() - timedelta(days=10)   # inside the 45-day horizon
D1 = D0 + timedelta(days=1)
D2 = D0 + timedelta(days=2)               # D0..D2 span = 48h, inside the 72h window


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    conn = jpt_common.db_connection()
    conn.executemany(
        "INSERT INTO members (bioguide_id, full_name, party, state, chamber, is_current) "
        "VALUES (?,?,?,?,?,1)",
        [(bid, name, "Democratic", "CA", "House of Representatives") for bid, name in MEMBERS],
    )
    conn.commit()
    conn.close()
    yield


def _tx(member, ticker, ttype, d, band="$1,001 - $15,000"):
    conn = jpt_common.db_connection()
    # Register the fixture symbol in `tickers`. RULE_CLUSTER now confers a
    # corroboration key only on a symbol that validates against that table, so a
    # fixture that never seeds it would exercise the unvalidated path and store
    # `ticker=''` — these tests are about clustering (consensus, severity, dedup,
    # upgrade), not about validity, and mean to run on a real symbol.
    # The unvalidated path has its own file: tests/test_rule_cluster_ticker_validity.py.
    conn.execute("INSERT OR IGNORE INTO tickers (symbol) VALUES (?)", (ticker,))
    conn.execute(
        "INSERT INTO transactions (member_id, raw_ticker_string, transaction_type, "
        "amount_band, transaction_date) VALUES (?,?,?,?,?)",
        (member, ticker, ttype, band, d.isoformat()),
    )
    conn.commit()
    conn.close()


def _cluster_alerts(ticker="AAA"):
    conn = jpt_common.db_connection()
    rows = conn.execute(
        "SELECT * FROM alerts WHERE rule='RULE_CLUSTER' AND ticker=? ORDER BY id",
        (ticker,),
    ).fetchall()
    out = [dict(r) for r in rows]
    conn.close()
    return out


def test_three_member_consensus_buy_is_high():
    _tx("A000001", "AAA", "purchase", D0)
    _tx("B000002", "AAA", "purchase", D1)
    _tx("C000003", "AAA", "purchase", D2)
    res = rc.run()
    assert res["emitted"] == 1
    a = _cluster_alerts()[0]
    assert a["severity"] == "HIGH"
    tags = json.loads(a["tags"])
    assert tags["direction"] == "consensus_buy"
    assert tags["distinct_members"] == 3
    assert "bought AAA" in a["headline"]
    assert a["time_horizon"] == "IMMEDIATE"


def test_five_member_cluster_is_critical():
    for m in ("A000001", "B000002", "C000003", "D000004", "E000005"):
        _tx(m, "AAA", "purchase", D1)
    res = rc.run()
    assert res["emitted"] == 1
    a = _cluster_alerts()[0]
    assert a["severity"] == "CRITICAL"
    assert json.loads(a["tags"])["distinct_members"] == 5


def test_mixed_cluster_flags_conflict_and_headline():
    _tx("A000001", "AAA", "purchase", D0)
    _tx("B000002", "AAA", "purchase", D1)
    _tx("C000003", "AAA", "sale", D2)      # disagreement → mixed
    res = rc.run()
    assert res["emitted"] == 1
    a = _cluster_alerts()[0]
    tags = json.loads(a["tags"])
    assert tags["direction"] == "mixed"                    # has_conflict basis
    assert "mixed" in a["headline"].lower()
    assert "cluster" in [t for t in tags["tags"]]


def test_dedup_same_identity_then_upgrade_on_new_member():
    # 3-member cluster fires once …
    _tx("A000001", "AAA", "purchase", D0)
    _tx("B000002", "AAA", "purchase", D1)
    _tx("C000003", "AAA", "purchase", D2)
    assert rc.run()["emitted"] == 1
    # … re-detecting the identical identity does NOT fire again …
    assert rc.run()["emitted"] == 0
    assert len(_cluster_alerts()) == 1
    # … a 4th member joining IS a new (upgrade) alert that supersedes the first.
    _tx("D000004", "AAA", "purchase", D1)
    res = rc.run()
    assert res["emitted"] == 1
    assert res["upgrades"] == 1
    alerts = _cluster_alerts()
    assert len(alerts) == 2
    first, second = alerts
    assert first["lifecycle_stage"] == "superseded"
    assert json.loads(second["tags"])["distinct_members"] == 4
    assert "expanded to 4" in second["headline"]


# ─────────────────────────────────────────────────────────────────────────────
# THE DISPOSAL SET — a missing sale type is SILENT
# ─────────────────────────────────────────────────────────────────────────────
# `sale_full` was absent from `_member_direction`'s disposal set. A member whose only
# trade was a full sale therefore classified as "other" and was dropped from consensus,
# so a genuine 3-member sell cluster containing one full-seller silently became a
# 2-member near-miss and never fired. Zero rows carry it today — prophylaxis, not a
# repair — but the scheduled House parser emits it for PTR code "S (full)".

def test_a_full_sale_is_a_disposal_not_other():
    assert rc._member_direction({"sale_full"}) == "sell"


@pytest.mark.parametrize("t,expected", [
    ("purchase", "buy"), ("sale", "sell"), ("sale_partial", "sell"),
    ("sale_full", "sell"), ("exchange", "other"),
])
def test_every_type_the_parser_emits_is_classified(t, expected):
    assert rc._member_direction({t}) == expected


def test_the_disposal_set_covers_every_type_the_parser_emits():
    """Closes the CLASS, not just the sale_full instance.

    `parse_house_pdfs.normalize_transaction_type` is the authority — it is what the
    scheduled parser writes into `transaction_type`. If a future disposal variant is added
    there and not here, that variant silently stops counting toward consensus and no other
    test notices. This reads the parser's own returns and requires each to be classified.
    """
    import re
    src = open(os.path.join(_REPO, "parse_house_pdfs.py")).read()
    body = src[src.index("def normalize_transaction_type"):]
    body = body[:body.index("\ndef ", 1)]
    emitted = set(re.findall(r'return "([a-z_]+)"', body))
    assert emitted, "could not read the parser's vocabulary — this guard has gone stale"
    assert "sale_full" in emitted, "fixture assumption broken: parser no longer emits sale_full"
    for t in emitted:
        d = rc._member_direction({t})
        if t.startswith("sale"):
            assert d == "sell", f"parser emits {t!r} but RULE_CLUSTER calls it {d!r}"
        elif t == "purchase":
            assert d == "buy"
        else:
            assert d == "other", f"{t!r} classified {d!r} — unexpected direction"


def test_a_full_seller_completes_a_three_member_sell_cluster():
    """End to end: the behaviour the missing type actually cost."""
    _tx("A000001", "FULLS", "sale", D0)
    _tx("B000002", "FULLS", "sale_partial", D1)
    _tx("C000003", "FULLS", "sale_full", D2)      # would have been "other" before

    rc.run()

    alerts = _cluster_alerts("FULLS")
    assert len(alerts) == 1, "a full seller did not complete the cluster"
    assert json.loads(alerts[0]["tags"])["distinct_members"] == 3


def test_an_exchange_still_does_NOT_count_toward_consensus():
    """The control — without it the above could pass by counting everything."""
    _tx("A000001", "EXCH", "sale", D0)
    _tx("B000002", "EXCH", "sale", D1)
    _tx("C000003", "EXCH", "exchange", D2)

    rc.run()

    assert _cluster_alerts("EXCH") == [], (
        "an exchange-only member was counted toward a sell consensus")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
