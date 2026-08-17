"""Pins the /status telemetry endpoint's load-bearing classification logic.

WHY THIS FILE EXISTS, specifically: this panel's credibility rests on
`_is_rule_source`. It is the function that makes the funnel read 1.37% instead of
1.94%, and the 1.94% version was a REAL NUMBER UNDER A FALSE LABEL — the defect the
whole surface was built to prevent, which shipped twice and was caught twice by
review rather than by a test.

The failure mode a test has to stop is concrete: someone adds a `RULE_`-prefixed
ENRICHER (the same shape as `RULE_OPTIONS`, which is already special-cased), forgets
`_NON_DETECTION_RULE_SOURCES`, and the prefix fallback silently counts its
`events_scanned` as "records examined by detection rules". Nothing would fail. The
panel would print the same class of false label again.

Everything here is a pure unit test over module-level helpers — no DB, no network,
no fixtures. `conftest.py` points DATABASE_PATH at a disposable file autouse, so
even the one endpoint test cannot touch the live database.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.routers.telemetry import (  # noqa: E402
    _COUNTER_MEANING,
    _fill_hour_gaps,
    _is_rule_source,
    _norm_source,
)


# ── the classifier ──────────────────────────────────────────────────────────
# Each case is (source, expected, why) so a failure says what broke, not just that
# a boolean flipped.
@pytest.mark.parametrize("source,expected,why", [
    # real detection rules, both spellings prod actually holds
    ("RULE_06",                   True,  "a detection rule, underscore spelling"),
    ("RULE_01B",                  True,  "a detection rule"),
    ("RULE_ADSB",                 True,  "a detection rule"),
    ("RULE ADSB",                 True,  "prod holds a SPACE spelling of the same rule"),
    ("RULE 07 POLYMARKET",        True,  "prod's legacy label for RULE_07"),

    # RULE-prefixed but NOT detectors — the case that produces a false label
    ("RULE_COLLECTOR",            False, "a collector: 0 rows in alerts, ever"),
    ("RULE COLLECTOR",            False, "same collector, space spelling"),
    ("RULE_OPTIONS",              False, "an enricher, not a detector"),
    ("RULE OPTIONS CORRELATION",  False, "prod's label for the same enricher"),
    ("RULE_DISCOVERY",            False, "a coverage collector"),

    # infrastructure
    ("SCORING",                   False, "the enrich_scores pass"),
    ("ENRICH SCORES",             False, "the same job under its other label"),
    ("DB_BACKUP",                 False, "a backup run"),
    ("DECAY",                     False, "severity downgrades"),
    ("MONITOR_BACKUP_STALL",      False, "a monitor; its flagged is PROBLEMS FOUND"),
    ("MONITOR_ENRICH_STALL",      False, "a monitor"),
    ("INGEST_HOUSE_INDEX",        False, "ingestion"),
    ("PARSE_HOUSE_PDFS",          False, "parsing"),
    ("SCHEDULER_JOB_FAILURE",     False, "the scheduler safety net"),
    ("REFRESH_TICKERS",           False, "ticker refresh"),
    ("LABEL_OUTCOMES",            False, "outcome labelling"),
    ("BRIEF",                     False, "brief generation"),
    ("DAILY_BRIEF",               False, "brief generation"),
    ("POSITION_LEDGER_AUTH_DENIED", False, "an auth audit row"),

    # ⚠️ THE WILDCARD CASE. `LIKE 'RULE_%'` would match this, because `_` is a
    # single-character wildcard in SQL LIKE. The Python classifier must not.
    ("RULES_ANYTHING",            False, "starts with RULE but not RULE_ or 'RULE '"),
    ("RULER",                     False, "same trap, no separator"),

    # degenerate input must never be a rule
    ("",                          False, "empty"),
    (None,                        False, "None"),
])
def test_is_rule_source_classifies_every_real_prod_label(source, expected, why):
    assert _is_rule_source(source) is expected, (
        f"{source!r} should be {'a rule' if expected else 'NOT a rule'} — {why}"
    )


def test_the_like_wildcard_trap_is_the_reason_this_is_python():
    """`LIKE 'RULE_%'` is `RULE?%`. If anyone reverts to SQL, this is what breaks.

    Pinned as its own test because the wildcard is the whole justification for
    classifying in Python, and a reviewer replacing this with a LIKE would make
    `RULES_ANYTHING` a detection rule.
    """
    assert _is_rule_source("RULE_07") is True
    assert _is_rule_source("RULES_ANYTHING") is False
    # a single-char wildcard would have matched both; the classifier separates them


def test_case_and_whitespace_do_not_change_the_verdict():
    for variant in ("rule_06", " RULE_06 ", "Rule_06"):
        assert _is_rule_source(variant) is True, variant
    for variant in ("scoring", " DB_BACKUP "):
        assert _is_rule_source(variant) is False, variant


# ── duplicate-label detection ───────────────────────────────────────────────
@pytest.mark.parametrize("a,b", [
    ("RULE_ADSB",           "RULE ADSB"),
    ("RULE_OSINT",          "RULE OSINT"),
    ("RULE_REDDIT",         "RULE REDDIT"),
    # these two need the explicit alias map — no spelling rule pairs them, and a
    # review caught the page under-disclosing because of it
    ("RULE_07",             "RULE 07 POLYMARKET"),
    ("SCORING",             "ENRICH SCORES"),
])
def test_same_job_under_two_labels_normalises_to_one_identity(a, b):
    assert _norm_source(a) == _norm_source(b), (
        f"{a!r} and {b!r} are the same job and must collide so the page can disclose it"
    )


def test_genuinely_different_sources_do_not_collide():
    """The detector must not over-merge — a false pair is as bad as a missed one."""
    distinct = ["RULE_06", "RULE_07", "RULE_01B", "SCORING", "DB_BACKUP", "DECAY"]
    norms = [_norm_source(x) for x in distinct]
    assert len(set(norms)) == len(distinct), f"over-merged: {norms}"


# ── hour-gap filling ────────────────────────────────────────────────────────
def test_missing_hours_are_filled_and_marked_not_silently_dropped():
    """GROUP BY emits only populated hours; the chart spaces bars by index.

    A review reproduced the consequence: a 4-hour outage rendered identically to a
    1-hour gap on an axis labelled "one bar = one clock hour". An outage is exactly
    when someone opens /status.
    """
    rows = [
        {"hour": "2026-08-17 09:00", "scanned": 5, "flagged": 1, "emitted_counter": 0, "runs": 2},
        {"hour": "2026-08-17 12:00", "scanned": 7, "flagged": 2, "emitted_counter": 1, "runs": 3},
    ]
    out = _fill_hour_gaps(rows)
    assert [r["hour"] for r in out] == [
        "2026-08-17 09:00", "2026-08-17 10:00", "2026-08-17 11:00", "2026-08-17 12:00",
    ]
    assert [r["no_runs"] for r in out] == [False, True, True, False]
    # a filled hour must be zero, not carry the neighbour's numbers
    for r in out:
        if r["no_runs"]:
            assert (r["scanned"], r["flagged"], r["emitted_counter"], r["runs"]) == (0, 0, 0, 0)
    # the real hours keep their real values
    assert out[0]["scanned"] == 5 and out[-1]["scanned"] == 7


def test_fill_preserves_the_real_window_and_invents_no_hour_outside_it():
    rows = [{"hour": "2026-08-17 09:00", "scanned": 1, "flagged": 0,
             "emitted_counter": 0, "runs": 1}]
    out = _fill_hour_gaps(rows)
    assert len(out) == 1 and out[0]["no_runs"] is False


def test_fill_handles_an_empty_window():
    """An empty 24 h window is a real state — fresh deploy, post-restore, quiet day."""
    assert _fill_hour_gaps([]) == []


def test_fill_returns_unparseable_input_untouched_rather_than_guessing():
    rows = [{"hour": "not-a-timestamp", "scanned": 1, "flagged": 0,
             "emitted_counter": 0, "runs": 1}]
    assert _fill_hour_gaps(rows) == rows


# ── the counter-meaning map ─────────────────────────────────────────────────
def test_counter_meaning_never_gives_a_source_another_sources_gloss():
    """The map is keyed, not positional — that is the entire point.

    The defect it replaced bound a hardcoded three-item sentence to a DATA-ORDERED
    top-3, so when PARSE_HOUSE_PDFS displaced DB_BACKUP the page called 703 parsed
    congressional transactions "a snapshot file".
    """
    # every entry is a 3-tuple of non-empty strings
    for source, means in _COUNTER_MEANING.items():
        assert isinstance(means, tuple) and len(means) == 3, source
        assert all(isinstance(m, str) and m.strip() for m in means), source
    # the two glosses the false-label defect confused must be distinct and correct
    assert "snapshot" in _COUNTER_MEANING["DB_BACKUP"][2].lower()
    assert "transaction" in _COUNTER_MEANING["PARSE_HOUSE_PDFS"][2].lower()
    assert _COUNTER_MEANING["DB_BACKUP"][2] != _COUNTER_MEANING["PARSE_HOUSE_PDFS"][2]


def test_the_inverted_monitor_metric_is_labelled_as_inverted():
    """MONITOR_BACKUP_STALL's `flagged` is len(problems): higher is WORSE.

    A reader who assumes "flagged" means "passed a filter" would read an alarm as
    throughput, so the gloss has to say so.
    """
    flagged_means = _COUNTER_MEANING["MONITOR_BACKUP_STALL"][1]
    assert "worse" in flagged_means.lower() or "problem" in flagged_means.lower()


# ── the endpoint itself ─────────────────────────────────────────────────────
def test_endpoint_returns_every_block_with_its_own_sql():
    """conftest's autouse fixture points DATABASE_PATH at a disposable DB, so this
    cannot read or write the live database."""
    from fastapi.testclient import TestClient

    from api.main import app

    # NOT a `with` block: app startup launches APScheduler, which would begin
    # running real rule subprocesses.
    client = TestClient(app)
    r = client.get("/api/telemetry")
    assert r.status_code == 200
    body = r.json()

    assert "as_of_utc" in body and body["as_of_utc"], "the live 'as of' stamp must be present"
    assert isinstance(body.get("query_ms"), (int, float))
    metrics = body["metrics"]
    assert len(metrics) >= 20, f"expected the full block set, got {len(metrics)}"
    for name, block in metrics.items():
        assert block.get("sql", "").strip(), f"{name} has no SQL — provenance is the contract"


def test_endpoint_contains_no_write_statement():
    """A read-only diagnostic must stay read-only, in code not just in intent."""
    import ast

    path = Path(__file__).resolve().parent.parent / "api" / "routers" / "telemetry.py"
    tree = ast.parse(path.read_text())
    # Every string CONSTANT in the module — this is where SQL lives. Docstrings are
    # included deliberately: if a write verb appears in one it is worth a human look.
    verbs = ("insert into", "update ", "delete from", "drop table", "alter table",
             "create table", "create index")
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = " ".join(node.value.lower().split())
            for v in verbs:
                if v in low:
                    offenders.append((v, node.value.strip()[:70]))
    assert not offenders, f"possible write statement(s) in SQL/strings: {offenders}"


def test_the_connection_is_read_only():
    """It used to be `db_connection()`, which re-runs the schema script and COMMITS.

    On prod (`journal_mode=delete`, verified) that is a whole-database write lock,
    taken once per poll per open tab against a DB that rules write to every 5
    minutes. A pre-merge review rejected it.
    """
    import ast

    path = Path(__file__).resolve().parent.parent / "api" / "routers" / "telemetry.py"
    src = path.read_text()
    assert "mode=ro" in src, "the endpoint must open a read-only connection"
    # AST, not text: the docstring legitimately DISCUSSES db_connection(), and a
    # text match on it fails for the wrong reason (it did, on first run).
    tree = ast.parse(src)
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "db_connection" not in called, "db_connection() is still CALLED, not just discussed"
