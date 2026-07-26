#!/usr/bin/env python3
"""
RULE_06 (Form 4 insider) reliability.

The bug being fixed: the rule emitted alerts but wrote **zero** `activity_log`
rows — a full 7-day re-scan of every Form 4, fetched serially, could not finish
inside the scheduler's 300s timeout, and `record_activity` only ran after the
loop. A non-200 from EDGAR additionally `break`ed out of pagination and reported
a clean zero, indistinguishable from a quiet day.

What these tests can and cannot prove: everything here runs against fixtures and
a disposable DB, so it pins down the rule's *control flow* — window selection,
dedup, commit points, failure signalling, the time budget. Whether the rule
emits the right alerts against live EDGAR, and whether a live run fits the
budget, are NOT provable here and are not claimed.

Runs under pytest or standalone:  python3 tests/test_rule06_reliability.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import jpt_common                      # noqa: E402
import rule_06_form4 as rule           # noqa: E402
from edgar_fake import ALL_ADSH, FakeClock, FakeEdgar   # noqa: E402

TODAY = "2026-07-26"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _install(monkeypatch, edgar: FakeEdgar, clock: FakeClock | None = None) -> FakeEdgar:
    monkeypatch.setattr(rule, "_get", edgar)
    if clock is not None:
        monkeypatch.setattr(rule, "time", clock)
    return edgar


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = jpt_common.db_connection()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _activity() -> list[dict]:
    return _rows(
        "SELECT events_scanned, events_flagged, alerts_emitted, duration_seconds, notes "
        "FROM activity_log WHERE source='RULE_06' ORDER BY id"
    )


def _alerts() -> list[dict]:
    return _rows("SELECT ticker, severity, headline, tags FROM alerts WHERE rule='RULE_06' ORDER BY id")


def _recorded_filings() -> list[dict]:
    return _rows(
        "SELECT doc_id, filing_date, report_type, extraction_status, member_id, raw_url "
        "FROM filings WHERE source='sec_form4' ORDER BY doc_id"
    )


def _seed_processed(adsh_list: list[str], filing_date: str = "2026-07-24") -> None:
    """Pretend a previous run already processed these filings."""
    conn = jpt_common.db_connection()
    for adsh in adsh_list:
        conn.execute(
            "INSERT INTO filings (source, doc_id, filing_date, report_type, extraction_status) "
            "VALUES ('sec_form4', ?, ?, '4', 'parsed_ok')",
            (adsh, filing_date),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# fixture sanity — guards the fixtures themselves
# ---------------------------------------------------------------------------

def test_search_fixture_parses_into_filing_refs(monkeypatch):
    _install(monkeypatch, FakeEdgar())
    refs, complete = rule.search_form4_filings("2026-07-19", TODAY)

    assert complete is True
    assert [r.adsh for r in refs] == ALL_ADSH
    # ciks[0] is the reporting owner (person), confirmed against the live
    # response recorded 2026-07-26; leading zeros stripped.
    assert refs[0].owner_cik == "2000001"
    assert refs[0].filename == "f1.xml"
    assert refs[0].file_date == "2026-07-24"


# ---------------------------------------------------------------------------
# Stage 2 — incremental scan
# ---------------------------------------------------------------------------

def test_cold_start_scans_the_full_window_and_records_every_filing(monkeypatch):
    edgar = _install(monkeypatch, FakeEdgar())

    significant, emitted, status = rule.run(None, TODAY, emit_alerts=True)

    assert status == "ok"
    assert (significant, emitted) == (2, 2)

    # cold start asks for the full 7-day window
    assert edgar.search_params[0]["startdt"] == "2026-07-19"
    assert edgar.search_calls == 2                      # pagination exercised
    assert sorted(edgar.filing_fetches) == ["f1.xml", "f2.xml", "f3.xml", "f4.xml", "f5.xml"]

    recorded = _recorded_filings()
    assert [r["doc_id"] for r in recorded] == ALL_ADSH
    assert [r["extraction_status"] for r in recorded] == (
        ["parsed_ok"] * 4 + ["parse_failed"]            # f5 is truncated XML
    )
    assert all(r["report_type"] == "4" for r in recorded)
    assert all(r["member_id"] is None for r in recorded)   # never a House member

    assert [(a["ticker"], a["severity"]) for a in _alerts()] == [
        ("NWSY", "CRITICAL"), ("ATFH", "HIGH"),
    ]


def test_second_run_fetches_only_the_new_filing(monkeypatch):
    """Stage 2's core claim, with prior state seeded on an isolated DB."""
    _seed_processed(ALL_ADSH[:4], filing_date="2026-07-25")
    edgar = _install(monkeypatch, FakeEdgar())

    significant, emitted, status = rule.run(None, TODAY, emit_alerts=True)

    assert status == "ok"
    # window narrowed off the watermark (2026-07-25) minus the 2-day lookback
    assert edgar.search_params[0]["startdt"] == "2026-07-23"
    # exactly one document fetched — the one filing not already recorded
    assert edgar.filing_fetches == ["f5.xml"]
    assert edgar.history_fetches == []

    note = _activity()[-1]["notes"]
    assert "in_window=5" in note and "already_processed=4" in note and "examined=1" in note
    assert (significant, emitted) == (0, 0)             # f5 is unparseable


