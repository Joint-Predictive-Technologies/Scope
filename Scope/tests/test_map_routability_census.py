#!/usr/bin/env python3
"""The shipped map export must be routable by the shipped map page.

🔴 THE FAILURE CLASS THIS GUARDS HAS HAPPENED TWICE, SILENTLY.  Block 11 shipped
338 `asset` mineral sites the graph plane dropped from every neighbourhood
(`node() -> None` for a type missing from `NODE_TYPES`); the navigability pass
found 6,213 picker rows printed by name with no way to open them.  Both times
the data was in the export and the frontend could not route it, and no test
noticed.  `serving/routability.py` now censuses every entity type the export
references against the intersection of what `graph_api` can return and what the
page's own `SYM` table draws, and `export_map.py` refuses to write an export
that fails it.  This file checks the artefacts that actually ship together —
`map-service/static/out/map-v1` and `map-service/static/osint_map.html` — so an
edit to either one that breaks the pairing fails here, without a database.

⚠️ NOTHING HERE READS THE EXPECTED VERDICT FROM THE MODULE UNDER TEST.  The
planted-defect fixture is written out as literals, and the real-export check
recomputes the census from the shipped rows and the shipped page rather than
trusting the manifest's own field — the manifest is then required to AGREE.
"""
from __future__ import annotations

import glob
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MAP_SERVICE = os.path.join(HERE, "..", "..", "map-service")
sys.path.insert(0, os.path.join(MAP_SERVICE, "serving"))

import routability                                   # noqa: E402
from map_sources import NODE_TYPES                   # noqa: E402

PAGE = os.path.join(MAP_SERVICE, "static", "osint_map.html")
EXPORT = os.path.join(MAP_SERVICE, "static", "out", "map-v1")

# a page carrying the two tables the frontend routes on, as hand-written literals
FIXTURE_PAGE = """<script>
var SYM={ person:d3.symbolCircle, company:d3.symbolSquare, asset:d3.symbolStar };
var SRC_TAG={contract:'CONTRACT', patent:'PATENT'};
</script>"""


def _shipped_signals():
    sigs = []
    for f in sorted(glob.glob(os.path.join(EXPORT, "county", "*.json"))):
        for c in json.load(open(f))["counties"].values():
            sigs.extend(c["signals"])
    nat = json.load(open(os.path.join(EXPORT, "national.json")))
    for st in nat["states"].values():
        sigs.extend(st.get("state_tier_signals", []))
    return sigs


# ── the planted defect, on an isolated fixture ────────────────────────────────

GRAPH_MAP = {"company": "company", "person": "person", "asset": "asset",
             "government_agency": "agency"}      # raw entity_type -> vocabulary, as literals


def _fixture_signals(holder_type):
    return [
        {"source": "contract", "seed": "E1", "seed_type": "company", "seed_raw_type": "company",
         "detail": [{"entity_id": "E1", "name": "ACME", "type": "company", "raw_type": "company"}],
         "detail_rest": [{"entity_id": "E2", "name": "ACME 2", "type": "company",
                          "raw_type": "company"}]},
        {"source": "commodity_msha", "seed": "S1", "seed_type": "asset", "seed_raw_type": "asset",
         "detail": [{"entity_id": "S1", "name": "MINE", "type": "asset", "raw_type": "asset",
                     "holders": [{"entity_id": "H1", "name": "HOLDER", "type": holder_type,
                                  "raw_type": holder_type}]}]},
    ]


def test_A_PLANTED_TYPE_THE_PAGE_CANNOT_OPEN_IS_CAUGHT_AND_REFUSED():
    table = routability.route_table(FIXTURE_PAGE)
    refs = routability.referenced(_fixture_signals("permit"))
    assert refs["types"] == {"company": 3, "asset": 2, "permit": 1}, refs   # E1 seed+row, E2 rest row; S1 seed+row; H1
    c = routability.census(refs, table, GRAPH_MAP, allowlist={})
    assert c["unroutable_types"] == ["permit"]
    assert c["missing_from_graph_api"] == ["permit"] and c["missing_from_frontend"] == []
    assert routability.refusal(c) is not None
    # the same defect placed where the page draws it but graph_api cannot, and
    # vice versa, is still caught — and the side that is missing is named
    c2 = routability.census(refs, table, dict(GRAPH_MAP, permit="permit"), allowlist={})
    assert c2["unroutable_types"] == ["permit"] and c2["missing_from_frontend"] == ["permit"]
    page2 = FIXTURE_PAGE.replace("asset:d3.symbolStar", "asset:d3.symbolStar, permit:d3.symbolWye")
    c3 = routability.census(refs, routability.route_table(page2), GRAPH_MAP, allowlist={})
    assert c3["unroutable_types"] == ["permit"] and c3["missing_from_graph_api"] == ["permit"]


