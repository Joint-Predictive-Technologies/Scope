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
    # every entry is a 5-tuple: three glosses plus two semantic booleans. (It was a
    # 3-tuple until a verifier showed the glosses alone were not enough — the page also
    # has to know WHETHER each column means what its name says.)
    for source, means in _COUNTER_MEANING.items():
        assert isinstance(means, tuple) and len(means) == 5, source
        assert all(isinstance(m, str) and m.strip() for m in means[:3]), source
        assert isinstance(means[3], bool) and isinstance(means[4], bool), source
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


# ── the FUNNEL WIRING, not just its helpers ─────────────────────────────────
# A verifier proved the 45 tests above all stayed green under two mutations that
# change behaviour: replacing the rule filter with `lambda s: True`, and reverting
# the last stage to `SUM(alerts_emitted)`. Those are precisely the two regressions
# this endpoint exists to prevent, so they get end-to-end tests over seeded rows.
def _seed(db_path):
    """A minimal activity_log + alerts pair: one DETECTION rule, one non-rule job."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
            events_scanned INTEGER DEFAULT 0, events_flagged INTEGER DEFAULT 0,
            alerts_emitted INTEGER DEFAULT 0, run_at TEXT DEFAULT (datetime('now')),
            duration_seconds REAL, notes TEXT);
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, rule TEXT, ticker TEXT,
            severity TEXT, headline TEXT, tags TEXT, created_at TEXT,
            corroborates INTEGER, opportunity_score REAL DEFAULT 0,
            evidence_confidence REAL DEFAULT 0, theme_id INTEGER);
        """
    )
    conn.execute(
        "INSERT INTO activity_log (source, events_scanned, events_flagged, alerts_emitted,"
        " run_at, duration_seconds) VALUES ('RULE_06', 1000, 10, 2, datetime('now'), 1.0)")
    # a NON-rule job with large counters: if the funnel is not rule-scoped these leak in
    conn.execute(
        "INSERT INTO activity_log (source, events_scanned, events_flagged, alerts_emitted,"
        " run_at, duration_seconds) VALUES ('INGEST_HOUSE_INDEX', 500, 400, 7,"
        " datetime('now'), 1.0)")
    # exactly THREE real alert rows — SUM(alerts_emitted) above is 9, so the two
    # cannot be confused by coincidence
    for i in range(3):
        conn.execute("INSERT INTO alerts (rule, ticker, severity, created_at)"
                     " VALUES ('RULE_06','AAA','HIGH', datetime('now'))")
    conn.commit()
    conn.close()


def _telemetry_over_seeded_db(tmp_path, monkeypatch):
    db = tmp_path / "seeded.db"
    _seed(str(db))
    monkeypatch.setenv("DATABASE_PATH", str(db))
    import importlib

    from api.routers import telemetry as mod
    importlib.reload(mod)          # rebind _get_db_path against the new env
    return mod.telemetry()


def test_the_funnel_excludes_non_rule_sources_end_to_end(tmp_path, monkeypatch):
    """`rule_scanned` must be the RULE row only — 1000, not 1500."""
    d = _telemetry_over_seeded_db(tmp_path, monkeypatch)
    fn = d["metrics"]["rule_funnel_24h"]["row"]
    assert fn["rule_scanned"] == 1000, (
        f"expected the RULE_06 row only; got {fn['rule_scanned']} "
        "(1500 means the non-rule job leaked into 'records examined by detection rules')"
    )
    assert fn["rule_flagged"] == 10, f"expected 10, got {fn['rule_flagged']}"
    # and the exclusion is reported, not silently dropped
    assert fn["excluded_non_rule_scanned"] == 500
    assert fn["excluded_non_rule_flagged"] == 400


def test_the_last_stage_counts_alert_ROWS_not_the_emitted_counter(tmp_path, monkeypatch):
    """3 real alert rows against SUM(alerts_emitted)=9. The stage must say 3.

    This is the original headline defect: `alerts_emitted` is a job-reported work
    counter, so reverting the stage to it would print 9 alerts where 3 exist.
    """
    d = _telemetry_over_seeded_db(tmp_path, monkeypatch)
    assert d["metrics"]["alerts_written_24h"]["row"]["alerts_written_24h"] == 3
    assert d["metrics"]["rule_funnel_24h"]["row"]["alerts_written"] == 3
    truth = d["metrics"]["emitted_counter_truth"]["row"]
    assert truth["emitted_counter_all_time"] == 9, "the counter is still reported, as itself"
    assert truth["alert_rows_all_time"] == 3