def test_run_with_nothing_new_fetches_no_documents_at_all(monkeypatch):
    _seed_processed(ALL_ADSH, filing_date="2026-07-26")
    edgar = _install(monkeypatch, FakeEdgar())

    significant, emitted, status = rule.run(None, TODAY, emit_alerts=True)

    assert (significant, emitted, status) == (0, 0, "ok")
    assert edgar.document_calls == []                   # the whole point
    assert "examined=0" in _activity()[-1]["notes"]


def test_watermark_older_than_the_cap_is_floored(monkeypatch):
    _seed_processed(["0001000099-26-000099"], filing_date="2026-06-01")
    edgar = _install(monkeypatch, FakeEdgar())

    rule.run(None, TODAY, emit_alerts=True)

    # not 2026-05-30 — a long outage must not queue an unbounded scan
    assert edgar.search_params[0]["startdt"] == "2026-07-19"


def test_explicit_since_overrides_the_watermark(monkeypatch):
    _seed_processed(ALL_ADSH, filing_date="2026-07-26")
    edgar = _install(monkeypatch, FakeEdgar())

    rule.run("2026-07-01", TODAY, emit_alerts=True)

    assert edgar.search_params[0]["startdt"] == "2026-07-01"
    assert "explicit --since" in _activity()[-1]["notes"]


# ---------------------------------------------------------------------------
# Stage 3 — incremental commit + activity_log on every path
# ---------------------------------------------------------------------------

def test_interruption_keeps_processed_rows_and_still_logs(monkeypatch):
    """Mid-run interruption: work already done survives, and the run is visible.

    Models an interruption Python can observe (KeyboardInterrupt/SIGTERM). A
    SIGKILL — what subprocess.run sends on timeout — cannot be observed at all;
    durability there rests on the per-filing commits this test checks, and
    visibility on the scheduler's SCHEDULER_JOB_FAILURE net (tested below).
    """
    edgar = _install(
        monkeypatch,
        FakeEdgar(raise_on_filing_fetch=3, exception=KeyboardInterrupt("killed")),
    )

    with pytest.raises(KeyboardInterrupt):
        rule.run(None, TODAY, emit_alerts=True)

    # f1 and f2 were fully processed before the interruption and are committed
    assert [r["doc_id"] for r in _recorded_filings()] == ALL_ADSH[:2]
    # f1's alert is durable too (insert_alert commits per row)
    assert [a["ticker"] for a in _alerts()] == ["NWSY"]
    assert edgar.filing_fetches == ["f1.xml", "f2.xml", "f3.xml"]

    # and the run is not invisible — logged, and logged as failed, not as a
    # clean scan that happened to examine fewer filings
    activity = _activity()
    assert len(activity) == 1
    assert "status=failed" in activity[0]["notes"]
    assert "KeyboardInterrupt" in activity[0]["notes"]
    assert activity[0]["events_scanned"] == 3


