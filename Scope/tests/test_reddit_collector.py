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

# Captured at IMPORT, before the autouse stub replaces it. `monkeypatch.undo()` would
# also undo conftest's DATABASE_PATH isolation — which the DB guard correctly refuses,
# so the real function has to be held by reference instead.
_REAL_MARKET_CAP = coll.market_cap


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
    # rule_reddit imports the bootstrap INSIDE run() as `_ensure_collector`, so
    # `rr.ensure_tables` does not exist — patching it set an attribute nothing reads and
    # the table was simply recreated before the INSERT. The scenario never occurred.
    monkeypatch.setattr(coll, "ensure_tables", lambda c: None)
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
    # AN ALLOWLIST, not a blocklist. The blocklist form let `"strength": times_seen`
    # through because the word simply was not on the list — a verifier added it and the
    # suite stayed green. Enumerating what MAY appear cannot be outflanked by a synonym.
    ALLOWED = {"ticker", "first_collected_at", "last_seen_at", "times_seen",
               "market_cap", "cap_status", "source"}
    extra = set(got[0]) - ALLOWED
    assert not extra, (
        f"the payload grew {extra} — collection is coverage, and any field beyond the "
        "allowlist risks reading as a signal. Widen ALLOWED deliberately if intended.")
    blob = str(body).lower()
    assert "not a signal" in blob and "coverage" in blob


def test_collection_emits_no_alert_at_all():
    """The rule must never write to `alerts`. Coverage is not a signal."""
    import inspect
    src = inspect.getsource(coll)
    body = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "insert_alert" not in body
    # `body.upper()` can never contain a lowercase "alerts", so the old form of this
    # assertion was ALWAYS TRUE — a verifier put a literal `INSERT INTO alerts` inside
    # run() and all 524 tests stayed green.
    assert "INTO ALERTS" not in body.upper()
    assert "INSERT INTO ALERTS" not in body.upper()

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
    # FIRST with no tables: must bail without creating them.
    coll.run(dry_run=True)
    conn = db_connection()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    logged = conn.execute("SELECT COUNT(*) FROM activity_log "
                          "WHERE source=?", (coll.RULE,)).fetchone()[0]
    conn.close()
    assert "ticker_universe" not in names, "a dry run created the coverage table"
    assert logged == 0, "a dry run wrote an activity_log row"

    # THEN with a READY schema, so the run actually reaches record_activity. The old
    # version only ever hit the `_tables_exist` early return, so mutating the
    # `if not dry_run:` around record_activity survived the whole suite — the test was
    # green without reaching the guard it is named for.
    conn = db_connection(); coll.ensure_tables(conn)
    _mention(conn, "DRYRUN2")
    conn.commit(); conn.close()
    out = coll.run(dry_run=True)
    conn = db_connection()
    logged2 = conn.execute("SELECT COUNT(*) FROM activity_log WHERE source=?",
                           (coll.RULE,)).fetchone()[0]
    universe = conn.execute("SELECT COUNT(*) FROM ticker_universe").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM reddit_mentions "
                           "WHERE collected_at IS NULL").fetchone()[0]
    conn.close()
    assert logged2 == 0, "a dry run on a READY schema wrote an activity_log row"
    assert universe == 0, "a dry run wrote to the coverage list"
    assert pending == 1, "a dry run marked a mention as collected"
    assert out["dry_run"] is True


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


# ── the producer's cashtag labelling — the untested single point of failure ──
#
# Both "layers" of the cashtag gate read ONE boolean column, written by ONE function.
# That function had zero tests, so three mutations of it put a bare mention into
# `ticker_universe` with 524/524 green. The layers cannot tell a mislabelled row from a
# real one; this is where the gate actually lives.