def test_a_failed_block_is_marked_unavailable_and_not_reported_as_zero(tmp_path, monkeypatch):
    """`alert_outcomes` is created lazily by label_outcomes.py, so it is genuinely
    absent on a fresh deploy. The block must say so rather than render 0/0/0."""
    d = _telemetry_over_seeded_db(tmp_path, monkeypatch)   # seeded DB has no alert_outcomes
    block = d["metrics"]["outcome_labeling"]
    assert block.get("unavailable"), "a missing table must mark the block unavailable"
    assert "alert_outcomes" in block["unavailable"]
    assert block["rows"] == [], "and it must be empty, not fabricated"
    assert d["degraded"], "the reason must travel on the payload"


def test_db_backup_flagged_is_glossed_as_a_FAILURE_not_as_unused():
    """Three call sites, and `flagged` inverts: db_backup.py:222/:256 write flagged=1
    when the backup RAISED or integrity_check FAILED. The gloss said "unused (always
    0)" — a false label on the highest-severity signal on the page. Pinned so the
    one-call-site assumption cannot come back."""
    scanned, flagged, emitted = _COUNTER_MEANING["DB_BACKUP"][:3]
    low = flagged.lower()
    assert "fail" in low, f"flagged must be described as a failure signal, got {flagged!r}"
    assert "worse" in low, "and its polarity must be stated"
    assert "unused" not in low and "always 0" not in low
    assert "success" in scanned.lower() or "0 on a failed" in scanned.lower()


# ── the outcome-status gloss, found by a re-audit AFTER the first verifier ──────
def test_all_four_outcome_statuses_are_documented_not_just_the_three_in_the_data():
    """`alert_outcomes` has FOUR statuses; prod currently holds three.

    `scripts/label_outcomes.py` writes `excluded` when an alert's rule was
    QUARANTINED for known-bad attribution (`:190`, and an UPDATE at `:229`). Prod has
    0 such rows, so every version of this page rendered a hardcoded three and would
    have silently shrunk the other bars if a quarantine ever happened. Pinned so the
    absence of a value in today's data cannot again be mistaken for its absence from
    the schema.
    """
    from api.routers import telemetry as mod
    meanings = None
    # the map lives on the response, so build one against the disposable test DB
    from fastapi.testclient import TestClient

    from api.main import app
    meanings = TestClient(app).get("/api/telemetry").json()["metrics"] \
        ["outcome_labeling"]["status_meaning"]
    for st in ("complete", "pending", "unavailable", "excluded"):
        assert st in meanings, f"{st!r} is a real status that label_outcomes.py writes"
        assert meanings[st].strip(), f"{st!r} has no meaning documented"
    # the one that matters most must say WHY it exists
    assert "quarantin" in meanings["excluded"].lower(), \
        "'excluded' means quarantined for known-bad attribution — say so"


def test_unavailable_is_not_glossed_as_merely_unpriceable():
    """Measured on prod: of 1,705 `unavailable` rows only 546 are a missing price.

    1,026 are 'non-single-equity (basket/multi-ticker)' and 133 are 'no ticker' — the
    alert never had a usable symbol, which is a different fact from the market not
    having a price. The page called all of them 'unpriceable'.
    """
    from fastapi.testclient import TestClient

    from api.main import app
    block = TestClient(app).get("/api/telemetry").json()["metrics"]["outcome_labeling"]
    mean = block["status_meaning"]["unavailable"].lower()
    assert "not" in mean and "unpriceable" in mean, \
        "the gloss must explicitly reject the 'unpriceable' shorthand"
    assert "symbol" in mean or "single-equity" in mean, \
        "it must name the real dominant cause (no usable single-equity symbol)"
    # and the reason breakdown must be served so the page can show it
    assert "reasons" in block and isinstance(block["reasons"], list)
    assert "reasons_sql" in block and block["reasons_sql"].strip()


