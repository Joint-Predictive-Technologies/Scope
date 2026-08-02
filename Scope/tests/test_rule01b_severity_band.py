#!/usr/bin/env python3
"""
RULE_01B — severity comes from a NUMERIC BAND, not a three-literal denylist.

Runs under pytest:  pytest tests/test_rule01b_severity_band.py

Why these fixtures exist. `_is_above_15k` was a DENYLIST of three amount strings:

    band not in {"$1,001 - $15,000", "$1k–15k", "$1,001-$15,000"}

so anything it did not recognise was HIGH. On the 2026-07-28 snapshot that is **37 of 9967**
transactions — 15 carrying `$1,001- $15,000` (the same band, one space missing), 15 raw
dollar-and-cents values, 5 bare `$1,001`, plus `$1,001 - $35,000` and `$1,`. The call site
passes `amount_band or "undisclosed amount"`, so a MISSING amount was HIGH as well.

Severity is gate-relevant: only HIGH/CRITICAL clear RULE_10's candidate floor, so an
unparseable string became a gate-eligible leg.

⚠️ NO SEVERITY REMAP ACCOMPANIES THIS. Severity is a detection-time score and this fix is
forward-only in code. Stored rows are measured by `scripts/audit_rule01b_severity.py`, which
has no write path at all.
"""
import importlib.util
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO = os.path.join(os.path.dirname(__file__), "..")
_RULE_PATH = os.path.join(_REPO, "scripts", "rule_01b_first_touch.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r01b = _load(_RULE_PATH, "rule_01b_sev")

import jpt_common  # noqa: E402
import ingest_senate  # noqa: E402

MEMBER = "A000001"
TODAY = date.today()
RECENT = TODAY - timedelta(days=10)

# The five malformed shapes actually present in `transactions`, with their counts on the
# 2026-07-28 snapshot. Every one of them is HIGH under the denylist.
MALFORMED = [
    ("$1,001- $15,000", 15),   # the blocked band, one space missing
    ("$823.45", 15),           # raw dollars-and-cents (this shape, 15 distinct values)
    ("$1,001", 5),             # bare floor, no range
    ("$1,001 - $35,000", 1),   # a real band that straddles the threshold
    ("$1,", 1),                # malformed
]


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO members (bioguide_id, full_name, party, state, chamber, is_current) "
        "VALUES (?,?,?,?,?,1)",
        (MEMBER, "Alpha, Aaron", "Democratic", "NJ", "House of Representatives"),
    )
    conn.execute("INSERT INTO tickers (symbol, company_name) VALUES ('ABT','Abbott')")
    conn.commit()
    conn.close()
    yield


def _tx(tx_id, ticker, band):
    conn = jpt_common.db_connection()
    conn.execute(
        "INSERT INTO transactions (id, member_id, raw_ticker_string, transaction_type, "
        "amount_band, transaction_date, filing_date) VALUES (?,?,?,'purchase',?,?,?)",
        (tx_id, MEMBER, ticker, band, RECENT.isoformat(), TODAY.isoformat()),
    )
    conn.commit()
    conn.close()


def _one():
    conn = jpt_common.db_connection()
    rows = conn.execute(
        "SELECT ticker, severity, detail FROM alerts WHERE rule='RULE_01B'").fetchall()
    conn.close()
    assert len(rows) == 1, f"expected one alert, got {len(rows)}"
    return rows[0]


# ─────────────────────────────────────────────────────────────────────────────
# 1. THE CANONICAL BANDS — table-driven over the REAL map
# ─────────────────────────────────────────────────────────────────────────────

def test_every_canonical_amount_band_resolves_correctly():
    """Driven off `jpt_common.AMOUNT_BANDS` itself, not a hand-copied list.

    A hand-copied list is how the denylist got stale in the first place: it named three
    spellings and silently mis-scored the rest.
    """
    for band in jpt_common.AMOUNT_BANDS:
        floor = ingest_senate.amount_band_floor(band)
        assert floor is not None, f"the canonical band {band!r} does not parse"
        expected = floor > r01b.RULE_01B_HIGH_THRESHOLD
        assert r01b._is_above_15k(band) is expected, (
            f"{band!r} (floor {floor}) resolved wrongly"
        )


def test_the_boundary_is_exactly_above_15000():
    assert r01b._is_above_15k("$15,001 - $50,000") is True
    assert r01b._is_above_15k("$1,001 - $15,000") is False
    assert r01b.RULE_01B_HIGH_THRESHOLD == 15_000