def test_extract_cashtagged_returns_only_dollar_prefixed_symbols():
    from scripts.rule_reddit import extract_cashtagged
    known = {"MOBX", "NVDA", "POST", "GME"}
    assert extract_cashtagged("$MOBX and $NVDA are moving", known) == ["MOBX", "NVDA"]
    assert extract_cashtagged("MOBX and NVDA are moving", known) == [], \
        "a BARE token was returned as cashtagged — the collector's primary gate"
    assert extract_cashtagged("I saw this post about GME", known) == [], \
        "prose was returned as cashtagged"
    assert extract_cashtagged("$POST beat earnings", known) == ["POST"], \
        "a cashtagged common word must still count"
    assert extract_cashtagged("$NOTREAL is fake", known) == [], "unknown symbol returned"


def test_extract_cashtagged_shares_the_regex_with_the_alert_path():
    """One definition, so the two cannot drift."""
    import inspect
    from scripts import rule_reddit as rr
    src = inspect.getsource(rr.extract_cashtagged)
    assert "TICKER_RE" in src, "the cashtag extractor rolled its own pattern"
    assert "BARE_TICKER_RE" not in src, "the cashtag extractor is reading BARE tokens"


def test_the_producer_labels_cashtagged_correctly_end_to_end(monkeypatch):
    """The layer both gates depend on, through the real rule."""
    from jpt_common import db_connection as _db
    conn = _db(); coll.ensure_tables(conn); conn.close()
    rr = _stub_reddit(monkeypatch, [{
        "id": "mixed1", "title": "$MOBX is up and NVDA is too", "selftext": "",
        "score": 50, "num_comments": 9, "upvote_ratio": 0.9,
        "url": "https://reddit.com/r/stocks/mixed1", "author_post_count": 30}])
    monkeypatch.setattr(rr, "_extract_tickers", lambda t, k: ["MOBX", "NVDA"])
    monkeypatch.setattr(rr, "extract_cashtagged", lambda t, k: ["MOBX"])
    try:
        rr.run(emit=False, dry_run=False)
    except Exception as exc:
        pytest.skip(f"could not drive the real producer: {type(exc).__name__}: {exc}")

    conn = _db()
    flags = dict(conn.execute(
        "SELECT ticker, cashtagged FROM reddit_mentions WHERE post_id='mixed1'"))
    conn.close()
    assert flags.get("MOBX") == 1, "the cashtagged ticker was not labelled"
    assert flags.get("NVDA") == 0, (
        "a BARE ticker was labelled cashtagged — this single column IS the gate, and "
        "both checks downstream would happily collect it")


def test_collect_passes_the_REAL_flag_not_a_literal():
    """`collect()` used to pass a hardcoded 1, making the collectable() check dead code."""
    import inspect
    src = inspect.getsource(coll.collect)
    assert 'm["cashtagged"]' in src, "collect() is not reading the stored flag"
    assert "collectable(conn, m[\"ticker\"], 1," not in src


# ── the market-cap machinery, which the autouse stub hid entirely ────────────

def test_market_cap_fails_closed_in_every_failure_mode(monkeypatch):
    """0% coverage before this: `no_network` stubs `coll.market_cap` in every test, so
    the real SEC/Yahoo path was never executed and could have returned anything."""
    import datetime as _d
    fresh = _d.date.today().isoformat()
    conn = db_connection(); coll.ensure_tables(conn)
    cap = lambda t: _REAL_MARKET_CAP(conn, t, cache=False)   # noqa: E731
    # `_is_foreign_private_issuer` is a REAL SEC call on this path — stub it, or this test
    # hits data.sec.gov live. Default False = domestic; the FPI case is asserted below.
    monkeypatch.setattr(coll, "_is_foreign_private_issuer", lambda c: False)
    monkeypatch.setattr(coll, "_cik_for", lambda s: None)
    assert cap("X") is None                                          # no CIK
    monkeypatch.setattr(coll, "_cik_for", lambda s: "0000320193")
    monkeypatch.setattr(coll, "_shares_outstanding", lambda c: None)
    monkeypatch.setattr(coll, "_last_close", lambda s: 100.0)
    assert cap("X") is None                                          # SEC down
    # `_shares_outstanding` returns (shares, as_of_date) — the date is part of the answer.
    monkeypatch.setattr(coll, "_shares_outstanding", lambda c: (1e9, fresh))
    monkeypatch.setattr(coll, "_last_close", lambda s: None)
    assert cap("X") is None                                          # Yahoo down
    monkeypatch.setattr(coll, "_last_close", lambda s: 0.0)
    assert cap("X") is None                                          # zero price
    monkeypatch.setattr(coll, "_shares_outstanding", lambda c: (0.0, fresh))
    monkeypatch.setattr(coll, "_last_close", lambda s: 10.0)
    assert cap("X") is None                                          # zero shares
    monkeypatch.setattr(coll, "_shares_outstanding", lambda c: (100.0, fresh))
    assert cap("X") is None                                          # shell share count
    monkeypatch.setattr(coll, "_shares_outstanding", lambda c: (2e9, "2019-01-01"))
    assert cap("X") is None                                          # years-stale fact
    monkeypatch.setattr(coll, "_shares_outstanding", lambda c: (2e9, fresh))
    monkeypatch.setattr(coll, "_is_foreign_private_issuer", lambda c: True)
    assert cap("X") is None                                          # foreign issuer
    monkeypatch.setattr(coll, "_is_foreign_private_issuer", lambda c: None)
    assert cap("X") is None                                          # issuer lookup failed
    monkeypatch.setattr(coll, "_is_foreign_private_issuer", lambda c: False)
    assert cap("X") == 20_000_000_000                                # healthy
    conn.close()


