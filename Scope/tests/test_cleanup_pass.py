"""Cleanup pass — the scheduler path bug, and phantom-safe retirement of 12/13/14."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jpt_common
from jpt_common import RULE_10_EXCLUDED, RULE_10_INSTRUMENTS, rule10_instruments, rule10_is_valid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RETIRED = ("RULE_12", "RULE_13", "RULE_14")
# Gate-eligible, not merely scheduled. RULE_08 left this tuple 2026-07-29 when it was
# excluded as a basket rule (its ticker comes from SECTOR_MAP, not from the document); it
# still runs and still emits. See tests/test_basket_rule_gate_class.py for the guarantee
# and tests/test_exclusion_single_source.py for its positive exclusion assertion.
LIVE = ("RULE_01B", "RULE_02", "RULE_CLUSTER", "RULE_06",
        "RULE_09", "RULE_11", "RULE_15", "RULE_16")


# --- Stage 1: every scheduled script must resolve -------------------------

def test_every_scheduled_script_path_resolves():
    """The 06:30 LLM brief was scheduled as "generate_brief.py" while the file lives at
    scripts/generate_brief.py, so _run_rule's subprocess raised FileNotFoundError every
    day and the brief never generated. This asserts the whole class, not just that one."""
    import api.main as m
    missing = [s for s in [*m._RULE_SCHEDULE, *m._CRON_SCHEDULE]
               if not os.path.exists(os.path.join(REPO, s))]
    assert not missing, f"scheduled scripts with unresolvable paths: {missing}"


def test_the_dead_brief_job_is_deleted_not_repointed():
    """It failed 100% of runs and morning_brief.py already owns 06:30."""
    import api.main as m
    sched = [*m._RULE_SCHEDULE, *m._CRON_SCHEDULE]
    assert not [s for s in sched if "generate_brief" in s], "the dead job is back"
    assert "scripts/morning_brief.py" in m._CRON_SCHEDULE, "the real brief job vanished"


def _accepts_emit_alerts(script: str) -> bool:
    """Would the script survive the flag the scheduler ALWAYS passes?

    STATIC, deliberately: actually executing every scheduled rule would hit live APIs
    and the DB. And a `--help` probe does NOT work — argparse handles -h and exits
    before it ever reports unrecognized arguments, so such a probe reports success for
    a script that fails in production (my first version of this test did exactly that
    and returned an empty broken-list).

    A script is safe if it declares the flag, or if it never parses strictly.
    """
    import ast
    src = open(os.path.join(REPO, script), encoding="utf-8").read()
    tree = ast.parse(src)
    declared, strict = False, False
    for node in ast.walk(tree):
        # a REAL add_argument("--emit-alerts", ...) — a docstring mention is not enough.
        # (Demonstrated: deleting the add_argument while leaving the docstring kept a
        # substring check green on a script that exits 2 on every scheduled run.)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "add_argument" and any(
                    isinstance(a, ast.Constant) and a.value == "--emit-alerts"
                    for a in node.args):
                declared = True
            if node.func.attr == "parse_args":
                strict = True
    return declared or not strict


def test_every_scheduled_script_accepts_the_flag_the_scheduler_passes():
    """PATH EXISTENCE IS NOT ENOUGH — the lesson of this whole item.

    `_run_rule` always invokes [python, script, "--emit-alerts"]. A script that parses
    strictly and does not declare the flag exits 2 on every scheduled run, exactly like
    a missing file. Fixing the brief's PATH only changed the error from "can't open
    file" to "unrecognized arguments"; the test that asserted os.path.exists stayed
    green throughout and certified a job that never runs.
    """
    import api.main as m
    broken = [s for s in [*m._RULE_SCHEDULE, *m._CRON_SCHEDULE]
              if os.path.exists(os.path.join(REPO, s)) and not _accepts_emit_alerts(s)]
    assert broken == [], (
        f"scheduled scripts that would exit 2 on --emit-alerts: {broken}")


# The brief's xfail is gone because the JOB is gone: the generate_brief.py cron entry
# was DELETED (not re-pointed) per the human-approved d3687eb — scripts/morning_brief.py
# already occupies the 06:30 slot. A deleted job cannot fail.


# --- Stage 2: phantom-safe retirement ------------------------------------

@pytest.mark.parametrize("rule", RETIRED)
def test_retired_rules_are_excluded_not_merely_unmapped(rule):
    """The RULE_01 trap: rule10_instruments resolves .get(rule, rule), so an
    eligible-but-unmapped rule becomes its OWN phantom instrument."""
    assert rule in RULE_10_EXCLUDED, f"{rule} is not excluded — it would be a phantom instrument"


def test_retired_rules_contribute_no_instrument():
    assert rule10_instruments(list(RETIRED)) == []
    for r in RETIRED:
        assert rule10_instruments([r]) == [], f"{r} still contributes an instrument"
        assert r not in rule10_instruments(["RULE_06", r])


def test_retired_rules_cannot_complete_a_fire():
    """Two real instruments plus all three retired rules must still not reach 3."""
    assert not rule10_is_valid(["RULE_06", "RULE_01B", *RETIRED])
    # ...while a genuine third does, proving the pair was otherwise ready
    assert rule10_is_valid(["RULE_06", "RULE_01B", "RULE_11"])


@pytest.mark.parametrize("rule", RETIRED)
def test_retired_rules_are_no_longer_scheduled(rule):
    import api.main as m
    slug = rule.lower().replace("rule_", "rule_")
    sched = [*m._RULE_SCHEDULE, *m._CRON_SCHEDULE]
    assert not [s for s in sched if slug in s], f"{rule} is still scheduled"


def test_live_rules_are_untouched():
    """The retirement must not have collateral effects."""
    for r in LIVE:
        assert r not in RULE_10_EXCLUDED, f"{r} was wrongly excluded"
        assert RULE_10_INSTRUMENTS.get(r), f"{r} lost its instrument mapping"
    assert rule10_is_valid(["RULE_06", "RULE_01B", "RULE_16"])


def test_rule10_threshold_and_map_are_unchanged():
    from scripts import rule_10_corroboration as r10
    assert jpt_common.RULE_10_MIN_INSTRUMENTS == 3
    assert r10.CONVERGENCE_WINDOW_DAYS == 14
    assert r10.DEDUP_WINDOW_DAYS == 7


# --- Stages 3-4: the docs must match the code ----------------------------

def test_gate_text_matches_the_current_gate():
    from api.routers.evidence import _WHY
    txt = _WHY["RULE_10"]
    assert "14 day" in txt.lower() and "instrument" in txt.lower()
    assert "24h" not in txt and "Four or more" not in txt


def test_no_stale_gate_wording_survives_in_user_facing_pages():
    """My own grep was too narrow and missed four live strings.

    I searched for the literal phrasing I had already fixed; the survivors were
    spelled-out variants — "four or more independent rules", "4+ independent rules",
    "4 independent signals in 24h" — on the landing page, the themes empty state and
    the API docs. This asserts the CLASS across every user-facing page plus code
    comments, not the one phrasing I happened to know about.
    """
    import glob
    import re as _re
    pat = _re.compile(r"(4\+|four|4 or more)[^.]{0,40}(distinct |independent )?"
                      r"(eligible )?rule|within 24 ?h", _re.I)
    offenders = []
    for path in glob.glob(os.path.join(REPO, "api", "static", "*.html")) + \
                glob.glob(os.path.join(REPO, "api", "routers", "*.py")) + \
                glob.glob(os.path.join(REPO, "scripts", "*.py")) + \
                [os.path.join(REPO, "jpt_common.py")]:
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if pat.search(line):
                offenders.append(f"{os.path.relpath(path, REPO)}:{i}")
    assert not offenders, f"stale 4-rules/24h gate wording still live: {offenders}"


def test_claude_md_lists_the_job_that_actually_owns_0630():
    md = open(os.path.join(REPO, "CLAUDE.md"), encoding="utf-8").read()
    non_rule = [l for l in md.splitlines() if l.startswith("Non-rule scheduled jobs")]
    block = md[md.index("Non-rule scheduled jobs"):][:600]
    assert "scripts/morning_brief.py" in block, "the real 06:30 brief job is undocumented"
    import api.main as m
    for name in ("generate_brief.py",):
        assert not [s for s in [*m._RULE_SCHEDULE, *m._CRON_SCHEDULE] if name in s]


def test_claude_md_describes_label_outcomes_accurately():
    """It claimed basket tickers get status='unavailable'; the code only excludes
    empty and space-containing tickers, and SPY (an ETF) has 135 complete rows."""
    md = open(os.path.join(REPO, "CLAUDE.md"), encoding="utf-8").read()
    assert "Non-equity / basket / delisted tickers get" not in md
    assert "_is_equity_ticker" in md and "space" in md
    src = open(os.path.join(REPO, "scripts", "label_outcomes.py"), encoding="utf-8").read()
    assert '" " not in t' in src, "the code changed — re-check what CLAUDE.md claims"


@pytest.mark.parametrize("path,must_contain", [
    ("scripts/rule_12_fara.py", "lda.senate.gov"),
    ("scripts/rule_14_patents.py", "GRANT date"),
])
def test_retired_docstrings_no_longer_lie(path, must_contain):
    head = open(os.path.join(REPO, path), encoding="utf-8").read()[:1600]
    assert "RETIRED" in head
    assert must_contain in head


def test_rule12_docstring_no_longer_claims_doj_fara_as_its_source():
    head = open(os.path.join(REPO, "scripts", "rule_12_fara.py"), encoding="utf-8").read()[:1600]
    assert "Source: DOJ FARA database" not in head
    assert "FALSE" in head or "was false" in head.lower()
