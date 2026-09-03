#!/usr/bin/env python3
"""The ledger's pure model, run under node from the shipped page — because the
live export cannot falsify it.

🔴 A VERIFIER SHOWED THE "MIN, NOT MEAN" SPOT-CHECK COULD NOT FAIL.  Across the
whole export only 3 dots hold a family with more than one signal, and all three
carry 1.0 / 1.0 — MIN, mean, first and max coincide on every real dot.  So the
frontend's `familyConfidence` is pinned here on a fixture whose signals
DISAGREE, the same way the exporter pins its own MIN.  The same verifier found a
regression the five anchors were structurally unable to see (one entity named by
two families rendered two trace buttons); that cell of the matrix is a fixture
here too.

The functions are sliced VERBATIM out of `map-service/static/osint_map.html`
(the page is `function boot(){}`, nothing is global) and executed by node.  If
node is not on this machine the test is SKIPPED with that reason — a skip is
visible in the run, an absent test is not.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "..", "..", "map-service", "static", "osint_map.html")
NAMES = ["SRC_TAG", "money", "esc", "famOf", "entityIndex", "dotEntities", "dotHidden",
         "noEntityNote", "loadedFamilies", "familySourceLabels", "sourceBadge",
         "familyConfidence", "restNoun", "namedElsewhere", "ledgerModel", "pageSection"]

MANIFEST = {
    "source_families": {"contract": "contract", "patent": "patent", "demand": "demand",
                        "commodity_msha": "commodity", "commodity_eia": "commodity"},
    "sources": {k: {"label": k + " label"} for k in
                ("contract", "patent", "demand", "commodity_msha", "commodity_eia")},
    "defects": {"by_source": {"patent": 1}},
    "commodity_defects": {"by_source": {"commodity_msha": 314}},
    "coverage": {"any_source_sweeps_geography": False},
}


def _slice(src, name):
    i = src.index("function %s(" % name)
    j = src.index("{", i)
    d, k = 0, j
    while True:
        if src[k] == "{":
            d += 1
        elif src[k] == "}":
            d -= 1
            if d == 0:
                break
        k += 1
    return src[i:k + 1]


def _module():
    src = open(PAGE, encoding="utf-8").read()
    parts = []
    for n in NAMES:
        if n == "SRC_TAG":
            i = src.index("var SRC_TAG=")
            parts.append(src[i:src.index("};", i) + 2])
        else:
            parts.append(_slice(src, n))
    return "\n".join(parts) + "\nmodule.exports={" + ",".join(NAMES) + "};\n"


def _run(county, page_sections=False):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed here; the ledger model cannot be executed")
    js = _module() + """
global.MANIFEST=%s;
const P=module.exports; const c=%s;
const m=P.ledgerModel(c);
const before=m.sections.map(s=>({family:s.family,rows:s.rows.map(e=>e.uid),trace:s.trace,
  min:s.confidence.min,unmeasured:s.confidence.unmeasured,badges:s.badges.map(b=>b.text),
  rest:s.rest.length,unshipped:s.unshipped}));
let paged=[]; if(%s){ for(const s of m.sections){ while(s.restCursor<s.rest.length){ const r=P.pageSection(m,s,12); paged.push({family:s.family,made:r.made.map(e=>e.uid),already:r.already,left:r.left}); } } }
console.log(JSON.stringify({entities:m.entities.map(e=>({uid:e.uid,name:e.name,sources:e.sources,note:e.note})),
  sections:before, after:m.sections.map(s=>({family:s.family,rows:s.rows.map(e=>e.uid)})),
  absent:m.absent, loaded:m.loaded, hidden:m.hidden, paged}));