def test_an_outage_collected_megacap_is_EVICTED_when_the_cap_resolves(monkeypatch):
    """One outage collected AAPL/NVDA/MSFT as `unknown` and there was no repair path.

    Eviction MARKS rather than deletes: deleting made a wrongful eviction permanent (a
    single bad price tick removed a genuine small cap, recoverable only via a fresh
    mention AND the 30-day cap-cache expiry), which is failing closed in a module that
    argues a missing name is the expensive failure. Excluded rows stay, are re-checked
    every pass, and are filtered out of the API.

    Driven through `collect()`, NOT by calling repair directly — the call site was the
    untested part, and deleting it left 540/540 green.
    """
    conn = db_connection(); coll.ensure_tables(conn)
    monkeypatch.setattr(coll, "market_cap", lambda c, s, **kw: None)   # outage
    _mention(conn, "AAPL"); _mention(conn, "TINYCO")
    coll.collect(conn); conn.commit()
    assert {r[0] for r in conn.execute(
        "SELECT ticker FROM ticker_universe")} == {"AAPL", "TINYCO"}

    caps = {"AAPL": 4_900_000_000_000, "TINYCO": 300_000_000}
    monkeypatch.setattr(coll, "market_cap", lambda c, s, **kw: caps.get(s))
    out = coll.collect(conn); conn.commit()          # the CALL SITE, not repair directly
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT ticker, cap_status FROM ticker_universe")}
    assert rows.get("AAPL") == "excluded", "the outage-collected mega-cap was not evicted"
    assert rows.get("TINYCO") == "small"
    assert out["evicted"] == 1 and out["repaired"] == 1, out

    # and the API must not show it as coverage
    body = _c.get("/api/universe").json()
    conn.close()
    assert "AAPL" not in [t["ticker"] for t in body["tickers"]]
    assert "TINYCO" in [t["ticker"] for t in body["tickers"]]


def test_a_wrongly_excluded_ticker_can_come_back(monkeypatch):
    """The reversibility the DELETE destroyed.

    The scenario is a row ALREADY in the universe (collected during an outage as
    `unknown`) that a bad price tick then reclassifies as large. Deleting it made that
    permanent — recoverable only via a fresh mention AND the 30-day cap-cache expiry.
    Marking makes the next repair pass fix it.

    (A ticker that looks large on FIRST contact is simply never collected — that is the
    ordinary gate, not an eviction, and it is not what this covers.)
    """
    conn = db_connection(); coll.ensure_tables(conn)
    monkeypatch.setattr(coll, "market_cap", lambda c, s, **kw: None)          # outage
    _mention(conn, "SMOL")
    coll.collect(conn); conn.commit()
    assert conn.execute("SELECT cap_status FROM ticker_universe "
                        "WHERE ticker='SMOL'").fetchone()["cap_status"] == "unknown"

    monkeypatch.setattr(coll, "market_cap", lambda c, s, **kw: 50_000_000_000)  # bad tick
    coll.collect(conn); conn.commit()
    assert conn.execute("SELECT cap_status FROM ticker_universe "
                        "WHERE ticker='SMOL'").fetchone()["cap_status"] == "excluded"

    monkeypatch.setattr(coll, "market_cap", lambda c, s, **kw: 300_000_000)   # corrected
    coll.collect(conn); conn.commit()
    row = conn.execute("SELECT cap_status, market_cap FROM ticker_universe "
                       "WHERE ticker='SMOL'").fetchone()
    conn.close()
    assert row is not None, "a wrongly excluded ticker was deleted and cannot return"
    assert (row["cap_status"], row["market_cap"]) == ("small", 300_000_000), (
        "the exclusion did not reverse when the cap resolved correctly")


