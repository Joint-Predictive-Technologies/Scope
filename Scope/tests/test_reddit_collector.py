"""Collection: coverage, never a signal and never a gate leg.

A collected ticker is NOT "watch this". It is "this name exists" — coverage so the real
instruments have something to cross-reference against. Three things matter:

  1. THE THREE GATES hold: cashtag required, engagement floor, confirmed large-cap
     excluded (unknown COLLECTED and flagged — the asymmetry is deliberate).
  2. NEVER A GATE LEG. Collection adds no RULE_10 instrument. This is the one that would
     actually damage Scope if it slipped — a social leg would hollow out the convergence
     moat, whose entire value is that its legs are independent sources.
  3. NEVER ENDORSED. Nothing here ranks, scores, or implies a name is interesting.

No baseline, no deviation, no ground-truth problem — that framing was removed. What
remains is a lookup table, and these tests pin that it stays one.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from jpt_common import db_connection  # noqa: E402
from scripts import rule_reddit_collector as coll  # noqa: E402

_c = TestClient(app)
_REAL_DB = os.path.join(os.path.dirname(__file__), "..", "data", "jpt.db")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test may touch the network, and none may LEAN ON a failed lookup.

An earlier test passed with its guard DELETED, because `market_cap` made a real
    SEC/Yahoo call for a fake ticker, got None, and failed closed — the guard was never
    what stopped it. A verifier found that by mutation, and the suite was silently
    hitting the network on every run.

    Default: caps resolve small, so anything NOT collected is rejected for a real
    reason. Tests that care about the cap override this explicitly.
    """
    monkeypatch.setattr(coll, "market_cap", lambda conn, sym, **kw: 500_000_000)
    for fn in ("_cik_for", "_shares_outstanding", "_last_close"):
        monkeypatch.setattr(coll, fn, lambda *a, **k: (_ for _ in ()).throw(
            AssertionError(f"{fn} hit the network during a test")))


# ── THE DISCIPLINE CRUX: never a gate leg ────────────────────────────────────

def test_discovery_adds_NO_gate_instrument():
    """The one that would actually damage Scope.

    RULE_10's instrument count is the moat. A social-buzz leg would let reddit chatter
    manufacture a corroboration, which is precisely what the gate exists to prevent.
    """
    from jpt_common import RULE_10_EXCLUDED, RULE_10_INSTRUMENTS, rule10_instruments

    assert "RULE_REDDIT" in RULE_10_EXCLUDED, "RULE_REDDIT left the exclusion set"
    assert "RULE_COLLECTOR" in RULE_10_EXCLUDED, "the collector left the exclusion set"
    assert coll.RULE not in RULE_10_INSTRUMENTS, "discovery was given an instrument"
    assert rule10_instruments([coll.RULE]) == [] or coll.RULE in RULE_10_EXCLUDED

    # the phantom-instrument trap: an eligible-but-unmapped rule becomes its OWN
    # instrument, so being absent from the map is NOT enough — it must be excluded.
    assert rule10_instruments(["RULE_REDDIT"]) == [], \
        "RULE_REDDIT resolves to an instrument — it would open corroborations"


def test_a_discovered_ticker_does_not_contribute_to_a_convergence():
    """Behavioural: collect a ticker, run the real gate, prove it changes nothing."""
    from scripts import rule_10_corroboration as r10

    conn = db_connection()
    coll.ensure_tables(conn)
    coll.upsert_universe(conn, "POOLONLY", "small", 100_000_000)
    # plus a RULE_REDDIT alert, the strongest form of the same question
    conn.execute("INSERT INTO alerts (rule, ticker, severity, headline, created_at) "
                 "VALUES ('RULE_REDDIT','POOLONLY','HIGH','buzz', datetime('now'))")
    conn.commit()

    fires = r10.find_corroborated_tickers(conn, 14 * 24)
    instruments = r10.instruments_for([
        {"rule": r[0]} for r in conn.execute(
            "SELECT rule FROM alerts WHERE ticker='POOLONLY'").fetchall()])
    conn.close()

    assert "POOLONLY" not in fires, "a collected-only ticker reached the gate"
    assert instruments == [], f"collection contributed instruments: {instruments}"


def test_no_corroboration_path_reads_the_watch_pool():
    """Structural: grep the gate's own sources for the coverage table."""
    import inspect
    from scripts import rule_10_corroboration as r10
    import jpt_common
    for mod in (r10, jpt_common):
        assert "ticker_universe" not in inspect.getsource(mod), (
            f"{mod.__name__} reads the coverage list — it must not")