""" % (json.dumps(MANIFEST), json.dumps(county), "true" if page_sections else "false")
    r = subprocess.run([node, "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _sig(source, conf, detail, **kw):
    s = {"source": source, "confidence": conf, "events": len(detail), "tier": 1,
         "ev": "direct", "clean": True, "verification": "hardened", "seed": None,
         "detail": detail, "detail_total": len(detail)}
    s.update(kw)
    return s


def test_FAMILY_CONFIDENCE_IS_MIN_ON_SIGNALS_THAT_DISAGREE():
    """MSHA 0.30 and EIA 0.90 in one family: MIN 0.30, mean 0.60, first 0.90,
    max 0.90 — four different numbers, so only one rule passes."""
    county = {"name": "Fx", "signals": [
        _sig("commodity_eia", 0.9, [{"entity_id": "S1", "name": "EIA MILL", "precision": "county",
                                     "holders": []}]),
        _sig("commodity_msha", 0.3, [{"entity_id": "S2", "name": "MSHA MINE",
                                      "precision": "coordinate", "holders": []}], clean=False),
        _sig("patent", None, [{"entity_id": "P", "name": "PAT CO", "patents": 2}], clean=False),
    ]}
    out = _run(county)
    fam = {s["family"]: s for s in out["sections"]}
    assert fam["commodity"]["min"] == 0.3 and fam["commodity"]["unmeasured"] == 0
    assert fam["commodity"]["badges"] == ["clean", "open defect · 314 commodity rows"]
    # a null confidence is unknown, not zero: excluded and counted
    assert fam["patent"]["min"] is None and fam["patent"]["unmeasured"] == 1
    assert fam["patent"]["badges"] == ["open defect · 1 name"]
    assert [s["family"] for s in out["sections"]] == ["commodity", "patent"]   # signal order
    assert out["absent"] == ["contract", "demand"]


def test_ONE_ENTITY_NAMED_BY_TWO_FAMILIES_GETS_EXACTLY_ONE_TRACE_BUTTON():
    """Lewis County KY's shape: demand and patent both name the one entity.
    The button belongs to the section that holds the row; the other section
    holds zero rows and no button."""
    county = {"name": "Fx", "signals": [
        _sig("demand", 0.05, [{"entity_id": "E", "name": "ONE CO", "demand": "d",
                               "match_type": "category", "evidence": "x"}]),
        _sig("patent", 0.9, [{"entity_id": "E", "name": "ONE CO", "patents": 3}], clean=False),
    ]}
    out = _run(county)
    assert [e["uid"] for e in out["entities"]] == ["E"]
    assert out["entities"][0]["sources"] == ["demand", "patent"]
    assert out["entities"][0]["note"] == "3 patents"          # the empty demand note is filled
    traces = [(s["family"], s["trace"], len(s["rows"])) for s in out["sections"]]
    assert traces == [("demand", True, 1), ("patent", False, 0)]
    assert out["absent"] == ["contract", "commodity"]


def test_A_ZERO_ENTITY_DOT_HAS_A_SECTION_AND_NOT_REACHED_AND_NO_BUTTON():
    county = {"name": "Fx", "signals": [
        _sig("patent", 0.9, [], events=1, roles={"inventor": 1}, clean=False)]}
    out = _run(county)
    assert out["entities"] == []
    assert [(s["family"], s["trace"], s["rows"]) for s in out["sections"]] == [("patent", False, [])]
    assert out["absent"] == ["contract", "demand", "commodity"]


def test_PAGING_DEDUPS_AGAINST_EVERY_SECTION_AND_CLOSES_EXACTLY():
    """Rest rows naming an entity already listed under another family create no
    row and are counted; the rest of the remainder lands in its own family."""
    county = {"name": "Fx", "signals": [
        _sig("demand", 0.05, [{"entity_id": "A", "name": "A CO", "demand": "d",
                               "match_type": "category", "evidence": "x"}]),
        _sig("patent", 0.9, [{"entity_id": "P%d" % i, "name": "P%d CO" % i, "patents": 20 - i}
                             for i in range(12)],
             detail_rest=[{"entity_id": "A", "name": "A CO", "patents": 7},
                          {"entity_id": "P12", "name": "P12 CO", "patents": 6},
                          {"entity_id": "P1", "name": "P1 CO", "patents": 5}],
             detail_total=15, clean=False),
    ]}
    out = _run(county, page_sections=True)
    assert out["hidden"] == 3
    assert out["sections"][1]["rest"] == 3 and out["sections"][1]["unshipped"] == 0
    assert out["paged"] == [{"family": "patent", "made": ["P12"], "already": 2, "left": 0}]
    after = {s["family"]: s["rows"] for s in out["after"]}
    assert after["demand"] == ["A"] and after["patent"][-1] == "P12" and len(after["patent"]) == 13
    uids = [u for s in out["after"] for u in s["rows"]]
    assert len(uids) == len(set(uids)) == 14