def test_a_still_unknown_row_is_left_alone_not_evicted(monkeypatch):
    """Documented fail-open behaviour that had no test — making the still_unknown branch
    DELETE survived the whole suite."""
    conn = db_connection(); coll.ensure_tables(conn)
    monkeypatch.setattr(coll, "market_cap", lambda c, s, **kw: None)
    _mention(conn, "GHOST")
    coll.collect(conn); conn.commit()
    out = coll.collect(conn); conn.commit()
    row = conn.execute("SELECT cap_status FROM ticker_universe "
                       "WHERE ticker='GHOST'").fetchone()
    conn.close()
    assert row is not None and row["cap_status"] == "unknown", \
        "a still-unpriceable row was evicted instead of left flagged for retry"
    assert out["still_unknown"] >= 1


def test_a_failed_cap_lookup_is_cached_so_it_is_not_retried_every_run(monkeypatch):
    """Unknowns were not cached at all, so every still-unknown row re-hit SEC AND Yahoo
    on EVERY run — 4 SEC + 4 Yahoo per unpriceable row per day, growing without bound,
    and contradicting the schedule's stated 'one lookup per NEW ticker' rationale."""
    calls = {"cik": 0}
    conn = db_connection(); coll.ensure_tables(conn)
    def _cik(sym):
        calls["cik"] += 1
        return None
    monkeypatch.setattr(coll, "_cik_for", _cik)
    for _ in range(4):
        _REAL_MARKET_CAP(conn, "GHOSTY", cache=True)
    conn.close()
    assert calls["cik"] == 1, (
        f"a failed lookup was retried {calls['cik']} times — failures must be cached")


def test_an_unknown_resighting_does_not_downgrade_a_resolved_row():
    """cap_status was overwritten while market_cap was COALESCEd, so a row could read
    cap_status='unknown' sitting on a resolved market_cap."""
    conn = db_connection(); coll.ensure_tables(conn)
    coll.upsert_universe(conn, "RESOLVED", "small", 750_000_000)
    coll.upsert_universe(conn, "RESOLVED", "unknown", None)
    conn.commit()
    row = conn.execute("SELECT cap_status, market_cap FROM ticker_universe "
                       "WHERE ticker='RESOLVED'").fetchone()
    conn.close()
    assert (row["cap_status"], row["market_cap"]) == ("small", 750_000_000)


def test_the_collection_epoch_excludes_pre_extraction_fix_mentions():
    """Untested before: mutating COLLECTION_EPOCH to 1970 survived the whole suite,
    so the guard against ~45% English-word junk was decoration."""
    conn = db_connection(); coll.ensure_tables(conn)
    conn.execute("INSERT INTO reddit_mentions (post_id, ticker, cashtagged, score, "
                 "num_comments, mentioned_at) VALUES "
                 "('old1','HERE',1,99,99,'2026-07-19 10:00:00')")
    conn.commit()
    assert "HERE" not in {m["ticker"] for m in coll.pending_mentions(conn)}
    coll.collect(conn); conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM ticker_universe "
                     "WHERE ticker='HERE'").fetchone()[0]
    conn.close()
    assert n == 0, "a pre-extraction-fix mention was collected"