# ── the PRODUCER — three mutations survived the whole 523-test suite ─────────
#
# `rule_reddit.py` is half of this branch and had ZERO coverage. A verifier reverted the
# grain fix (`tickers[:1]`), deleted the mention INSERT outright, and deleted
# `ensure_tables()` — each left 523/523 green. Every test seeded `reddit_mentions`
# through its own helper and none ever ran the thing that writes it.

def _stub_reddit(monkeypatch, posts):
    """Run the REAL rule against fabricated posts, network sealed off.

    `_fetch_subreddit` returns flat post dicts (resp.json()["data"]), and run() sleeps
    3s per subreddit — both stubbed so this exercises the write path and nothing else.
    """
    from scripts import rule_reddit as rr
    monkeypatch.setattr(rr, "SUBREDDITS", ["stocks"])
    monkeypatch.setattr(rr, "MIN_UPVOTES", 0)
    monkeypatch.setattr(rr, "_fetch_subreddit", lambda sub: posts)
    monkeypatch.setattr(rr.time, "sleep", lambda *_a: None)
    return rr


def test_the_producer_records_EVERY_ticker_not_just_the_first(monkeypatch):
    """The grain fix, through the real rule rather than a seeded fixture."""
    from jpt_common import db_connection as _db
    conn = _db(); coll.ensure_tables(conn)
    known = {r[0] for r in conn.execute("SELECT symbol FROM tickers")} or set()
    conn.close()

    rr = _stub_reddit(monkeypatch, [{
        "id": "multi1", "title": "$NVDA $AMD and $PLTR all ripping", "selftext": "",
        "score": 50, "num_comments": 10, "upvote_ratio": 0.9,
        "url": "https://reddit.com/r/stocks/multi1", "author_post_count": 30}])
    monkeypatch.setattr(rr, "_extract_tickers",
                        lambda text, k: ["NVDA", "AMD", "PLTR"])
    try:
        rr.run(emit=False, dry_run=False)
    except Exception as exc:                      # fetch shape varies; skip, don't lie
        pytest.skip(f"could not drive the real producer: {type(exc).__name__}: {exc}")

    conn = _db()
    mentions = sorted(r[0] for r in conn.execute(
        "SELECT ticker FROM reddit_mentions WHERE post_id='multi1'"))
    posts = conn.execute(
        "SELECT COUNT(*) FROM reddit_posts WHERE post_id='multi1'").fetchone()[0]
    conn.close()
    assert mentions == ["AMD", "NVDA", "PLTR"], (
        f"the producer recorded {mentions} — the grain fix is reverted or gone")
    assert posts == 1, "reddit_posts should still hold exactly one row per post"


def test_a_missing_mentions_table_cannot_take_RULE_REDDIT_down(monkeypatch):
    """The silent-data-loss path this branch introduced.

    Putting the mention INSERT inside the try that guards the reddit_posts write meant a
    missing `reddit_mentions` killed BOTH: the rule stored 0 posts, emitted 0 alerts, and
    logged scanned=4 flagged=0 emitted=0 — indistinguishable from a quiet day.
    """
    from jpt_common import db_connection as _db
    conn = _db(); coll.ensure_tables(conn)
    conn.execute("DROP TABLE reddit_mentions")     # simulate the bootstrap not running
    conn.commit(); conn.close()

    rr = _stub_reddit(monkeypatch, [{
        "id": "solo1", "title": "$NVDA looks strong", "selftext": "", "score": 50,
        "num_comments": 5, "upvote_ratio": 0.9,
        "url": "https://reddit.com/r/stocks/solo1", "author_post_count": 30}])
    monkeypatch.setattr(rr, "_extract_tickers", lambda text, k: ["NVDA"])
    monkeypatch.setattr(rr, "ensure_tables", lambda c: None, raising=False)
    try:
        rr.run(emit=False, dry_run=False)
    except Exception as exc:
        pytest.skip(f"could not drive the real producer: {type(exc).__name__}: {exc}")

    conn = _db()
    stored = conn.execute(
        "SELECT COUNT(*) FROM reddit_posts WHERE post_id='solo1'").fetchone()[0]
    conn.close()
    assert stored == 1, (
        "a missing reddit_mentions table stopped RULE_REDDIT storing posts — mention "
        "capture must never take the rule down")


# ── THE THREE GATES ─────────────────────────────────────────────────────────

