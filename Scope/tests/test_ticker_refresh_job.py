"""The scheduled ticker refresh must never touch congressional data.

`resolve_tickers` has two halves:

  upsert_sec_tickers    reference refresh of `tickers`, conflict key `symbol`. Safe.
  resolve_transactions  UPDATEs `transactions.ticker_id` — congressional trade data,
                        the dataset RULE_01B / RULE_02 / RULE_CLUSTER read.

Only the first is scheduled. The separation cannot be a flag, because the scheduler
invokes every job as `[sys.executable, <script>, "--emit-alerts"]` and has no way to
pass one — so it is structural, and these tests walk the call graph to prove it rather
than trusting a comment.

`tickers` also had no schedule at all before this: one writer, refreshed only by hand,
with RULE_16's CINS fallback resolving against it. Staleness there is silent, which is
why it needs a cadence. Measured locally: 10,619 rows last touched 2026-07-09, 18 days
before this was written. Prod UNVERIFIED.
"""
from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REPO = os.path.join(os.path.dirname(__file__), "..")
UNSAFE = "resolve_transactions"


def _call_graph(path):
    """{function name -> set of names it calls} for one module."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    graph = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    if isinstance(f, ast.Name):
                        calls.add(f.id)
                    elif isinstance(f, ast.Attribute):
                        calls.add(f.attr)
            graph[node.name] = calls
    return graph


def _reaches(graph, start, target, seen=None):
    seen = seen or set()
    if start in seen:
        return False
    seen.add(start)
    for callee in graph.get(start, ()):
        if callee == target or _reaches(graph, callee, target, seen):
            return True
    return False


# ── the structural guarantee ─────────────────────────────────────────────────

def test_the_safe_half_cannot_reach_the_congressional_write():
    g = _call_graph(os.path.join(REPO, "resolve_tickers.py"))
    assert "refresh_tickers_only" in g, "the safe entry point is gone"
    assert not _reaches(g, "refresh_tickers_only", UNSAFE), (
        f"refresh_tickers_only can reach {UNSAFE}(), which UPDATEs congressional "
        "transactions — the scheduled job must not be able to")


def test_the_scheduled_script_cannot_reach_it_either():
    g = _call_graph(os.path.join(REPO, "scripts", "refresh_tickers.py"))
    assert not _reaches(g, "main", UNSAFE)
    # Executable references only — the module docstring names `resolve_transactions`
    # precisely to say it is unreachable, and a naive substring scan would flag that.
    tree = ast.parse(open(os.path.join(REPO, "scripts", "refresh_tickers.py"),
                          encoding="utf-8").read())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
            {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
             for a in n.names}
    assert UNSAFE not in names, "the scheduled script references the unsafe half in code"


def test_the_manual_main_DOES_still_run_both():
    """The control. Without it, the two tests above pass on a module that lost the
    unsafe half entirely — which would be a silent capability regression, not a fix."""
    g = _call_graph(os.path.join(REPO, "resolve_tickers.py"))
    assert _reaches(g, "main", UNSAFE), "manual main() no longer resolves transactions"
    assert _reaches(g, "main", "refresh_tickers_only")


# ── the schedule ─────────────────────────────────────────────────────────────

def _cron():
    from api.main import _CRON_SCHEDULE
    return _CRON_SCHEDULE


def test_only_the_safe_script_is_scheduled():
    cron = _cron()
    from api.main import _RULE_SCHEDULE
    both = {**cron, **_RULE_SCHEDULE}
    assert "scripts/refresh_tickers.py" in cron
    assert "resolve_tickers.py" not in both, (
        "resolve_tickers.py is scheduled — that runs resolve_transactions() unattended")
    assert not any("resolve_tickers" in k for k in both), \
        f"a resolve_tickers entry is scheduled: {[k for k in both if 'resolve_tickers' in k]}"


def test_it_is_weekly_not_daily():
    spec = _cron()["scripts/refresh_tickers.py"]
    assert spec.get("day_of_week"), f"no day_of_week — this would run daily: {spec}"
    assert spec["day_of_week"] == "sun"


def test_the_scheduled_script_survives_the_emit_alerts_flag():
    """The generate_brief lesson: the scheduler passes --emit-alerts to EVERY job, and
    an unrecognised argument makes argparse exit 2. That is how the morning brief failed
    100% of its runs while looking scheduled."""
    from scripts.refresh_tickers import build_parser
    args = build_parser().parse_args(["--emit-alerts"])
    assert args.emit_alerts is True


def test_the_scheduled_path_actually_exists_on_disk():
    """A cron entry pointing at a missing file raises FileNotFoundError every run —
    the exact live bug found in the cleanup pass."""
    for path in _cron():
        assert os.path.exists(os.path.join(REPO, path)), f"scheduled but missing: {path}"


# ── the refresh does what it is for ──────────────────────────────────────────

def test_the_refresh_writes_tickers_and_stamps_updated_at(monkeypatch):
    import resolve_tickers as rt
    from jpt_common import db_connection

    monkeypatch.setattr(rt, "download_sec_tickers", lambda: {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
    })
    n = rt.refresh_tickers_only()
    assert n == 2

    conn = db_connection()
    rows = conn.execute(
        "SELECT symbol, company_name, updated_at FROM tickers "
        "WHERE symbol IN ('AAPL','MSFT') ORDER BY symbol").fetchall()
    # the congressional table must be untouched by the safe half
    txn_cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    conn.close()

    assert [r["symbol"] for r in rows] == ["AAPL", "MSFT"]
    assert all(r["updated_at"] for r in rows), "updated_at not stamped — staleness "\
                                               "would be undetectable"
    assert "ticker_id" in txn_cols  # sanity: the column the unsafe half would rewrite


def test_the_refresh_logs_its_activity(monkeypatch):
    """Silence is the failure mode being fixed; a run must leave a trace."""
    import resolve_tickers as rt
    from jpt_common import db_connection

    monkeypatch.setattr(rt, "download_sec_tickers", lambda: {
        "0": {"cik_str": 1, "ticker": "ZZZZ", "title": "Z Corp"}})
    rt.refresh_tickers_only()

    conn = db_connection()
    row = conn.execute(
        "SELECT source, notes FROM activity_log WHERE source='REFRESH_TICKERS' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None, "the refresh left no activity_log row"
    assert "upserted=" in (row["notes"] or "")
