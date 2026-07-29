#!/usr/bin/env python3
"""
Unit tests for the correctness/trust fixes:
  - RULE_10 eligibility (Section 1B)
  - Federal contractor → ticker resolution (Section 1E)
  - Congressional date validation (Section 1D)

Runs under pytest, or standalone:  python3 tests/test_trust_fixes.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import (
    rule10_is_valid, rule10_eligible_rules, rule10_rules_from_tags,
    resolve_contractor, CONTRACTOR_MIN_CONFIDENCE,
)


# ── RULE_10 eligibility ──────────────────────────────────────────────────────
def test_two_eligible_rules_no_rule10():
    assert rule10_is_valid(["RULE_02", "RULE_06"]) is False

def test_four_eligible_distinct_rules_fires():
    # RULE_16 (institutional) replaced RULE_08 here on 2026-07-29 when RULE_08 was
    # excluded as a basket rule. Shape preserved: 4 rule names, 3 instruments
    # (RULE_01B+RULE_02 are both congressional) — which is what must fire.
    assert rule10_is_valid(["RULE_01B", "RULE_02", "RULE_06", "RULE_16"]) is True

def test_four_raw_but_two_families_no_rule10():
    assert rule10_is_valid(["RULE_02", "RULE_02", "RULE_06", "RULE_06"]) is False

def test_excluded_rules_dont_count():
    # 2 eligible + 2 excluded -> still invalid
    assert rule10_is_valid(["RULE_02", "RULE_06", "RULE_07", "RULE_OSINT"]) is False
    assert rule10_eligible_rules(["RULE_02", "RULE_06", "RULE_07", "RULE_OSINT", "RULE_ANOMALY", "RULE_REDDIT"]) == ["RULE_02", "RULE_06"]

def test_lmt_two_rule_case_is_invalid():
    # The bad production card: RULE_07 + RULE_08. It was 1 eligible (07 noise-excluded);
    # since 2026-07-29 it is 0 — RULE_08 is basket-excluded too. Still invalid, now more so.
    assert rule10_is_valid(["RULE_07", "RULE_08"]) is False
    assert rule10_eligible_rules(["RULE_07", "RULE_08"]) == []

def test_rules_from_tags_parsing():
    assert rule10_rules_from_tags('{"rules": ["RULE_01B","RULE_06"]}') == ["RULE_01B", "RULE_06"]
    assert rule10_rules_from_tags('{"rules_fired": "RULE_01B,RULE_06,RULE_08"}') == ["RULE_01B", "RULE_06", "RULE_08"]
    assert rule10_rules_from_tags("not json") == []


# ── Contractor → ticker resolution ───────────────────────────────────────────
_TMAP = [
    ("HNST", "The Honest Company Inc"),
    ("TITN", "Titan Machinery Inc"),
    ("CPSS", "Consumer Portfolio Services Inc"),
    ("LMT", "Lockheed Martin Corporation"),
]

def _ticker(name):
    t, _p, conf = resolve_contractor(name, _TMAP)
    return t if (t and conf >= CONTRACTOR_MIN_CONFIDENCE) else None

def test_exact_issuer_match():
    assert _ticker("LOCKHEED MARTIN CORPORATION") == "LMT"

def test_subsidiary_maps_to_public_parent():
    t, parent, conf = resolve_contractor("RAYTHEON COMPANY", _TMAP)
    assert t == "RTX" and conf >= 90 and "RTX" in parent

def test_alias_match():
    assert _ticker("PRATT & WHITNEY") == "RTX"

def test_false_partial_collision_rejected():
    # The original bug: RAYTHEON COMPANY must NOT map to HNST (Honest Company)
    assert _ticker("RAYTHEON COMPANY") != "HNST"

def test_private_contractor_no_ticker():
    t, parent, conf = resolve_contractor("BECHTEL PLANT MACHINERY, INC.", _TMAP)
    assert t is None and "private" in (parent or "").lower()

def test_ambiguous_generic_name_unmapped():
    assert _ticker("MISSION SUPPORT & TEST SERVICES LLC") is None
    assert _ticker("CONSOLIDATED NUCLEAR SECURITY, LLC") is None

def test_unknown_private_returns_nothing():
    assert resolve_contractor("SOME RANDOM PRIVATE CONTRACTOR LLC", _TMAP) == (None, None, 0)


# ── Congressional date validation ────────────────────────────────────────────
def _date_valid(txn, filed):
    """Mirror of the SQL/UI rule: filing must not precede the transaction."""
    from datetime import date
    try:
        t = date.fromisoformat(txn); f = date.fromisoformat(filed)
    except Exception:
        return False
    return f >= t

def test_normal_dates_valid():
    assert _date_valid("2026-06-01", "2026-06-11") is True

def test_impossible_ordering_invalid():
    # The bug: transaction Jun 11, filed Jun 1
    assert _date_valid("2026-06-11", "2026-06-01") is False

def test_malformed_dates_invalid():
    assert _date_valid("99/99/99", "2026-06-01") is False
    assert _date_valid("2026-06-01", "") is False


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
