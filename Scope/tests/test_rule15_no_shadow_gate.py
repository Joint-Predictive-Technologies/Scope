"""RULE_10 is the SINGLE corroboration authority. RULE_15 corroborates nothing.

RULE_15 used to run a second, hidden gate: RULE_08 + RULE_09 + RULE_15 on one ticker
within 30 days made it UPDATE *other rules'* alerts to lifecycle_stage='corroborated'.
It counted rule NAMES rather than instruments — the exact D1 defect the gate redesign
spent a session removing — bypassed RULE_10, and its UPDATE carried no time bound at
all while its trigger did.

It was dormant only because RULE_15 never reached its emit path, so repairing RULE_15
would have RE-ARMED D1. These tests exist so that can never happen quietly.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection

# abspath matters: an unnormalised ".../tests/.." makes the "/tests" skip below
# match EVERY directory, silently walking nothing and passing vacuously.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RULE15 = os.path.join(REPO, "scripts", "rule_15_earnings_nlp.py")
RULE10 = os.path.join(REPO, "scripts", "rule_10_corroboration.py")


def _code(path: str) -> str:
    """Source with comments stripped, so a comment ABOUT the removal can't pass."""
    src = open(path, encoding="utf-8").read()
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())


def test_rule15_contains_no_corroboration_write():
    code = _code(RULE15)
    assert "lifecycle_stage" not in code, \
        "RULE_15 still has an executable lifecycle_stage write"
    assert "corroborated" not in code, \
        "RULE_15 still writes a corroboration"


def test_rule15_does_not_count_rule_names_for_corroboration():
    """The D1 signature: COUNT(DISTINCT rule) over a hardcoded rule list."""
    code = _code(RULE15)
    assert "COUNT(DISTINCT rule)" not in code.replace(" ", "").replace("COUNT(DISTINCTrule)",
                                                                      "COUNT(DISTINCT rule)")
    assert "RULE_08" not in code or "RULE_09" not in code, \
        "RULE_15 still references the RULE_08/09 cross-check trio"


def test_rule10_remains_the_single_corroboration_authority():
    code10 = _code(RULE10)
    assert "lifecycle_stage" in code10 and "corroborated" in code10, \
        "RULE_10 must still be the one that sets corroborated"


def test_only_rule10_writes_corroborated_repo_wide():
    """Repo-wide: exactly one executable writer of lifecycle_stage='corroborated'."""
    writers = []
    for root, _dirs, files in os.walk(REPO):
        if (os.sep + "tests") in root or (os.sep + ".git") in root \
                or "__pycache__" in root:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            code = _code(path)
            # BOTH quote styles — the single-quote-only pattern let a double-quoted
            # second corroboration authority sit in the repo undetected.
            if re.search(r"""lifecycle_stage\s*=\s*['"]corroborated['"]""", code):
                writers.append(os.path.relpath(path, REPO))
    assert writers, "the walk found no writers at all — the scan is broken, not clean"
    assert sorted(writers) == ["scripts/rule_10_corroboration.py"], \
        f"expected RULE_10 to be the only corroboration writer, got {writers}"


# The end-to-end D1 test that used to live here was VACUOUS: it ran RULE_15 with no
# `tickers` rows, so after the CIK fix every ticker was skipped, the emit path was
# unreachable, and it passed even with the shadow gate FULLY RE-INSERTED (proven by
# mutation). Replaced by
# tests/test_rule15_payoff.py::test_rule15_emitting_corroborates_nothing_outside_rule10,
# which seeds `tickers` + `earnings_sentiment`, ASSERTS the rule emitted, and only then
# asserts nothing is corroborated — so it cannot pass for free.