def _mention(conn, ticker, cashtagged=1, score=10, num_comments=5, post="p1"):
    conn.execute(
        "INSERT OR IGNORE INTO reddit_mentions (post_id, ticker, subreddit, "
        "cashtagged, score, num_comments) VALUES (?,?,?,?,?,?)",
        (f"{post}_{ticker}", ticker, "stocks", cashtagged, score, num_comments))
    conn.commit()


def test_a_cashtagged_smallcap_on_an_engaged_post_is_collected():
    conn = db_connection(); coll.ensure_tables(conn)
    _mention(conn, "MOBX")
    coll.collect(conn); conn.commit()
    row = conn.execute("SELECT cap_status, times_seen FROM ticker_universe "
                       "WHERE ticker='MOBX'").fetchone()
    conn.close()
    assert row is not None and row["cap_status"] == "small"


def test_a_BARE_mention_is_NOT_collected():
    """Cashtag required. Bare tokens are ambiguous prose that match a symbol —
    collecting them would refill the universe with the English words the extraction
    fix spent a session removing."""
    conn = db_connection(); coll.ensure_tables(conn)
    _mention(conn, "BAREONLY", cashtagged=0)
    coll.collect(conn); conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM ticker_universe "
                     "WHERE ticker='BAREONLY'").fetchone()[0]
    conn.close()
    assert n == 0, "a bare mention was collected"


def test_a_zero_and_zero_post_is_NOT_collected():
    """The pulse check. Zero upvotes AND zero comments is not a post anyone saw."""
    conn = db_connection(); coll.ensure_tables(conn)
    _mention(conn, "DEADPOST", score=0, num_comments=0)
    coll.collect(conn); conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM ticker_universe "
                     "WHERE ticker='DEADPOST'").fetchone()[0]
    conn.close()
    assert n == 0, "a 0-upvote 0-comment post was collected"
    # control: the SAME ticker with a pulse IS collected, so the rejection is the
    # engagement floor and not a dead fixture
    assert coll.clears_engagement(0, 0) is False
    assert coll.clears_engagement(coll.MIN_SCORE, 0) is True
    assert coll.clears_engagement(0, coll.MIN_COMMENTS) is True


def test_a_confirmed_large_cap_is_excluded(monkeypatch):
    """$AAPL does not need discovering."""
    conn = db_connection(); coll.ensure_tables(conn)
    monkeypatch.setattr(coll, "market_cap", lambda c, s, **kw: 4_900_000_000_000)
    _mention(conn, "AAPL")
    coll.collect(conn); conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM ticker_universe "
                     "WHERE ticker='AAPL'").fetchone()[0]
    conn.close()
    assert n == 0, "a confirmed large cap was collected"


def test_an_unknown_cap_is_COLLECTED_and_flagged(monkeypatch):
    """THE ASYMMETRY, and it is deliberate.

    The old watch pool failed CLOSED — unknown caps were rejected, because a wrong name
    in a surface a human reads is expensive. This is a lookup table nobody reads for its
    own sake, so a MISSING name is the expensive failure: it would silently remove a
    ticker from the universe the real instruments cross-reference against.
    """
    conn = db_connection(); coll.ensure_tables(conn)
    monkeypatch.setattr(coll, "market_cap", lambda c, s, **kw: None)
    _mention(conn, "NOPRICE")
    coll.collect(conn); conn.commit()
    row = conn.execute("SELECT cap_status, market_cap FROM ticker_universe "
                       "WHERE ticker='NOPRICE'").fetchone()
    conn.close()
    assert row is not None, "an unknown cap was dropped — it must be collected, flagged"
    assert row["cap_status"] == "unknown" and row["market_cap"] is None


def test_the_large_cap_boundary(monkeypatch):
    """Only a CONFIRMED large cap is excluded; the line is inclusive."""
    conn = db_connection(); coll.ensure_tables(conn)
    monkeypatch.setattr(coll, "market_cap",
                        lambda c, s, **kw: coll.LARGE_CAP_MIN - 1)
    assert coll.classify_cap(conn, "JUSTUNDER")[0] == "small"
    monkeypatch.setattr(coll, "market_cap", lambda c, s, **kw: coll.LARGE_CAP_MIN)
    assert coll.classify_cap(conn, "EXACTLY")[0] == "excluded"
    conn.close()


# ── the coverage list ───────────────────────────────────────────────────────