def test_THE_TWO_SURVIVORS_A_VERIFIER_FOUND_ARE_CAUGHT():
    """(a) a raw type equal to a NODE_TYPES VALUE that is not a KEY — `agency` —
    is what graph_api.node() returns None for, and the first census passed it;
    (b) a row with a routable type and no renderable name is silently dropped
    by the page's picker.  Both refuse the export now."""
    refs = routability.referenced(_fixture_signals("agency"))
    c = routability.census(refs, routability.route_table(FIXTURE_PAGE), GRAPH_MAP, allowlist={})
    assert c["unroutable_types"] == ["agency"] and c["missing_from_graph_api"] == ["agency"]
    assert routability.refusal(c) is not None
    sigs = _fixture_signals("company")
    sigs[1]["detail"][0]["holders"][0]["name"] = "H1"          # the name IS the id
    refs = routability.referenced(sigs)
    c = routability.census(refs, routability.route_table(FIXTURE_PAGE), GRAPH_MAP, allowlist={})
    assert c["unroutable_types"] == [] and c["nameless_rows"] == 1
    assert routability.refusal(c) is not None
    # (c) the third survivor, from the second pass: an EMPTY entity_id is the
    # other half of the page's `if(!id || !name)` guard
    sigs = _fixture_signals("company")
    sigs[1]["detail"][0]["holders"][0]["entity_id"] = ""
    refs = routability.referenced(sigs)
    c = routability.census(refs, routability.route_table(FIXTURE_PAGE), GRAPH_MAP, allowlist={})
    assert c["idless_rows"] == 1 and c["nameless_rows"] == 0 and c["unroutable_types"] == []
    assert routability.refusal(c) is not None


def test_THE_TWO_WAYS_THROUGH_ARE_A_ROUTE_ON_BOTH_ENDS_OR_A_REASONED_ALLOWLIST():
    refs = routability.referenced(_fixture_signals("permit"))
    page2 = FIXTURE_PAGE.replace("asset:d3.symbolStar", "asset:d3.symbolStar, permit:d3.symbolWye")
    ok = routability.census(refs, routability.route_table(page2),
                            dict(GRAPH_MAP, permit="permit"), allowlist={})
    assert ok["unroutable_types"] == [] and routability.refusal(ok) is None
    allowed = routability.census(refs, routability.route_table(FIXTURE_PAGE), GRAPH_MAP,
                                 allowlist={"permit": "fixture: permits have no plane yet"})
    assert allowed["unroutable_types"] == []
    assert allowed["allowlisted"] == {"permit": "fixture: permits have no plane yet"}
    assert routability.refusal(allowed) is None
    # the live allow-list is empty, and every entry it ever gains needs a reason
    assert routability.ALLOWLIST == {}


def test_A_ROW_WITHOUT_A_TYPE_COUNTS_AGAINST_THE_EXPORT_NOT_FOR_IT():
    sigs = _fixture_signals("company")
    del sigs[1]["detail"][0]["holders"][0]["raw_type"]
    refs = routability.referenced(sigs)
    assert refs["untyped_rows"] == 1
    c = routability.census(refs, routability.route_table(FIXTURE_PAGE), GRAPH_MAP, allowlist={})
    assert c["unroutable_types"] == [] and routability.refusal(c) is not None


# ── the real shipped pair ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def shipped():
    if not os.path.isfile(PAGE) or not os.path.isfile(os.path.join(EXPORT, "manifest.json")):
        pytest.fail(f"the shipped pair is missing: {PAGE} / {EXPORT} — this test must see both")
    return {"page": open(PAGE, encoding="utf-8").read(),
            "manifest": json.load(open(os.path.join(EXPORT, "manifest.json"))),
            "signals": _shipped_signals()}


def test_THE_SHIPPED_PAGE_ROUTES_EVERY_TYPE_THE_SHIPPED_EXPORT_REFERENCES(shipped):
    table = routability.route_table(shipped["page"])
    refs = routability.referenced(shipped["signals"])
    c = routability.census(refs, table, NODE_TYPES)
    assert refs["untyped_rows"] == 0, "every shipped entity row must carry its raw type"
    assert refs["nameless_rows"] == 0, "every shipped entity row must carry a renderable name"
    assert refs["idless_rows"] == 0, "every shipped entity row must carry an entity_id"
    assert c["unroutable_types"] == [], c
    assert refs["types"], "the export references no entity at all?"
    for t in refs["types"]:
        assert (t in NODE_TYPES and NODE_TYPES[t] in c["node_route_table"]) or t in c["allowlisted"], t
    # every source the export ships is one the page can label
    assert c["unlabelled_sources"] == [], c["unlabelled_sources"]


def test_THE_MANIFEST_AGREES_WITH_A_CENSUS_RECOMPUTED_FROM_WHAT_SHIPPED(shipped):
    """The manifest's field is a disclosure; this recomputation is the check.
    A page edit that drops a symbol without a re-export, or a hand-edited
    manifest, fails here."""
    m = shipped["manifest"]
    table = routability.route_table(shipped["page"])
    refs = routability.referenced(shipped["signals"])
    c = routability.census(refs, table, NODE_TYPES)
    r = m["routability"]
    assert m["unroutable_types"] == c["unroutable_types"] == []
    assert r["node_route_table"] == c["node_route_table"]
    assert r["frontend_symbols"] == sorted(table["node_symbols"])
    assert r["source_tag_table"] == sorted(table["source_tags"])
    assert r["types_referenced"] == c["types_referenced"]
    assert r["untyped_rows"] == 0 and r["nameless_rows"] == 0 and r["idless_rows"] == 0
    assert r["ids_not_in_entities"] == 0
    assert r["frontend"]["md5"] == routability.md5_of(PAGE), (
        "the page changed since the export censused it — re-run export_map.py so the "
        "manifest describes the page that ships with it")


def test_DETAIL_REST_SHIPS_THE_WHOLE_REMAINDER_FOR_EVERY_SIGNAL(shipped):
    for s in shipped["signals"]:
        if s.get("detail_total") is None:
            continue
        assert len(s["detail"]) <= 12
        assert len(s["detail"]) + len(s.get("detail_rest", [])) == s["detail_total"], s["source"]
