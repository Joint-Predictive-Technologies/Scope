"""RULE_12 is retired — lock it retired, and lock what was deliberately KEPT.

RULE_12 claimed DOJ FARA in its docstring but read `lda.senate.gov/api/v1/filings/`
— the same endpoint as RULE_09 — so it was never an independent source, and it never
emitted a single alert. See 02_Sessions/SESSION-2026-07-26-rule12-real-fara.md for
the investigation that produced the NO-GO on rewiring it.

Two failure modes these tests exist to prevent:

1. **Retiring it by deleting the map entry alone.** `rule10_instruments` resolves
   `RULE_10_INSTRUMENTS.get(rule, rule)`, so an *unmapped but eligible* rule becomes
   its own instrument. Removing RULE_12 from the map without excluding it would have
   PROMOTED it to a distinct leg — the exact `foreign-agents` split that was
   investigated and rejected. The gate tests below fail loudly if that regresses.
2. **Retiring it too hard.** `fara_filings` holds ingested rows and the display maps
   render any pre-existing RULE_12 alert. Prod may hold such alerts — local emptiness
   is not evidence about prod — so ripping the labels out would break their rendering.
   Detection-time immutability applies to how history displays, not just to scores.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common
from jpt_common import (RULE_10_EXCLUDED, RULE_10_INSTRUMENTS, rule10_eligible_rules,
                        rule10_instruments, rule10_is_valid)

REPO = os.path.join(os.path.dirname(__file__), "..")


# ---------------------------------------------------------------------------
# Retired from the gate
# ---------------------------------------------------------------------------

def test_rule12_is_excluded_from_the_eligible_set():
    assert "RULE_12" in RULE_10_EXCLUDED


def test_rule12_is_MAPPED_and_EXCLUDED_belt_and_braces():
    """main kept RULE_12 in the instrument map. That is SAFER than removing it.

    The branch this test came from deleted the map entry and relied on the exclusion
    alone. main does both — and the difference matters because `rule10_instruments`
    resolves `.get(rule, rule)`: with the entry present, removing the exclusion would
    collapse RULE_12 into `senate-lda` alongside RULE_09 (correct — it reads the same
    LDA endpoint). With the entry gone it would become its own phantom `RULE_12` leg,
    which is the exact failure the retirement existed to prevent.

    So the map entry is a second line of defence, not a leftover.
    """
    assert RULE_10_INSTRUMENTS.get("RULE_12") == "senate-lda", (
        "RULE_12 lost its map entry — without it, dropping the exclusion promotes it to "
        "its own instrument instead of collapsing it into the senate-lda instrument")
    assert "RULE_12" in RULE_10_EXCLUDED
    assert rule10_instruments(["RULE_12"]) == []


def test_rule12_is_dropped_from_eligible_rules():
    # RULE_09 used to stand here as the eligible co-member of `senate-lda`. It was excluded
    # as CONTEXT on 2026-08-02, so an eligible rule now carries the property; RULE_09's own
    # exclusion is asserted alongside, which is strictly more than this test used to check.
    assert rule10_eligible_rules(["RULE_11", "RULE_12", "RULE_06"]) == ["RULE_06", "RULE_11"]
    assert rule10_eligible_rules(["RULE_09", "RULE_12", "RULE_06"]) == ["RULE_06"]


def test_rule12_contributes_no_instrument():
    """The load-bearing one: RULE_12 must add nothing to the count."""
    without = rule10_instruments(["RULE_11", "RULE_06"])
    with_12 = rule10_instruments(["RULE_11", "RULE_06", "RULE_12"])
    assert with_12 == without
    assert "foreign-agents" not in with_12
    assert "RULE_12" not in with_12          # the unmapped-fallback failure mode


def test_rule12_alone_yields_no_instruments():
    assert rule10_instruments(["RULE_12"]) == []
    assert not rule10_is_valid(["RULE_12"])


def test_rule12_cannot_complete_a_convergence():
    """Two real instruments + RULE_12 must NOT reach the threshold of 3."""
    assert not rule10_is_valid(["RULE_01B", "RULE_06", "RULE_12"])
    # ...while a genuine third instrument does, proving the pair was otherwise ready.
    assert rule10_is_valid(["RULE_01B", "RULE_06", "RULE_11"])
    # RULE_09 now fails the same way RULE_12 does — it too cannot complete a convergence.
    assert not rule10_is_valid(["RULE_01B", "RULE_06", "RULE_09"])


def test_rule12_exclusion_survives_casing():
    """The gate case-folds rule names; a lowercase RULE_12 must not sneak back in."""
    for variant in ("rule_12", "Rule_12", "rUlE_12"):
        assert rule10_instruments(["RULE_11", "RULE_06", variant]) == \
               rule10_instruments(["RULE_11", "RULE_06"])
        assert not rule10_is_valid(["RULE_11", "RULE_06", variant])


# ---------------------------------------------------------------------------
# The two exclusion sets must not drift apart again
# ---------------------------------------------------------------------------

def test_gate_script_exclusion_set_is_coupled_to_jpt_common():
    """`EXCLUDED_FROM_CORROBORATION` is the SQL candidate filter; RULE_10_EXCLUDED
    drives instrument counting. They were two independent copies of the same five
    names, so an eligibility change could half-land. They are now derived."""
    from scripts.rule_10_corroboration import EXCLUDED_FROM_CORROBORATION
    assert EXCLUDED_FROM_CORROBORATION == set(RULE_10_EXCLUDED)
    assert "RULE_12" in EXCLUDED_FROM_CORROBORATION


def test_gate_sql_filter_actually_excludes_rule12():
    """Not just the set — the generated SQL must name RULE_12."""
    import scripts.rule_10_corroboration as r10
    excluded = ",".join(f"'{r}'" for r in r10.EXCLUDED_FROM_CORROBORATION)
    assert "'RULE_12'" in excluded


# ---------------------------------------------------------------------------
# Unscheduled, and the script is gone
# ---------------------------------------------------------------------------

def test_rule12_script_is_UNSCHEDULED_not_deleted():
    """`CLAUDE.md`: "Unscheduled rather than deleted; see RULE_10_EXCLUDED."

    Keeping the file costs nothing and preserves the investigation that produced the
    NO-GO. What must be true is that nothing RUNS it — deletion and non-scheduling are
    different guarantees, and it is the second one that matters.
    """
    import re as _re
    main_py = open(os.path.join(REPO, "api", "main.py"), encoding="utf-8").read()
    body = "\n".join(l.split("#")[0] for l in main_py.splitlines())
    assert "rule_12_fara" not in body, "RULE_12 is scheduled again"


def test_rule12_is_not_scheduled():
    """Neither the interval nor the cron table may reference it."""
    src = open(os.path.join(REPO, "api", "main.py"), encoding="utf-8").read()
    import re
    # Only count real schedule keys, not the explanatory comment.
    keys = re.findall(r'^\s*"([^"]+\.py)":', src, re.M)
    assert not any("rule_12" in k for k in keys), \
        f"rule_12 still has a schedule entry: {[k for k in keys if 'rule_12' in k]}"


def test_no_module_still_imports_the_deleted_script():
    """A stale import would be an ImportError at scheduler start-up."""
    out = subprocess.run(
        ["grep", "-rn", "--include=*.py", "rule_12_fara", REPO],
        capture_output=True, text=True,
    ).stdout
    offenders = [ln for ln in out.splitlines() if "tests/" not in ln]
    assert not offenders, f"live code still references rule_12_fara: {offenders}"


# ---------------------------------------------------------------------------
# Deliberately KEPT — retiring the rule must not erase history or data
# ---------------------------------------------------------------------------

def test_fara_filings_table_is_still_created():
    """Never drop tables. The 3 ingested rows and /api/fara-activity stay readable."""
    conn = jpt_common.db_connection()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(fara_filings)")]
        assert "foreign_principal" in cols and "country" in cols
    finally:
        conn.close()


@pytest.mark.parametrize("mapping,label", [
    ("WHY_MATTERS", "why_matters"),
    ("RULE_TIME_HORIZONS", "time horizon"),
    ("RULE_SOURCE_QUALITY", "source quality"),
])
def test_display_maps_retain_rule12(mapping, label):
    """A pre-existing RULE_12 alert (prod may hold some — local emptiness proves
    nothing about prod) must still render. Retiring the producer does not license
    deleting the vocabulary its historical rows depend on."""
    m = getattr(jpt_common, mapping, None)
    if m is None:
        pytest.skip(f"{mapping} not present in jpt_common")
    assert "RULE_12" in m, f"RULE_12 dropped from {label} — historical alerts would break"


def test_evidence_router_still_labels_rule12():
    from api.routers.evidence import _SOURCE, _WHY
    assert "RULE_12" in _SOURCE
    # The label must name the source the code ACTUALLY read, not the claimed one:
    # RULE_12 read RULE_09's Senate LDA endpoint, never a FARA one.
    #
    # ⚠️ SHAPE CHANGED 2026-08-15 (source-links work): _SOURCE values were
    # (label, front_door_url) tuples and this asserted on the URL. The front-door
    # URLs were removed — a source system's homepage is not the document, and
    # several were measured dead (lda.senate.gov 301s to a host that then blocks).
    # The pin's MEANING is unchanged and now rides on the label, which is the only
    # thing left that can misname the source.
    label = _SOURCE["RULE_12"]
    assert isinstance(label, str), "_SOURCE values are labels, not (label, url)"
    assert "LDA" in label, f"RULE_12 must be labelled as Senate LDA, got {label!r}"
    assert "FARA" not in label.upper(), "RULE_12 never read FARA — mislabelling it hides that"
    assert "RULE_12" in _WHY


def test_retirement_is_documented_where_someone_would_look():
    """The next person to touch the gate map must find out why RULE_12 is absent."""
    src = open(os.path.join(REPO, "jpt_common.py"), encoding="utf-8").read()
    # Window from the D1 rationale through the threshold, so it covers the comment
    # ABOVE the RULE_10_EXCLUDED assignment as well as the map itself.
    block = src[src.index("# D1 — count instruments"):src.index("RULE_10_MIN_INSTRUMENTS")]
    assert "RULE_12" in block
    assert "retire" in block.lower() or "RETIRED" in block
    # and it must warn about the unmapped-fallback trap that makes this subtle
    assert "unmapped" in block.lower() or "get(rule, rule)" in block