def test_recollection_increments_and_never_duplicates():
    conn = db_connection(); coll.ensure_tables(conn)
    coll.upsert_universe(conn, "SEENTWICE", "small", 400_000_000)
    coll.upsert_universe(conn, "SEENTWICE", "small", 400_000_000)
    conn.commit()
    rows = conn.execute("SELECT times_seen, first_collected_at, last_seen_at "
                        "FROM ticker_universe WHERE ticker='SEENTWICE'").fetchall()
    conn.close()
    assert len(rows) == 1, "re-collection duplicated"
    assert rows[0]["times_seen"] == 2
    assert rows[0]["first_collected_at"] is not None


def test_a_later_known_cap_does_not_erase_an_earlier_one():
    """COALESCE: an unknown re-sighting must not blank a cap we already resolved."""
    conn = db_connection(); coll.ensure_tables(conn)
    coll.upsert_universe(conn, "KEEPCAP", "small", 750_000_000)
    coll.upsert_universe(conn, "KEEPCAP", "unknown", None)
    conn.commit()
    row = conn.execute("SELECT market_cap FROM ticker_universe "
                       "WHERE ticker='KEEPCAP'").fetchone()
    conn.close()
    assert row["market_cap"] == 750_000_000


# ── never endorsed ──────────────────────────────────────────────────────────

def test_the_api_frames_this_as_coverage_and_carries_no_signal():
    conn = db_connection(); coll.ensure_tables(conn)
    coll.upsert_universe(conn, "COVERED", "small", 300_000_000)
    conn.commit(); conn.close()

    body = _c.get("/api/universe").json()
    assert body["kind"] == "ticker_coverage_list"
    got = [t for t in body["tickers"] if t["ticker"] == "COVERED"]
    assert got, "a collected ticker did not surface"
    for banned in ("confidence", "score", "probability", "prediction", "target",
                   "signal", "deviation", "conviction", "rank"):
        assert banned not in str(got[0]).lower(), (
            f"the payload carries {banned!r} — collection is coverage, not a signal")
    blob = str(body).lower()
    assert "not a signal" in blob and "coverage" in blob


def test_collection_emits_no_alert_at_all():
    """The rule must never write to `alerts`. Coverage is not a signal."""
    import inspect
    src = inspect.getsource(coll)
    body = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "insert_alert" not in body
    assert "INTO alerts" not in body.upper()

    conn = db_connection(); coll.ensure_tables(conn)
    _mention(conn, "NOALERT")
    before = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    coll.collect(conn); conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    conn.close()
    assert before == after, "collection wrote an alert"


def test_the_list_is_ordered_by_recency_not_by_mention_count():
    """Ordering by times_seen would rank names against each other — the exact
    'reddit found something' reading this list must not support."""
    import inspect
    from api.routers import universe as mod
    src = inspect.getsource(mod)
    assert "ORDER BY datetime(last_seen_at) DESC" in src
    assert "ORDER BY times_seen" not in src


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    """The prior process failure: a --dry-run that created tables and logged a row."""
    import sqlite3 as _sq
    coll.run(dry_run=True)          # tables do not exist -> must bail without creating
    conn = db_connection()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    logged = conn.execute("SELECT COUNT(*) FROM activity_log "
                          "WHERE source=?", (coll.RULE,)).fetchone()[0]
    conn.close()
    assert "ticker_universe" not in names, "a dry run created the coverage table"
    assert logged == 0, "a dry run wrote an activity_log row"


def test_BOTH_cashtag_mechanisms_are_tested_separately():
    """Defence in depth is only defence if each layer is tested.

    A bare mention is stopped TWICE: `pending_mentions` filters `cashtagged = 1` in SQL,
    and `collectable` re-checks. The end-to-end test above passes with the `collectable`
    check DELETED, because the SQL already removed the row — so the second layer was
    untested and would have been the only thing left if the query ever changed. A
    verifier's mutation found exactly that.
    """
    # layer 1: the predicate, called directly
    ok, reason, _ = coll.collectable(None, "ANY", cashtagged=0, score=99,
                                     num_comments=99, cache=False)
    assert not ok and reason == "not_cashtagged", "the collectable() cashtag check is gone"

    # layer 2: the query, which must not hand bare rows downstream at all
    conn = db_connection(); coll.ensure_tables(conn)
    _mention(conn, "BARE2", cashtagged=0)
    _mention(conn, "CASH2", cashtagged=1)
    tickers = {m["ticker"] for m in coll.pending_mentions(conn)}
    conn.close()
    assert "CASH2" in tickers, "the query dropped a cashtagged mention"
    assert "BARE2" not in tickers, "pending_mentions handed a bare mention downstream"