def test_rows_are_committed_during_the_run_not_at_the_end(monkeypatch):
    """Progress is durable *while the scan is still running*.

    A separate connection can only see committed rows, so observing them
    mid-scan is what actually proves the per-filing commit — the interruption
    test above cannot, because `run()`'s `finally` commits on its way out
    regardless. This is the only property that survives a SIGKILL, which no
    test can reproduce.
    """
    observed: dict[int, int] = {}

    def observe(n: int) -> None:
        conn = jpt_common.db_connection()          # independent connection
        observed[n] = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE source='sec_form4'"
        ).fetchone()[0]
        conn.close()

    _install(monkeypatch, FakeEdgar(on_filing_fetch=observe))
    rule.run(None, TODAY, emit_alerts=True)

    # before the 1st fetch nothing is done; by the 3rd, the first two are durable
    assert observed[1] == 0
    assert observed[2] == 1
    assert observed[3] == 2
    assert observed[5] == 4


def test_activity_row_is_written_on_a_clean_run(monkeypatch):
    _install(monkeypatch, FakeEdgar())
    rule.run(None, TODAY, emit_alerts=True)

    activity = _activity()
    assert len(activity) == 1
    assert activity[0]["events_scanned"] == 5
    assert activity[0]["events_flagged"] == 2
    assert activity[0]["alerts_emitted"] == 2
    assert activity[0]["duration_seconds"] is not None
    assert "status=ok" in activity[0]["notes"]


# ---------------------------------------------------------------------------
# Stage 4 — failures are loud
# ---------------------------------------------------------------------------

def test_non_200_search_fails_the_run_instead_of_reporting_zero(monkeypatch):
    _install(monkeypatch, FakeEdgar(search_status=503))

    significant, emitted, status = rule.run(None, TODAY, emit_alerts=True)

    assert status == "failed"
    assert (significant, emitted) == (0, 0)
    note = _activity()[-1]["notes"]
    assert "status=failed" in note
    assert "HTTP 503" in note
    assert _recorded_filings() == []


def test_search_non_200_raises_rather_than_truncating(monkeypatch):
    _install(monkeypatch, FakeEdgar(search_status=403))
    with pytest.raises(rule.EdgarUnavailable) as caught:
        rule.search_form4_filings("2026-07-19", TODAY)
    assert "HTTP 403" in str(caught.value)


def test_every_document_fetch_failing_is_a_failure_not_a_quiet_day(monkeypatch):
    _install(monkeypatch, FakeEdgar(document_status=503))

    significant, emitted, status = rule.run(None, TODAY, emit_alerts=True)

    assert status == "failed"
    note = _activity()[-1]["notes"]
    assert "all 5 filing fetches failed" in note
    assert "fetch_failed=5" in note
    # nothing recorded, so a transient outage is retried rather than lost
    assert _recorded_filings() == []


def test_scheduler_argv_is_accepted(monkeypatch):
    """The exact argv api/main.py:172 uses must parse — no exit-2."""
    args = rule.build_parser().parse_args(["--emit-alerts"])
    assert args.emit_alerts is True
    assert args.since is None
    assert args.max_seconds == rule.TIME_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# Stage 5 — time budget, resumable partial
# ---------------------------------------------------------------------------