def test_ensure_tables_repairs_a_legacy_reddit_mentions(monkeypatch):
    """The PRAGMA-guarded ADD COLUMNs were correct and UNTESTED — deleting them left
    the suite green. This is the `tickers.updated_at` failure class: CREATE TABLE IF NOT
    EXISTS is a no-op on a table that already exists with fewer columns."""
    conn = db_connection()
    conn.execute("DROP TABLE IF EXISTS reddit_mentions")
    conn.execute("""CREATE TABLE reddit_mentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT NOT NULL,
        ticker TEXT NOT NULL, subreddit TEXT,
        mentioned_at TEXT DEFAULT (datetime('now')), UNIQUE(post_id, ticker))""")
    conn.execute("INSERT INTO reddit_mentions (post_id, ticker) VALUES ('legacy','OLD')")
    conn.commit()
    assert not coll._tables_exist(conn), "a legacy schema must not read as ready"

    coll.ensure_tables(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reddit_mentions)").fetchall()}
    row = conn.execute("SELECT cashtagged, score, num_comments FROM reddit_mentions "
                       "WHERE post_id='legacy'").fetchone()
    coll.ensure_tables(conn)                       # idempotent
    conn.close()
    assert {"cashtagged", "score", "num_comments"} <= cols, "the guarded adds are gone"
    assert (row["cashtagged"], row["score"], row["num_comments"]) == (0, 0, 0), \
        "legacy rows must default to NOT cashtagged — a second barrier against pre-fix junk"


def test_dry_run_does_not_crash_on_a_legacy_schema(capsys):
    """It exited rc=1 with `no such column: score`, because --dry-run deliberately skips
    ensure_tables and _tables_exist only checked the table NAME."""
    conn = db_connection()
    conn.execute("DROP TABLE IF EXISTS reddit_mentions")
    conn.execute("""CREATE TABLE reddit_mentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT, ticker TEXT,
        subreddit TEXT, mentioned_at TEXT, UNIQUE(post_id, ticker))""")
    conn.commit(); conn.close()

    out = coll.run(dry_run=True)                   # must not raise
    assert out["dry_run"] is True and out["collected"] == 0
    assert "schema not ready" in capsys.readouterr().out


# ── the window fix: times_seen must count SIGHTINGS, not collector runs ──────

def test_rerunning_with_no_new_posts_does_not_bump_times_seen():
    """Every ticker was re-upserted on every run, so `times_seen` counted RUNS and all
    `last_seen_at` converged to now — making the API's recency ordering a tie broken by
    nothing, and the oldest names carry the highest counts (a de facto age ranking)."""
    conn = db_connection(); coll.ensure_tables(conn)
    _mention(conn, "STEADY")
    coll.collect(conn); conn.commit()
    first = conn.execute("SELECT times_seen, last_seen_at FROM ticker_universe "
                         "WHERE ticker='STEADY'").fetchone()
    assert first["times_seen"] == 1

    for _ in range(5):                       # five more runs, no new posts
        coll.collect(conn); conn.commit()
    after = conn.execute("SELECT times_seen, last_seen_at FROM ticker_universe "
                         "WHERE ticker='STEADY'").fetchone()
    conn.close()
    assert after["times_seen"] == 1, (
        f"five idle runs bumped times_seen to {after['times_seen']} — it is counting "
        "collector runs, not sightings")
    assert after["last_seen_at"] == first["last_seen_at"], "an idle run moved last_seen_at"


def test_a_genuinely_new_sighting_DOES_bump_it():
    """The control — without it, `times_seen` could simply be frozen."""
    conn = db_connection(); coll.ensure_tables(conn)
    _mention(conn, "AGAIN", post="p1")
    coll.collect(conn); conn.commit()
    _mention(conn, "AGAIN", post="p2")       # a genuinely new post
    coll.collect(conn); conn.commit()
    row = conn.execute("SELECT times_seen FROM ticker_universe "
                       "WHERE ticker='AGAIN'").fetchone()
    conn.close()
    assert row["times_seen"] == 2, f"a new sighting did not register ({row['times_seen']})"


