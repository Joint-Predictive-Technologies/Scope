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


def _run(county, page_sections=False, manifest=None):
    """`manifest` overrides the shared fixture for one test.

    🔴 EXTENDING THE SHARED MANIFEST BROKE THREE TESTS AND THE MUTATION CONTROL
    CAUGHT IT.  Adding a sixth family changed `loaded`/`absent` counts that three
    existing tests pin by number, so a fixture edit made for one test silently
    rewrote the premise of the others. A test that needs a different corpus gets
    its own corpus."""
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
console.log(JSON.stringify({entities:m.entities.map(e=>({uid:e.uid,name:e.name,sources:e.sources,note:e.note,
  alts:(e.alts||[]).map(a=>a.name), awardedAs:e.awardedAs||null, sited:!!e.sited})),
  sections:before, after:m.sections.map(s=>({family:s.family,rows:s.rows.map(e=>e.uid)})),
  absent:m.absent, loaded:m.loaded, hidden:m.hidden, paged}));
""" % (json.dumps(manifest or MANIFEST), json.dumps(county), "true" if page_sections else "false")
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


# ══════════════════════════════════════════════════════════════════════════════
# the two fixes PORTED onto this lineage from the navigability build, 2026-09-03
#
# ⚠️ BOTH EXISTED ON THE LIVE PAGE AND NOT ON THIS ONE.  The ledger elevation and
# the navigability fixes were written in PARALLEL sessions against the same file,
# and each ended up holding something the other lacked.  Deploying this lineage
# would have REGRESSED both behaviours against what users already see — which is
# why they are ported and pinned here instead of left as a recorded divergence.
# ══════════════════════════════════════════════════════════════════════════════

def test_A_THIRD_DIFFERING_NAME_IS_KEPT_AND_NOT_SILENTLY_DROPPED():
    """🔴 Two NON-contract sources naming one node differently.

    The contract branches keep an `awardedAs` and nothing else, so a conflict
    between two non-contract sources fell through both and the second name was
    DROPPED — the row then asserted one identity for a click that opens a node
    another source calls something else. `demand_signals` carries the identical
    stray-name shape, masked only by an unrelated `match_type` filter, so this is
    a live hazard and not a hypothetical one.

    Every conflict branch must degrade to SHOWING BOTH names."""
    out = _run({"signals": [
        _sig("patent", 0.9, [{"entity_id": "E1", "name": "ACME PATENTS LLC", "patents": 3}]),
        _sig("demand", 0.5, [{"entity_id": "E1", "name": "ACME CORPORATION"}]),
    ]})
    e = out["entities"][0]
    assert e["name"] == "ACME PATENTS LLC"
    assert e["alts"] == ["ACME CORPORATION"], e


def test_THE_ORIGINAL_CONTRACT_NAME_RULE_IS_UNTOUCHED_BY_THE_PORT():
    """The canonical name still wins and the awarded string is still kept beside
    it — a port that fixed one case by breaking the case that already worked
    would be the "fix that changes the symptom" this campaign keeps catching."""
    out = _run({"signals": [
        _sig("contract", 0.95, [{"entity_id": "G1", "name": "BATH IRON WORKS CORPORATION",
                                 "amount": 5, "date": "2024-01-01", "description": "x"}]),
        _sig("patent", 0.9, [{"entity_id": "G1", "name": "GENERAL DYNAMICS CORP", "patents": 2}]),
    ]})
    g = out["entities"][0]
    assert g["name"] == "GENERAL DYNAMICS CORP"
    assert g["awardedAs"] == "BATH IRON WORKS CORPORATION"
    assert g["alts"] == [], "the awarded name belongs in awardedAs, not in alts"


def test_THE_COORDINATE_FOOTER_READS_THE_DATUM_NOT_THE_RENDERED_NOTE():
    """🔴 A site carrying its own coordinate, named FIRST by another source.

    The footer decided "does any row carry its own position" by regexing the
    rendered `note` for `· coordinate`. That couples it to first-note-wins: a
    coordinate-tier site some other source names first keeps THAT source's note,
    loses its precision from the display, and drops out of the test — so the panel
    tells the reader every row shares a county when one does not.

    The precision is a DATUM and has to be carried as one."""
    out = _run({"signals": [
        _sig("patent", 0.9, [{"entity_id": "S1", "name": "BIG MINE HOLDINGS", "patents": 2}]),
        _sig("commodity_msha", 1.0, [{"entity_id": "S1", "name": "BIG MINE HOLDINGS",
                                      "precision": "coordinate", "holders": []}]),
    ]})
    e = out["entities"][0]
    # the note is the PATENT's, so a regex over it finds nothing...
    assert e["note"] == "2 patents", e
    assert "coordinate" not in e["note"]
    # ...and the datum still says the row is positioned
    assert e["sited"] is True, e


def test_A_REPEATED_ALT_NAME_IS_LISTED_ONCE():
    """The de-dupe guard on `alts`, which the first version of these tests could
    not fail: with only two sources the guard is never reached a second time, so
    deleting it changed nothing any test could see. Three sources, two of which
    volunteer the SAME differing name, is the smallest fixture that exercises it."""
    out = _run({"signals": [
        _sig("patent", 0.9, [{"entity_id": "E1", "name": "ACME PATENTS LLC", "patents": 3}]),
        _sig("demand", 0.5, [{"entity_id": "E1", "name": "ACME CORPORATION"}]),
        _sig("commodity_msha", 0.8, [{"entity_id": "E1", "name": "ACME CORPORATION",
                                      "precision": "county", "holders": []}]),
    ]})
    e = out["entities"][0]
    assert e["name"] == "ACME PATENTS LLC"
    assert e["alts"] == ["ACME CORPORATION"], "one name, listed once — not once per source"


def test_A_SITE_THAT_NAMES_ITSELF_FIRST_IS_STILL_SITED():
    """🔴 The common case, and the one the first version of this suite missed.

    The footer test named the site by a PATENT first, so `sited` was only ever set
    on the merge path (`if(sited) seen[id].sited=true`). The constructor's own
    `sited:!!sited` was therefore unpinned, and breaking it — a mineral site that
    is the first source to name its own entity — left every test green while the
    footer went back to claiming the rows share a county."""
    out = _run({"signals": [
        _sig("commodity_msha", 1.0, [{"entity_id": "S1", "name": "BIG MINE HOLDINGS",
                                      "precision": "coordinate", "holders": []}]),
        _sig("patent", 0.9, [{"entity_id": "S1", "name": "BIG MINE HOLDINGS", "patents": 2}]),
    ]})
    e = out["entities"][0]
    assert e["note"] == "mineral site · coordinate", e
    assert e["sited"] is True, e


def test_AN_OIL_GAS_WELL_IS_NOT_CALLED_A_MINERAL_SITE():
    """🔴 MSHA, EIA and ND oil/gas are three families that are NEVER merged — a
    load-bearing export rule. Both mineral sites and wells reach the same branch
    of `dotEntities` because both carry `holders`, so every NDIC well rendered
    "mineral site · coordinate": MSHA's noun on an oil well. The export keeps the
    families apart and the ledger's own prose was quietly rejoining them.

    The noun is derived from the FAMILY, so a new family cannot inherit another's
    noun by matching the same branch."""
    mani = json.loads(json.dumps(MANIFEST))
    mani["source_families"]["oil_gas_nd"] = "oil_gas"
    mani["sources"]["oil_gas_nd"] = {"label": "oil_gas_nd label"}
    out = _run({"signals": [
        _sig("oil_gas_nd", 1.0, [{"entity_id": "W1", "name": "ABELMANN 23-14 1-H",
                                  "precision": "coordinate",
                                  "holders": [{"entity_id": "OP1", "name": "DEVON ENERGY",
                                               "rel": "contractor"}]}]),
        _sig("commodity_msha", 1.0, [{"entity_id": "S1", "name": "BIG MINE",
                                      "precision": "coordinate", "holders": []}]),
    ]}, manifest=mani)
    by = {e["uid"]: e for e in out["entities"]}
    assert by["W1"]["note"] == "well · coordinate", by["W1"]
    assert by["S1"]["note"] == "mineral site · coordinate", by["S1"]
    # the operator still hangs off the well as its own openable row
    assert by["OP1"]["note"] == "holder — contractor", by["OP1"]
