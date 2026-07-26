#!/usr/bin/env python3
"""
Integration tests for influence-entity resolution (§7/§13/§14).

Runs under pytest or standalone:  python3 tests/test_influence_entity.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402
from jpt_common import db_connection, resolve_org  # noqa: E402

_c = TestClient(app)

# ── lobbying fixture ──────────────────────────────────────────────────────────
# These tests used to depend on real `lobbying_filings` rows that happened to sit
# in the working database, so they passed only while the suite read production
# data. They now seed their own filings.
#
# The entity itself stays AIPAC on purpose: `resolve_org` matches against an
# in-code alias registry (jpt_common), not against the database — proven by
# test_aipac_alias_resolves_to_canonical, which passes on an empty DB. Only the
# *filings* were ever a prod dependency, so only those are seeded.
#
# /api/lobbying/entity matches on `client_name LIKE '%<canonical>%'`
# (api/routers/lobbying.py:82-96), and derives YoY from per-year totals, treating
# a latest year with fewer than 3 filings as partial (`:114-120`).

_CANONICAL = "American Israel Public Affairs Committee"


def _seed_lobbying(rows):
    """rows: list of (filing_year, period, amount, issues). Returns total spend."""
    conn = db_connection()
    for i, (year, period, amount, issues) in enumerate(rows):
        conn.execute(
            """INSERT INTO lobbying_filings
                   (filing_uuid, client_name, registrant_name, amount,
                    filing_year, period, issues, category)
               VALUES (?,?,?,?,?,?,?,'lobbying')""",
            (f"test-uuid-{i}", _CANONICAL, "Test Registrant LLP",
             amount, year, period, issues))
    conn.commit(); conn.close()
    return round(sum(r[2] for r in rows), 2)


def test_aipac_alias_resolves_to_canonical():
    rec, conf, by = resolve_org("AIPAC")
    assert rec and rec["canonical"] == "American Israel Public Affairs Committee"
    assert conf == 100 and by == "alias"


def test_canonical_and_partial_resolve():
    assert resolve_org("american israel public affairs committee")[1] == 100
    assert resolve_org("israel public affairs")[0] is not None  # partial/alias


def test_unknown_org_does_not_resolve():
    assert resolve_org("totally made up org 12345") == (None, 0, None)


def test_entity_endpoint_lobbying_available():
    total = _seed_lobbying([
        (2024, "first_quarter",  100_000.0, "DEFENSE, FOREIGN RELATIONS"),
        (2024, "second_quarter", 150_000.0, "FOREIGN RELATIONS"),
        (2024, "third_quarter",  250_000.0, "DEFENSE"),
    ])
    d = _c.get("/api/lobbying/entity?q=AIPAC").json()
    assert d["entity"]["resolved"] is True
    assert d["lobbying"]["available"] is True
    assert d["lobbying"]["total_spend"] == total == 500_000.0
    assert d["lobbying"]["filing_count"] == 3
    # issues are aggregated from the seeded filings, not invented
    assert {i["issue"] for i in d["lobbying"]["top_issues"]} == {"DEFENSE", "FOREIGN RELATIONS"}


def test_campaign_finance_labeled_not_fabricated():
    """Campaign finance must be a separate, honestly-labeled category."""
    d = _c.get("/api/lobbying/entity?q=AIPAC").json()
    assert d["campaign_finance"]["available"] is False
    assert d["campaign_finance"]["category"].startswith("FEC")
    assert d["foreign_principal"]["available"] is False


def test_partial_year_yoy_omitted():
    # 2024 complete (3 filings), 2025 partial (1 filing) -> YoY must be omitted
    # with an explicit note rather than computed off half a year.
    _seed_lobbying([
        (2024, "first_quarter",  100_000.0, "DEFENSE"),
        (2024, "second_quarter", 100_000.0, "DEFENSE"),
        (2024, "third_quarter",  100_000.0, "DEFENSE"),
        (2025, "first_quarter",   50_000.0, "DEFENSE"),
    ])
    lob = _c.get("/api/lobbying/entity?q=AIPAC").json()["lobbying"]
    assert lob["yoy_pct"] is None, "YoY must not be computed off a partial year"
    assert lob["yoy_note"] and "partial year" in lob["yoy_note"]
    assert "2025" in lob["yoy_note"]


def test_yoy_computed_on_complete_years():
    # Both years complete (3+ filings) -> a real YoY, not a note.
    _seed_lobbying([
        (2024, "first_quarter",  100_000.0, "DEFENSE"),
        (2024, "second_quarter", 100_000.0, "DEFENSE"),
        (2024, "third_quarter",  100_000.0, "DEFENSE"),
        (2025, "first_quarter",  200_000.0, "DEFENSE"),
        (2025, "second_quarter", 100_000.0, "DEFENSE"),
        (2025, "third_quarter",  300_000.0, "DEFENSE"),
    ])
    lob = _c.get("/api/lobbying/entity?q=AIPAC").json()["lobbying"]
    # 300k -> 600k = +100.0%
    assert lob["yoy_pct"] == 100.0
    assert lob["yoy_note"] is None


def test_search_surfaces_orgs():
    d = _c.get("/api/search?q=aipac").json()
    assert "orgs" in d
    assert any("israel" in o["name"].lower() for o in d["orgs"])


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
