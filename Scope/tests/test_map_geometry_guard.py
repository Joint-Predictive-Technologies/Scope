#!/usr/bin/env python3
"""The export's geometry guard — and the Connecticut defect it was written for.

🔴 WHY THIS EXISTS.  Connecticut replaced its county-equivalents with nine
PLANNING REGIONS in 2022 (FIPS 09110-09190).  The export keyed on those, because
that is what the contract data reports.  The page's geometry
(`us-atlas@3/counties-10m`) still carries the pre-2022 counties 09001-09015.
The intersection was EMPTY, so `renderCounties` read `cd[f.id]` as undefined for
every Connecticut polygon and drew all eight as `no-coverage` hatching:
**RTX, Sikorsky and Electric Boat — five real signals across three counties —
rendered as "no source reached this county".**

A false negative presented as a coverage fact is worse than a crash, because it
looks like an answer.  Nothing detected it for the life of the export.

⚠️ THE CONTROL IS THE POINT.  A guard that only ever passes is not a guard, so
this pins BOTH directions: the current export must pass, and each of three
sabotages must make it refuse — including a replay of the exact pre-fix world.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[2]
SERVING = REPO / "map-service" / "serving"
EXPORT_DIR = REPO / "map-service" / "static" / "out" / "map-v1" / "county"


def _export_map():
    spec = importlib.util.spec_from_file_location(
        "export_map_under_test", SERVING / "export_map.py")
    mod = importlib.util.module_from_spec(spec)
    old = os.getcwd()
    os.chdir(SERVING)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:                    # argparse at import time, if any
        pass
    finally:
        os.chdir(old)
    return mod


@pytest.fixture(scope="module")
def em():
    return _export_map()


@pytest.fixture(scope="module")
def county_keys():
    keys = set()
    for p in sorted(EXPORT_DIR.glob("*.json")):
        keys |= set(json.loads(p.read_text())["counties"])
    assert keys, "no county export on disk to check the guard against"
    return keys


def test_drawable_set_loads_and_covers_connecticut(em):
    ids = em.renderable_county_ids()
    assert len(ids) > 3000, f"only {len(ids)} drawable ids — file truncated?"
    ct = sorted(i for i in ids if i.startswith("09"))
    assert ct == ["09110", "09120", "09130", "09140", "09150",
                  "09160", "09170", "09180", "09190"], ct
    # the obsolete counties must be GONE, or both vintages would pass and the
    # guard could never catch a re-keyed export
    assert not any(i in ids for i in ("09001", "09003", "09015"))


def test_control_current_export_passes(em, county_keys):
    """CONTROL. The export on disk must not trip the guard."""
    assert em.geometry_refusal(county_keys) is None


def test_sabotage_replays_the_connecticut_defect(em, county_keys, monkeypatch):
    """The pre-fix world: drawable set carries the OLD Connecticut counties."""
    pre_fix = {i for i in em.renderable_county_ids() if not i.startswith("09")}
    pre_fix |= {"09001", "09003", "09005", "09007",
                "09009", "09011", "09013", "09015"}
    monkeypatch.setattr(em, "renderable_county_ids", lambda: pre_fix)
    why = em.geometry_refusal(county_keys)
    assert why is not None, "the guard did not catch the defect it was written for"
    for fips in ("09110", "09120", "09180"):
        assert fips in why, f"{fips} not named in the refusal: {why}"


def test_sabotage_unknown_fips(em, county_keys):
    why = em.geometry_refusal(county_keys | {"99999"})
    assert why is not None and "99999" in why


def test_sabotage_vintage_drift_elsewhere(em, county_keys):
    """Not Connecticut-specific: Alaska has changed borough FIPS too."""
    why = em.geometry_refusal(county_keys | {"02063", "02066"})
    assert why is not None and "02063" in why


def test_missing_drawable_file_refuses_rather_than_passes(em, county_keys, monkeypatch):
    """An absent file must REFUSE, never silently allow — the failure mode that
    would quietly disable the guard forever."""
    def boom():
        raise FileNotFoundError
    monkeypatch.setattr(em, "renderable_county_ids", boom)
    why = em.geometry_refusal(county_keys)
    assert why is not None and "missing" in why.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 🔴 THE THREE TESTS ABOVE ALL CALL `geometry_refusal` DIRECTLY, AND A VERIFIER
# SHOWED THAT IS NOT ENOUGH: two mutants survived the whole suite.
#
#   Mutant B  delete the call from `main()`  ->  24 passed, export ships unguarded
#   Mutant D  drop a region from the page's splice and refresh the manifest md5
#             ->  24 passed, and Electric Boat's county draws as `no-coverage`
#                 again — the exact defect this guard exists to prevent
#
# A guard whose WIRING is untested is a guard that can be removed in two lines
# without a single test going red.  These pin the wiring and the binding.
# ─────────────────────────────────────────────────────────────────────────────

PAGE = REPO / "map-service" / "static" / "osint_map.html"
SNAPSHOT = pathlib.Path.home() / "scope-backups" / "2026-09-03-serving-snapshot" / "osint-map-serving.db"


def _splice_ids():
    """The FIPS the PAGE actually splices in, parsed from the shipped bytes."""
    import re
    src = PAGE.read_text()
    m = re.search(r"var CT_PLANNING_REGIONS=(\{.*?\});\n", src, re.S)
    assert m, "the page no longer carries a CT_PLANNING_REGIONS splice"
    return {f["id"] for f in json.loads(m.group(1))["features"]}


def test_drawable_set_is_bound_to_what_the_page_splices(em):
    """MUTANT D. Drop a region from the page and this must go red.

    `map_geometry_ids.txt` is an assertion ABOUT the page. Nothing else compares
    the two, so without this the file can keep promising a region the page no
    longer draws — and the guard would keep passing while a county went dark."""
    page_ct = _splice_ids()
    file_ct = {i for i in em.renderable_county_ids() if i.startswith("09")}
    assert page_ct == file_ct, (
        f"the page splices {sorted(page_ct)} but the drawable set claims "
        f"{sorted(file_ct)} — regenerate serving/{em.GEOMETRY_IDS_FILE}")


def test_the_export_actually_carries_the_signal_counties(em):
    """The three Connecticut counties that were dark must be in the page's splice
    — not merely in the file that describes it."""
    assert {"09110", "09120", "09180"} <= _splice_ids()


def test_empty_key_set_refuses_rather_than_passing_vacuously(em):
    assert em.geometry_refusal(set()) is not None


@pytest.mark.skipif(not SNAPSHOT.exists(), reason=f"serving snapshot absent: {SNAPSHOT}")
def test_MUTANT_B_main_actually_calls_the_guard(tmp_path, monkeypatch):
    """MUTANT B. Run `main()` end to end against a drawable set that omits
    Connecticut; it must exit 2 and write NOTHING.

    Calling `geometry_refusal` in a unit test proves the function works. It does
    not prove `main()` asks it. Deleting that one line left every other test in
    this file green."""
    import subprocess, sys as _s, textwrap
    out = tmp_path / "out"
    prog = textwrap.dedent(f"""
        import sys, importlib.util
        sys.path.insert(0, {str(SERVING)!r})
        spec = importlib.util.spec_from_file_location("em", {str(SERVING / 'export_map.py')!r})
        em = importlib.util.module_from_spec(spec); spec.loader.exec_module(em)
        real = em.renderable_county_ids
        em.renderable_county_ids = lambda: {{i for i in real() if not i.startswith("09")}}
        sys.argv = ["export_map.py", "--db", {str(SNAPSHOT)!r}, "--out", {str(out)!r},
                    "--frontend", {str(PAGE)!r}]
        em.main()
    """)
    r = subprocess.run([_s.executable, "-c", prog], capture_output=True, text=True, timeout=600)
    assert r.returncode == 2, (
        "main() did not refuse an export the page cannot draw — the guard is not "
        f"wired in.\nstdout tail:\n{r.stdout[-1500:]}\nstderr tail:\n{r.stderr[-800:]}")
    assert "EXPORT REFUSED" in r.stdout
    written = list(out.rglob("*.json")) if out.exists() else []
    assert not written, f"refused, but still wrote {written[:5]}"