def test_time_budget_stops_early_logs_partial_and_next_run_resumes(monkeypatch):
    clock = FakeClock(cost_per_call=10.0)
    edgar = _install(monkeypatch, FakeEdgar(clock=clock), clock=clock)

    # 2 search pages + 2 documents = 40s of the 45s budget; the third filing's
    # deadline check then trips.
    significant, emitted, status = rule.run(
        None, TODAY, emit_alerts=True, time_budget=45.0
    )

    assert status == "partial"
    note = _activity()[-1]["notes"]
    assert "status=partial" in note
    assert "resume" in note
    processed_first = [r["doc_id"] for r in _recorded_filings()]
    assert 0 < len(processed_first) < 5, processed_first

    # Second run, generous budget: picks up exactly where the first stopped.
    clock2 = FakeClock(cost_per_call=0.0)
    edgar2 = _install(monkeypatch, FakeEdgar(clock=clock2), clock=clock2)
    significant2, emitted2, status2 = rule.run(None, TODAY, emit_alerts=True)

    assert status2 == "ok"
    assert [r["doc_id"] for r in _recorded_filings()] == ALL_ADSH   # no gap
    assert set(edgar2.filing_fetches).isdisjoint(edgar.filing_fetches)
    assert len(_activity()) == 2


def test_search_pagination_truncated_by_the_budget_is_reported_partial(monkeypatch):
    clock = FakeClock(cost_per_call=10.0)
    _install(monkeypatch, FakeEdgar(clock=clock), clock=clock)

    _, _, status = rule.run(None, TODAY, emit_alerts=True, time_budget=5.0)

    assert status == "partial"
    # both reasons survive into the note — pagination was cut short *and* the
    # filing loop never got a turn
    note = _activity()[-1]["notes"]
    assert "paginating" in note
    assert "budget" in note


# ---------------------------------------------------------------------------
# Stage 4 (end to end) — the scheduler's own invocation path
#
# These drive api.main._run_rule, which is what production uses: it spawns
# `[python, script, "--emit-alerts"]` in a subprocess with a 300s timeout. The
# script is a thin wrapper that swaps in the fake transport and then calls the
# real rule_06_form4.main(), so the argv contract, exit code and the
# SCHEDULER_JOB_FAILURE net are all exercised without touching the network.
# ---------------------------------------------------------------------------

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TESTS = os.path.dirname(os.path.abspath(__file__))

WRAPPER = """
import sys
sys.path.insert(0, {repo!r})
sys.path.insert(0, {tests!r})
import rule_06_form4 as rule
from edgar_fake import FakeEdgar
rule._get = FakeEdgar(search_status={search_status})
rule.main()
"""


def _wrapper(tmp_path, search_status: int = 200):
    script = tmp_path / f"rule_06_wrapped_{search_status}.py"
    script.write_text(WRAPPER.format(repo=REPO, tests=TESTS, search_status=search_status))
    return str(script)


def _scheduler_failures() -> list[str]:
    return [
        r["notes"] for r in _rows(
            "SELECT notes FROM activity_log WHERE source='SCHEDULER_JOB_FAILURE' ORDER BY id DESC"
        )
    ]


def test_scheduler_invocation_exits_zero_and_logs_no_failure(tmp_path):
    from api import main as appmain

    result = appmain._run_rule(_wrapper(tmp_path, search_status=200))

    assert result == "ok", result                  # exit 0 — not exit-2
    assert _scheduler_failures() == []
    assert len(_activity()) == 1                   # the rule's own row
    assert "status=ok" in _activity()[0]["notes"]


def test_forced_rule06_failure_produces_a_scheduler_failure_row(tmp_path):
    from api import main as appmain

    result = appmain._run_rule(_wrapper(tmp_path, search_status=503))

    assert result != "ok"
    failures = _scheduler_failures()
    assert failures, "a failed RULE_06 run must leave a SCHEDULER_JOB_FAILURE row"
    assert "exited with code 1" in failures[0]
    # both records exist: the rule's own diagnosis and the scheduler's net
    assert "HTTP 503" in _activity()[-1]["notes"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
