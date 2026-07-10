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
from jpt_common import resolve_org  # noqa: E402

_c = TestClient(app)


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
    d = _c.get("/api/lobbying/entity?q=AIPAC").json()
    assert d["entity"]["resolved"] is True
    assert d["lobbying"]["available"] is True
    assert d["lobbying"]["total_spend"] > 0


def test_campaign_finance_labeled_not_fabricated():
    """Campaign finance must be a separate, honestly-labeled category."""
    d = _c.get("/api/lobbying/entity?q=AIPAC").json()
    assert d["campaign_finance"]["available"] is False
    assert d["campaign_finance"]["category"].startswith("FEC")
    assert d["foreign_principal"]["available"] is False


def test_partial_year_yoy_omitted():
    d = _c.get("/api/lobbying/entity?q=AIPAC").json()
    lob = d["lobbying"]
    # Either a real YoY on complete years, or an explicit partial-year note.
    assert lob["yoy_pct"] is not None or lob["yoy_note"]


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