# ─────────────────────────────────────────────────────────────────────────────
# 2. THE 37 — every malformed shape was a false HIGH
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("band,snapshot_count", MALFORMED)
def test_a_malformed_band_is_no_longer_a_false_HIGH(band, snapshot_count):
    denylist = {"$1,001 - $15,000", "$1k–15k", "$1,001-$15,000"}
    assert band not in denylist, "fixture error: this band was already handled"
    assert r01b._is_above_15k(band) is False, (
        f"{band!r} (n={snapshot_count} on the snapshot) is still HIGH — it is at most "
        f"${ingest_senate.amount_band_floor(band)}"
    )


def test_a_band_straddling_the_threshold_uses_its_FLOOR():
    """`$1,001 - $35,000` could be either side. HIGH is a claim, so it needs the minimum."""
    assert ingest_senate.amount_band_floor("$1,001 - $35,000") == 1001
    assert r01b._is_above_15k("$1,001 - $35,000") is False, (
        "a band whose minimum is $1,001 was called HIGH on the strength of its ceiling"
    )


@pytest.mark.parametrize("band", ["$5,000,001 - $25,000,000", "Over $50,000,000",
                                  "$50,001 - $100,000", "$15,001"])
def test_a_genuinely_large_amount_is_still_HIGH(band):
    assert r01b._is_above_15k(band) is True, "the fix silenced a real HIGH"


# ─────────────────────────────────────────────────────────────────────────────
# 3. FAIL-SAFE DIRECTION
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("band", [None, "", "   ", "undisclosed amount", "not a number"])
def test_an_unparseable_amount_fails_SAFE_to_medium(band):
    """The honest direction, stated explicitly.

    The denylist made every one of these HIGH — including `"undisclosed amount"`, which is
    the literal string the call site substitutes for a missing `amount_band`. A severity we
    cannot justify must not be the one that clears RULE_10's candidate floor.
    """
    assert r01b._is_above_15k(band) is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. THE THRESHOLD IS NOT HARMONISED WITH RULE_01
# ─────────────────────────────────────────────────────────────────────────────

def test_the_two_thresholds_are_not_harmonised():
    """RULE_01B is $15k, RULE_01 is $50k, and this session did not merge them.

    `instrument-definitions-and-tiers.md` redefines RULE_01B conceptually but states no
    severity threshold, so there is no documented target. Moving either number changes how
    many congressional legs clear the gate's HIGH/CRITICAL floor — a gate change, and its
    own decision. This test exists so that becomes a deliberate edit rather than a drift.
    """
    assert r01b.RULE_01B_HIGH_THRESHOLD == 15_000
    assert ingest_senate.HIGH_AMOUNT_THRESHOLD == 50_000
    assert r01b.RULE_01B_HIGH_THRESHOLD != ingest_senate.HIGH_AMOUNT_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# 5. END TO END — and out of the gate's candidate set
# ─────────────────────────────────────────────────────────────────────────────

def test_a_malformed_band_first_touch_emits_MEDIUM_and_misses_the_gate():
    _tx(100, "ABT", "$1,001- $15,000")

    r01b.run(emit=True)

    assert _one()["severity"] == "MEDIUM"

    conn = jpt_common.db_connection()
    candidates = conn.execute(
        "SELECT ticker FROM alerts WHERE ticker IS NOT NULL AND ticker != '' "
        "  AND severity IN ('HIGH','CRITICAL')"
    ).fetchall()
    conn.close()
    assert candidates == [], (
        "a $1,001 trade cleared RULE_10's HIGH/CRITICAL candidate floor"
    )


def test_a_real_large_first_touch_still_reaches_the_gate():
    _tx(100, "ABT", "$50,001 - $100,000")

    r01b.run(emit=True)

    assert _one()["severity"] == "HIGH"
    conn = jpt_common.db_connection()
    n = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE ticker!='' AND severity IN ('HIGH','CRITICAL')"
    ).fetchone()[0]
    conn.close()
    assert n == 1


# ─────────────────────────────────────────────────────────────────────────────
# 6. NO SEVERITY REMAP EXISTS
# ─────────────────────────────────────────────────────────────────────────────