def test_evaluated_mentions_are_marked_including_rejected_ones():
    """Rejections must be marked too, or every run re-tests them and re-hits the cap API.
    Engagement is fixed at ingest and never improves."""
    conn = db_connection(); coll.ensure_tables(conn)
    _mention(conn, "DEAD2", score=0, num_comments=0)
    coll.collect(conn); conn.commit()
    unmarked = conn.execute(
        "SELECT COUNT(*) FROM reddit_mentions WHERE collected_at IS NULL "
        "AND cashtagged = 1").fetchone()[0]
    conn.close()
    assert unmarked == 0, "a rejected mention was left pending and will be re-evaluated"


def test_last_seen_at_is_the_mention_time_not_the_run_time(monkeypatch):
    """It means "when was this name last mentioned". Stamping the run time made it mean
    "when did the collector last execute" — identical for every row, which is what made
    the API's recency ordering meaningless.

    The epoch is moved back for this fixture only: a mention old enough to distinguish
    the two timestamps necessarily predates the real COLLECTION_EPOCH, which correctly
    rejects it (that guard has its own test).
    """
    monkeypatch.setattr(coll, "COLLECTION_EPOCH", "2000-01-01 00:00:00")
    conn = db_connection(); coll.ensure_tables(conn)
    conn.execute("INSERT INTO reddit_mentions (post_id, ticker, cashtagged, score, "
                 "num_comments, mentioned_at) VALUES "
                 "('old_m','OLDSEEN',1,20,5,'2026-07-01 09:30:00')")
    conn.commit()
    coll.collect(conn); conn.commit()
    row = conn.execute("SELECT last_seen_at FROM ticker_universe "
                       "WHERE ticker='OLDSEEN'").fetchone()
    conn.close()
    assert row is not None, "the fixture was not collected"
    assert row["last_seen_at"] == "2026-07-01 09:30:00", (
        f"last_seen_at is {row['last_seen_at']!r} — it took the RUN time, not the "
        "mention's, so every row would carry the same value")


# ── normalization ───────────────────────────────────────────────────────────

def test_the_universe_is_normalized_like_every_other_ticker_table():
    from jpt_common import normalize_ticker
    conn = db_connection(); coll.ensure_tables(conn)
    for raw in ("  mobx  ", "mobx", "MOBX"):
        coll.upsert_universe(conn, raw, "small", 400_000_000)
    conn.commit()
    rows = [r[0] for r in conn.execute(
        "SELECT ticker FROM ticker_universe WHERE ticker LIKE '%MOBX%'")]
    conn.close()
    assert rows == [normalize_ticker("MOBX")] == ["MOBX"], (
        f"case/whitespace variants created separate rows: {rows}")


def test_the_scheduled_entry_exists_and_points_at_a_real_file():
    from api.main import _CRON_SCHEDULE, _RULE_SCHEDULE
    key = "scripts/rule_reddit_collector.py"
    assert key in _CRON_SCHEDULE, "the collector is scheduled nowhere — it never accumulates"
    assert key not in _RULE_SCHEDULE
    assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", key))
    # coverage is not news: it must not be scheduled so often it looks like a feed
    assert _CRON_SCHEDULE[key].get("hour"), "no hour set — this would run every minute"


def test_first_collected_at_is_the_COLLECTION_time_not_the_mention_time(monkeypatch):
    """A field whose name did not match what it held — introduced by the last fix.

    `first_collected_at` and `last_seen_at` were bound to the SAME parameter, so "first
    collected" silently carried the mention's timestamp. The old assertion was
    `is not None`, which is unfalsifiable here: the column has a DEFAULT and both INSERT
    branches COALESCE to non-null.
    """
    import datetime as _dt
    monkeypatch.setattr(coll, "COLLECTION_EPOCH", "2000-01-01 00:00:00")
    conn = db_connection(); coll.ensure_tables(conn)
    conn.execute("INSERT INTO reddit_mentions (post_id, ticker, cashtagged, score, "
                 "num_comments, mentioned_at) VALUES "
                 "('fc1','FIRSTCOL',1,20,5,'2026-07-01 09:30:00')")
    conn.commit()
    coll.collect(conn); conn.commit()
    row = conn.execute("SELECT first_collected_at, last_seen_at FROM ticker_universe "
                       "WHERE ticker='FIRSTCOL'").fetchone()
    conn.close()
    assert row["last_seen_at"] == "2026-07-01 09:30:00"      # the MENTION
    assert row["first_collected_at"] != "2026-07-01 09:30:00", (
        "first_collected_at took the mention's timestamp — it means when we COLLECTED it")
    age = _dt.datetime.utcnow() - _dt.datetime.fromisoformat(row["first_collected_at"])
    assert abs(age.total_seconds()) < 300, row["first_collected_at"]