# ── the FOURTH occurrence of the false-label defect, found one layer inside the fix ──
def test_flagged_is_only_counted_where_it_is_actually_a_filter_output():
    """`events_flagged` has no consistent meaning across sources.

    A verifier proved the rule-only funnel left a LARGER error than it removed: on prod
    46.7% of the displayed "passed the quality filter" figure was not that. Read off the
    call sites — RULE_01B/RULE_09/RULE_10/RULE_15/RULE_ANOMALY pass the SAME value to
    scanned and flagged; RULE_11/RULE_16 pass a DB-write count; RULE_02/RULE_CLUSTER pass
    an aggregate in a different unit.
    """
    from api.routers.telemetry import _flagged_is_filter, _scanned_is_records

    # genuine filter outputs — these five and no more
    for src in ("RULE_OSINT", "RULE_REDDIT", "RULE_07", "RULE_06", "RULE_08"):
        assert _flagged_is_filter(src), f"{src}'s flagged IS a filter output"
    # NOT filter outputs, each for a different documented reason
    for src in ("RULE_01B", "RULE_09", "RULE_10", "RULE_15", "RULE_ANOMALY",
                "RULE_11", "RULE_16", "RULE_02", "RULE_CLUSTER"):
        assert not _flagged_is_filter(src), (
            f"{src}'s flagged is NOT 'passed a filter' — counting it reproduces the defect")


def test_rule_anomaly_is_excluded_from_records_examined():
    """`rule_anomaly.py:130` writes scanned=flagged=emitted — all three are the ALERTS IT
    WROTE. It is structurally identical to `decay_alerts.py:104`, the writer this module
    cites as its reason for excluding non-rule sources; being a detection rule, the old
    source-type scoping waved it straight through into "records examined"."""
    from api.routers.telemetry import _COUNTER_MEANING, _flagged_is_filter, _scanned_is_records
    assert not _scanned_is_records("RULE_ANOMALY")
    assert not _flagged_is_filter("RULE_ANOMALY")
    means = _COUNTER_MEANING["RULE_ANOMALY"]
    assert "identical" in means[0].lower() and "emitted" in means[0].lower(), \
        "its scanned gloss must say it is identical to the emitted count"


def test_every_source_that_logs_to_prod_has_a_documented_meaning():
    """A source with no entry renders 'not documented' — acceptable — but the ones we
    know log to prod should all be covered, or the excluded table is mostly blanks."""
    from api.routers.telemetry import _COUNTER_MEANING
    for src in ("RULE_01B", "RULE_02", "RULE_06", "RULE_07", "RULE_08", "RULE_09",
                "RULE_10", "RULE_11", "RULE_15", "RULE_16", "RULE_ADSB", "RULE_ANOMALY",
                "RULE_CLUSTER", "RULE_OSINT", "RULE_REDDIT", "RULE_COLLECTOR",
                "SCORING", "DB_BACKUP", "DECAY", "MONITOR_BACKUP_STALL",
                "MONITOR_ENRICH_STALL", "INGEST_HOUSE_INDEX", "PARSE_HOUSE_PDFS",
                "REFRESH_TICKERS", "LABEL_OUTCOMES", "BRIEF", "DAILY_BRIEF",
                "BACKTEST", "SCHEDULER_JOB_FAILURE"):
        m = _COUNTER_MEANING.get(src)
        assert m and len(m) == 5, f"{src} has no 5-tuple meaning"
        assert isinstance(m[3], bool) and isinstance(m[4], bool), \
            f"{src} must declare whether each column means what its name says"


def test_label_outcomes_flagged_gloss_no_longer_says_merely_unpriceable():
    """The twin of the outcome-status defect: `_COUNTER_MEANING['LABEL_OUTCOMES'][1]`
    described the same column as 'unpriceable alerts', which is wrong for ~68% of them."""
    from api.routers.telemetry import _COUNTER_MEANING
    gloss = _COUNTER_MEANING["LABEL_OUTCOMES"][1].lower()
    assert "not merely 'unpriceable'" in gloss or "not merely \"unpriceable\"" in gloss, \
        "the gloss must reject the 'unpriceable' shorthand outright"
    assert "symbol" in gloss, "and name the real dominant cause"