def test_no_RULE_01B_script_contains_a_stored_severity_write():
    """Severity is a detection-time score. This fix is forward-only IN CODE.

    The three prior RULE_01B sessions each shipped a remap because they corrected
    ATTRIBUTION. This one must not: an out-of-band severity write is the exact defect the
    RULE_15 saga treated as a violation. The audit script has no write path at all.

    ⚠️ THIS IS A TRIPWIRE, NOT A PROOF, AND THE NAME SAYS SO. A verification pass defeated
    an earlier version with `UPDATE alerts AS a SET severity` and with string concatenation,
    and pointed out that its glob (`*rule01b*`) did not even match `rule_01b_first_touch.py`
    — the underscore. Both are fixed below, but a determined write can still evade a
    source scan. It catches drift, which is what it is for.

    Out of scope by design: `scripts/decay_alerts.py:71` downgrades CRITICAL->HIGH guarded
    only by `rule != 'RULE_10'`, so RULE_01B is NOT excluded there. It is unreachable only
    because RULE_01B emits HIGH or MEDIUM and never CRITICAL (see the emit site, and 0
    CRITICAL rows in the corpus) — a conditional guarantee, not an absolute one, and it is
    that script's business rather than this one's.
    """
    import ast
    import glob
    import re

    def executable_source(path):
        """The file minus its module docstring.

        ⚠️ SCAN CODE, NOT PROSE. The first version of this test grepped the whole file, and
        the audit script's own docstring — which EXPLAINS that it contains no UPDATE, no
        INSERT and no `--apply` — tripped every assertion. A doc that describes a guarantee
        must not be mistaken for a breach of it.
        """
        src = open(path).read()
        doc = ast.get_docstring(ast.parse(src))
        return src.replace(doc, "", 1) if doc else src

    # Both spellings. `*rule01b*` alone misses `rule_01b_first_touch.py` — the rule that
    # actually assigns severity — which is precisely the file this must cover.
    family = sorted(set(
        glob.glob(os.path.join(_REPO, "scripts", "*rule01b*.py"))
        + glob.glob(os.path.join(_REPO, "scripts", "*rule_01b*.py"))
    ))
    assert any(f.endswith("rule_01b_first_touch.py") for f in family), (
        "the rule itself is not in the scanned set — the guard is looking at the wrong files"
    )
    # Tolerates a table alias (`UPDATE alerts AS a SET ... severity`), which defeated the
    # first version of this check.
    write = re.compile(r"UPDATE\s+alerts\b(?:\s+AS\s+\w+)?\s+SET[^\"';]*severity", re.I | re.S)
    offenders = [os.path.basename(p) for p in family if write.search(executable_source(p))]
    assert offenders == [], f"these scripts write stored severity: {offenders}"

    audit = os.path.join(_REPO, "scripts", "audit_rule01b_severity.py")
    if os.path.exists(audit):
        body = executable_source(audit)
        assert 'add_argument("--apply"' not in body and "add_argument('--apply'" not in body, \
            "the audit script grew an --apply flag"
        for verb in ("UPDATE", "INSERT", "DELETE"):
            assert not re.search(rf"\b{verb}\s+(INTO\s+)?\w+", body, re.I), \
                f"the audit script contains a {verb} statement"
        assert "commit()" not in body, "the audit script commits"


# ─────────────────────────────────────────────────────────────────────────────
# 7. MUTATION
# ─────────────────────────────────────────────────────────────────────────────

def test_restoring_the_denylist_turns_the_malformed_fixtures_red(tmp_path):
    src = open(_RULE_PATH).read()
    target = "    floor = amount_band_floor(band)\n" \
             "    return floor is not None and floor > RULE_01B_HIGH_THRESHOLD"
    assert target in src, (
        "the severity comparison this mutation targets is no longer in the source — "
        "the mutation test has gone stale and must be updated"
    )
    mutant_path = tmp_path / "rule_01b_sev_mutant.py"
    mutant_path.write_text(src.replace(
        target,
        '    return bool(band) and band not in '
        '{"$1,001 - $15,000", "$1k–15k", "$1,001-$15,000"}', 1))
    mutant = _load(str(mutant_path), "rule_01b_sev_mutant")

    for band, _n in MALFORMED:
        assert mutant._is_above_15k(band) is True, (
            f"{band!r} is not HIGH under the restored denylist, so the fixture does not "
            "discriminate and proves nothing about the fix"
        )
    assert mutant._is_above_15k("undisclosed amount") is True