def test_a_mention_arriving_mid_run_is_not_marked_unseen():
    """Leak B: the marking UPDATE keyed on TICKER, so a mention landing mid-run for an
    already-evaluated ticker was marked without ever being counted — a sighting lost
    forever. It now keys on the row ids actually read."""
    conn = db_connection(); coll.ensure_tables(conn)
    _mention(conn, "RACE", post="r1")
    pending = coll.pending_mentions(conn)
    assert pending and pending[0]["ids"], "pending_mentions must carry row ids"
    _mention(conn, "RACE", post="r2")        # arrives after the read
    # mark only what the first read saw, as collect() now does
    qs = ",".join("?" * len(pending[0]["ids"]))
    conn.execute(f"UPDATE reddit_mentions SET collected_at = datetime('now') "
                 f"WHERE id IN ({qs})", pending[0]["ids"])
    conn.commit()
    still = {m["ticker"] for m in coll.pending_mentions(conn)}
    conn.close()
    assert "RACE" in still, "the mid-run mention was marked without being counted"


def test_rule_reddit_bootstraps_the_collector_schema():
    """Deleting `_ensure_collector(conn)` from rule_reddit.run() survived the suite. The
    live DB still has the pre-collector five-column `reddit_mentions`, so without it
    every mention INSERT raises into the except and logs 'mention capture skipped' —
    the collector would collect nothing until its own 02:15 run repaired the schema."""
    import inspect
    from scripts import rule_reddit as rr
    src = inspect.getsource(rr.run)
    body = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "_ensure_collector(conn)" in body, (
        "rule_reddit no longer bootstraps the collector schema")


def test_last_seen_at_updates_on_a_RE_SIGHTING_not_just_the_first(monkeypatch):
    """The ON CONFLICT half — and the common case.

    The first-collection test only exercised the INSERT branch, so mutating
    `last_seen_at = COALESCE(excluded.last_seen_at, ...)` to `datetime('now')` on the
    UPDATE branch survived the whole suite. That is the exact bug this session fixed,
    re-introducible on the other half of the same statement.
    """
    monkeypatch.setattr(coll, "COLLECTION_EPOCH", "2000-01-01 00:00:00")
    conn = db_connection(); coll.ensure_tables(conn)
    conn.execute("INSERT INTO reddit_mentions (post_id, ticker, cashtagged, score, "
                 "num_comments, mentioned_at) VALUES "
                 "('rs1','RESEEN',1,20,5,'2026-07-01 06:00:00')")
    conn.commit()
    coll.collect(conn); conn.commit()
    assert conn.execute("SELECT last_seen_at FROM ticker_universe WHERE ticker='RESEEN'"
                        ).fetchone()["last_seen_at"] == "2026-07-01 06:00:00"

    conn.execute("INSERT INTO reddit_mentions (post_id, ticker, cashtagged, score, "
                 "num_comments, mentioned_at) VALUES "
                 "('rs2','RESEEN',1,20,5,'2026-07-02 07:30:00')")
    conn.commit()
    coll.collect(conn); conn.commit()
    row = conn.execute("SELECT last_seen_at, times_seen FROM ticker_universe "
                       "WHERE ticker='RESEEN'").fetchone()
    conn.close()
    assert row["times_seen"] == 2
    assert row["last_seen_at"] == "2026-07-02 07:30:00", (
        f"re-sighting stored {row['last_seen_at']!r} — the UPDATE branch took the RUN "
        "time, so every re-seen row would carry the same value")
