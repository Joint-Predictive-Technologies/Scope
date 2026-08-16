"""EDGAR filing-metadata ingestion (m018).

The property under test is NOT "rows arrive". It is that a consumer can tell an
ingestion artifact from real filer behaviour — because the last attempt at a
rate-of-change metric died on exactly that, with 71% of its corpus landing in a
single catch-up run and reading as a four-month burst.
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3

import pytest

import scripts.ingest_edgar_filings as ing
from jpt_common import _run_migrations


@pytest.fixture()
def conn():
    """A real database: the shipped schema, then the real migration chain.

    Hand-rolling `edgar_filings` here would test a CREATE TABLE written in this file
    rather than the one that will run against prod — and m018 landing wrong is exactly
    the failure worth catching.
    """
    c = sqlite3.connect(":memory:")
    schema = os.path.join(os.path.dirname(__file__), "..", "schema_sqlite.sql")
    c.executescript(open(schema, encoding="utf-8").read())
    _run_migrations(c)
    yield c
    c.close()


# ── the schema exists, and is not attached to a moat table ────────────────────

def test_m018_creates_both_tables_with_the_columns_a_consumer_needs(conn):
    assert conn.execute(
        "SELECT 1 FROM scope_migrations WHERE name='m018_edgar_filing_metadata'"
    ).fetchone()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(edgar_filings)")}
    assert {"cik", "form_type", "filing_date", "accession_number",
            "ingested_at", "is_backfill", "first_seen_run_id"} <= cols
    watch = {r[1] for r in conn.execute("PRAGMA table_info(edgar_cik_watch)")}
    assert {"cik", "monitoring_since", "first_run_id"} <= watch


def test_the_moat_tables_gained_nothing(conn):
    """m018 is additive. `filings` in particular must not have grown SEC columns —
    it is the congressional PTR corpus, keyed on member_id, and IS read by rules."""
    fcols = {r[1] for r in conn.execute("PRAGMA table_info(filings)")}
    assert "accession_number" not in fcols
    assert "is_backfill" not in fcols


def test_no_rule_or_gate_module_reads_edgar_filings():
    """Stated over the SOURCE, not over one code path.

    The guard is a grep because what it protects against is a future edit inside the
    scoring engine, not a call today's tests happen to exercise. Reads specifically —
    `jpt_common` legitimately CREATEs these tables in m018, so the bare name would
    match the schema definition itself.
    """
    root = os.path.join(os.path.dirname(__file__), "..")
    watched = [os.path.join(root, "jpt_common.py")]
    watched += sorted(glob.glob(os.path.join(root, "rule_*.py")))
    watched += sorted(glob.glob(os.path.join(root, "scripts", "rule_*.py")))
    for extra in ("enrich_scores.py", "insider_clusters.py", "rule_cluster.py"):
        watched.append(os.path.join(root, "scripts", extra))

    reads = re.compile(r"(FROM|JOIN|UPDATE|INTO)\s+edgar_(filings|cik_watch)", re.I)
    offenders = [os.path.basename(p) for p in watched
                 if os.path.exists(p) and reads.search(open(p, encoding="utf-8").read())]
    assert not offenders, (
        f"{offenders} query the EDGAR metadata tables. This is a new, unproven data "
        "surface with no accumulated organic history; wiring it into detection or "
        "scoring is a moat change and needs its own gated pass.")


# ── the backfill rule, which is the reason the table exists ───────────────────

def test_first_run_marks_everything_backfill_including_today():
    """Clause (a). We did not watch these arrive; we found them.

    The same-day case is the one that matters — it is the one a naive
    `filing_date < today` test would wrongly call organic on day one, seeding a
    baseline with a discovery event.
    """
    assert ing.classify_backfill("2020-01-01", "2026-08-16 12:00:00", first_run=True) == 1
    assert ing.classify_backfill("2026-08-16", "2026-08-16 12:00:00", first_run=True) == 1


def test_later_poll_marks_older_documents_backfill():
    """Clause (b). A late-surfacing amendment is a discovery, not new activity."""
    assert ing.classify_backfill("2026-08-15", "2026-08-16 12:00:00", first_run=False) == 1


def test_later_poll_marks_genuinely_new_filings_organic():
    """The only path to `is_backfill = 0`: first seen by a later poll, dated on or
    after monitoring began."""
    assert ing.classify_backfill("2026-08-17", "2026-08-16 12:00:00", first_run=False) == 0
    assert ing.classify_backfill("2026-08-16", "2026-08-16 12:00:00", first_run=False) == 0


# ── end to end, with EDGAR stubbed ────────────────────────────────────────────

def _stub(monkeypatch, payload):
    monkeypatch.setattr(ing, "fetch_filings",
                        lambda cik, throttle, include_history=False: list(payload))


def _f(acc, form, date):
    return {"accession_number": acc, "cik": "0000320193", "form_type": form,
            "filing_date": date, "report_date": None, "primary_document": None,
            "is_amendment": 1 if form.endswith("/A") else 0}


def test_two_run_lifecycle_separates_the_ingestion_event_from_the_filing(conn, monkeypatch):
    """The whole point, exercised as a sequence rather than asserted per-row.

    Run 1 discovers history — all backfill. Run 2 finds one genuinely new filing —
    organic — and must NOT relabel anything run 1 wrote.
    """
    t = ing._Throttle(1000)

    _stub(monkeypatch, [_f("A-1", "10-K", "2024-11-01"), _f("A-2", "8-K", "2026-08-01")])
    c1 = ing.ingest_cik(conn, "0000320193", "AAPL", "run-1", t, apply=True)
    assert c1["first_run"] and c1["new"] == 2 and c1["new_backfill"] == 2
    assert c1["new_organic"] == 0

    since = conn.execute(
        "SELECT monitoring_since FROM edgar_cik_watch WHERE cik='0000320193'").fetchone()[0]

    # Run 2: the two known accessions again, plus one filed after monitoring began.
    _stub(monkeypatch, [_f("A-1", "10-K", "2024-11-01"), _f("A-2", "8-K", "2026-08-01"),
                        _f("A-3", "4", "2099-01-01")])
    c2 = ing.ingest_cik(conn, "0000320193", "AAPL", "run-2", t, apply=True)
    assert not c2["first_run"]
    assert c2["already_known"] == 2, "re-polling must not re-insert"
    assert c2["new"] == 1 and c2["new_organic"] == 1 and c2["new_backfill"] == 0

    rows = dict(conn.execute(
        "SELECT accession_number, is_backfill FROM edgar_filings").fetchall())
    assert rows == {"A-1": 1, "A-2": 1, "A-3": 0}
    runs = dict(conn.execute(
        "SELECT accession_number, first_seen_run_id FROM edgar_filings").fetchall())
    assert runs["A-1"] == "run-1" and runs["A-3"] == "run-2", "provenance must survive"
    assert conn.execute(
        "SELECT poll_count FROM edgar_cik_watch WHERE cik='0000320193'").fetchone()[0] == 2
    assert conn.execute(
        "SELECT monitoring_since FROM edgar_cik_watch WHERE cik='0000320193'"
    ).fetchone()[0] == since, "monitoring_since is set once and never moves"


def test_a_rerun_cannot_flip_an_existing_rows_verdict(conn, monkeypatch):
    """A row's `is_backfill` is fixed at first sight.

    Without this, a second run over the same accession could rewrite history —
    and a flag that the latest run can redefine is not evidence of anything.
    """
    t = ing._Throttle(1000)
    _stub(monkeypatch, [_f("A-1", "8-K", "2099-06-01")])
    ing.ingest_cik(conn, "0000320193", "AAPL", "run-1", t, apply=True)
    assert conn.execute("SELECT is_backfill FROM edgar_filings").fetchone()[0] == 1

    ing.ingest_cik(conn, "0000320193", "AAPL", "run-2", t, apply=True)
    assert conn.execute("SELECT is_backfill FROM edgar_filings").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM edgar_filings").fetchone()[0] == 1


def test_dry_run_fetches_but_writes_nothing(conn, monkeypatch):
    t = ing._Throttle(1000)
    _stub(monkeypatch, [_f("A-1", "10-K", "2024-11-01")])
    c = ing.ingest_cik(conn, "0000320193", "AAPL", "run-1", t, apply=False)
    assert c["new"] == 1
    assert conn.execute("SELECT COUNT(*) FROM edgar_filings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM edgar_cik_watch").fetchone()[0] == 0


def test_broad_form_coverage_is_stored_verbatim(conn, monkeypatch):
    """The blocker was that no form type but 'PTR' and '4' existed anywhere. Form
    strings are stored as EDGAR writes them, with amendments flagged rather than
    normalised away."""
    t = ing._Throttle(1000)
    _stub(monkeypatch, [_f("A-1", "10-K", "2024-01-01"), _f("A-2", "S-1", "2024-02-01"),
                        _f("A-3", "8-K/A", "2024-03-01"), _f("A-4", "S-3", "2024-04-01")])
    ing.ingest_cik(conn, "0000320193", "AAPL", "run-1", t, apply=True)
    got = dict(conn.execute("SELECT form_type, is_amendment FROM edgar_filings"))
    assert got == {"10-K": 0, "S-1": 0, "8-K/A": 1, "S-3": 0}


def test_one_document_filed_under_two_ciks_is_kept_for_both(conn, monkeypatch):
    """🔴 The regression this table nearly shipped with.

    An accession is unique per DOCUMENT, not per filer. EDGAR lists Berkshire's 13G on
    Apple under both CIKs with the same accession `0001193125-24-036431`. Keyed on the
    accession alone, the second filer's copy vanished into `ON CONFLICT DO NOTHING` —
    96 rows of 204,053 on the first real pass — and a per-CIK velocity metric would
    have under-counted a filer with nothing in its own data to show why.

    Constructed here rather than fetched so the property is pinned even if those two
    companies never co-file again.
    """
    t = ing._Throttle(1000)
    shared = "0001193125-24-036431"

    for cik in ("0000320193", "0001067983"):
        monkeypatch.setattr(
            ing, "fetch_filings",
            lambda k, th, include_history=False, _c=cik: [
                {"accession_number": shared, "cik": _c, "form_type": "SC 13G/A",
                 "filing_date": "2024-02-14", "report_date": None,
                 "primary_document": None, "is_amendment": 1}])
        ing.ingest_cik(conn, cik, None, "run-1", t, apply=True)

    got = [r[0] for r in conn.execute(
        "SELECT cik FROM edgar_filings WHERE accession_number=? ORDER BY cik", (shared,))]
    assert got == ["0000320193", "0001067983"], (
        "a document filed under two CIKs must count for both filers")


# ── transport ─────────────────────────────────────────────────────────────────

def test_a_source_failure_raises_rather_than_returning_an_empty_batch(conn, monkeypatch):
    """An unreachable EDGAR must not look like a filer who stopped filing — that
    reading is precisely the false signal a velocity metric would emit."""
    def boom(cik, throttle, include_history=False):
        raise ing.SourceUnavailable("data.sec.gov -> HTTP 403")
    monkeypatch.setattr(ing, "fetch_filings", boom)
    with pytest.raises(ing.SourceUnavailable):
        ing.ingest_cik(conn, "0000320193", "AAPL", "run-1", ing._Throttle(1000), apply=True)
    assert conn.execute("SELECT COUNT(*) FROM edgar_cik_watch").fetchone()[0] == 0


def test_throttle_never_exceeds_sec_published_ceiling():
    """`--rate 1000` must not become 1000 req/s. SEC's limit is 10; the cap is in the
    throttle rather than in argparse so no future call site can route around it."""
    assert ing._Throttle(1000).interval >= 1.0 / ing.MAX_RATE
    assert ing._Throttle(ing.DEFAULT_RATE).interval == pytest.approx(1 / 8.0)
