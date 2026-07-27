"""Discovery: unusual traction for THIS ticker, and never a gate leg.

Two properties matter more than the algorithm:

  1. UNUSUAL, NOT POPULAR. A mega-cap must not fire. A ticker sitting at its own normal
     high volume must not fire. Ranking by raw volume just re-finds GME every day.
  2. NEVER A GATE LEG. Discovery adds no RULE_10 instrument. This is the one that would
     actually damage Scope if it slipped — a social-buzz leg would hollow out the
     convergence moat, whose entire value is that its legs are independent sources.

⚠️ THE PARAMETERS ARE NOT TUNED, and these tests do not pretend otherwise. There are
12.3 hours of real mention history, 49% of it the English-word false positives the
extraction fix removed, so K and the floors are declared guesses. What IS tested here is
the MACHINERY: the shape of the decision, the guards, the dedup, the read-only API and
the never-a-leg discipline — all of which are verifiable today. Retune against real
history before trusting a firing.
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
from scripts import rule_reddit_discovery as disc  # noqa: E402

_c = TestClient(app)
_REAL_DB = os.path.join(os.path.dirname(__file__), "..", "data", "jpt.db")


@pytest.fixture
def past_epoch(monkeypatch):
    """Let fixtures seed HISTORY.

    DISCOVERY_EPOCH is deliberately set to the day the extraction fix landed, so the
    ~49%-contaminated pre-fix mentions cannot form baselines. A consequence: nothing
    dated before today is countable, which is correct in production and makes it
    impossible to seed a 14-day baseline in a test. The quarantine is production
    policy, not part of the algorithm under test, so tests that need history move it
    back. Tests that verify the quarantine itself deliberately do NOT use this.
    """
    monkeypatch.setattr(disc, "DISCOVERY_EPOCH", "2000-01-01 00:00:00")


def _seed(conn, ticker, day_offsets, subreddit="wallstreetbets"):
    """day_offsets: 0 = today, 1 = yesterday. One row per mention."""
    for i, off in enumerate(day_offsets):
        conn.execute(
            "INSERT OR IGNORE INTO reddit_mentions (post_id, ticker, subreddit, "
            "mentioned_at) VALUES (?,?,?, datetime('now', ?))",
            (f"{ticker}_{off}_{i}", ticker, subreddit, f"-{off} days"))
    conn.commit()


def _history(conn, days=10):
    """Give the rule enough distinct days that MIN_HISTORY_DAYS is satisfied."""
    for d in range(1, days + 1):
        conn.execute(
            "INSERT OR IGNORE INTO reddit_mentions (post_id, ticker, subreddit, "
            "mentioned_at) VALUES (?,?,?, datetime('now', ?))",
            (f"hist_{d}", "HISTFILL", "stocks", f"-{d} days"))
    conn.commit()


# ── the decision, tested pure (no DB, no network) ────────────────────────────

def test_a_small_cap_going_two_to_forty_fires():
    fires, trigger, dev = disc.evaluate(40, [2, 2, 3, 1, 2, 2, 2, 0, 2, 3, 2, 1, 2, 2])
    assert fires and trigger == "deviation" and dev >= 10


def test_a_ticker_at_its_own_normal_volume_does_NOT_fire():
    """POPULAR IS NOT UNUSUAL — the whole point of a per-ticker baseline.

    40 mentions is a lot in absolute terms. For a ticker that always gets 40, it is
    nothing. A volume ranking would surface this every single day.
    """
    fires, trigger, dev = disc.evaluate(40, [38, 42, 40, 39, 41, 40, 37, 43, 40, 40])
    assert not fires and trigger == "within_normal", f"{trigger} dev={dev}"


def test_one_to_three_does_NOT_fire_the_absolute_floor():
    """3x the baseline, but 3 mentions is noise. The floor outranks the deviation."""
    fires, trigger, _ = disc.evaluate(3, [1, 1, 0, 1, 1, 1, 0, 1])
    assert not fires and trigger == "below_floor"


def test_a_brand_new_ticker_needs_a_HIGH_absolute_count():
    """Cold start: nothing to deviate from, so the bar is absolute and stricter."""
    below, trigger, _ = disc.evaluate(6, [0] * 14)
    assert not below and trigger == "cold_start_below_bar"
    fires, trigger, _ = disc.evaluate(disc.COLD_START_MENTIONS, [0] * 14)
    assert fires and trigger == "cold_start"


def test_the_baseline_is_a_MEDIAN_so_a_prior_spike_cannot_blind_it():
    """A mean would be poisoned by the earlier spike and suppress the next detection."""
    import statistics
    hist = [2, 2, 2, 40, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]     # one prior spike
    assert statistics.median(hist) == 2
    assert statistics.mean(hist) > 4                        # a mean would be ~4.7
    fires, trigger, _ = disc.evaluate(12, hist)
    assert fires and trigger == "deviation", "a prior spike blinded the detector"


def test_zero_filling_means_a_quiet_ticker_has_a_low_baseline(past_epoch):
    """Without zero-fill the baseline would be 'typical ACTIVE day', a different
    question that makes quiet tickers look busy and suppresses their spikes."""
    conn = db_connection()
    disc.ensure_tables(conn)
    _seed(conn, "QUIET", [3, 9])          # active on 2 of the last 14 days
    counts = disc.daily_counts(conn, "QUIET")
    conn.close()
    assert len(counts) == disc.BASELINE_DAYS
    assert counts.count(0) == disc.BASELINE_DAYS - 2


# ── guards ───────────────────────────────────────────────────────────────────

def test_insufficient_history_emits_NOTHING():
    """The structural guard. With 12 hours of real data this is the live state."""
    conn = db_connection()
    disc.ensure_tables(conn)
    _seed(conn, "NEWBUZZ", [0] * 30)      # 30 mentions today, 1 day of history
    assert disc.history_days(conn) < disc.MIN_HISTORY_DAYS
    assert disc.find_candidates(conn) == []
    conn.close()


def test_an_unknown_market_cap_is_not_treated_as_small(monkeypatch, past_epoch):
    """Fail CLOSED. A missed candidate costs one review; a mega-cap in the pool is
    noise in the only surface a human is asked to read."""
    conn = db_connection()
    disc.ensure_tables(conn)
    _history(conn)
    _seed(conn, "UNKNOWNCAP", [0] * 30)
    monkeypatch.setattr(disc, "market_cap", lambda c, s: None)
    assert not [c for c in disc.find_candidates(conn) if c["ticker"] == "UNKNOWNCAP"]
    conn.close()


def test_a_mega_cap_does_NOT_reach_the_pool(monkeypatch, past_epoch):
    conn = db_connection()
    disc.ensure_tables(conn)
    _history(conn)
    _seed(conn, "MEGA", [0] * 30)
    monkeypatch.setattr(disc, "market_cap", lambda c, s: 3_000_000_000_000)
    assert not [c for c in disc.find_candidates(conn) if c["ticker"] == "MEGA"]
    # control: the SAME traction with a small cap DOES reach it, so the exclusion is
    # the cap filter and not the fixture failing to trigger
    monkeypatch.setattr(disc, "market_cap", lambda c, s: 500_000_000)
    assert [c for c in disc.find_candidates(conn) if c["ticker"] == "MEGA"]
    conn.close()


def test_pre_epoch_mentions_are_quarantined():
    """The stored history is ~49% English-word false positives from the old extractor.
    Counting them would build baselines for the word 'HERE'."""
    conn = db_connection()
    disc.ensure_tables(conn)
    conn.execute("INSERT INTO reddit_mentions (post_id, ticker, mentioned_at) "
                 "VALUES ('old1','HERE','2026-07-19 10:00:00')")
    conn.commit()
    assert disc.today_count(conn, "HERE") == 0
    assert sum(disc.daily_counts(conn, "HERE")) == 0
    conn.close()


# ── the watch pool ───────────────────────────────────────────────────────────

def test_rediscovery_updates_and_never_duplicates():
    conn = db_connection()
    disc.ensure_tables(conn)
    c = {"ticker": "DUPE", "mention_count": 20, "baseline": 2.0, "deviation": 10.0,
         "market_cap": 500_000_000, "trigger": "deviation", "subreddits": "stocks",
         "sample_days": 10}
    disc.upsert_watch(conn, c)
    c2 = dict(c, mention_count=35, deviation=17.5)
    disc.upsert_watch(conn, c2)
    conn.commit()
    rows = conn.execute(
        "SELECT ticker, times_discovered, mention_count, first_discovered, "
        "last_discovered FROM discovery_watch WHERE ticker='DUPE'").fetchall()
    conn.close()
    assert len(rows) == 1, "re-discovery duplicated instead of updating"
    assert rows[0]["times_discovered"] == 2
    assert rows[0]["mention_count"] == 35, "the pool did not refresh its traction"
    assert rows[0]["first_discovered"] is not None


# ── the API ──────────────────────────────────────────────────────────────────

def test_the_endpoint_returns_the_pool_and_no_confidence_score():
    conn = db_connection()
    disc.ensure_tables(conn)
    disc.upsert_watch(conn, {
        "ticker": "REVIEWME", "mention_count": 22, "baseline": 2.0, "deviation": 11.0,
        "market_cap": 400_000_000, "trigger": "deviation", "subreddits": "stocks",
        "sample_days": 12})
    conn.commit(); conn.close()

    body = _c.get("/api/discovery").json()
    assert body["kind"] == "discovery_watch_pool"
    got = [c for c in body["candidates"] if c["ticker"] == "REVIEWME"]
    assert got, "the pool did not surface a discovered ticker"
    cand = got[0]

    # THE FRAMING TEST. No ground truth exists, so no number may imply one.
    blob = str(body).lower()
    for banned in ("confidence", "score", "probability", "prediction", "target",
                   "buy", "signal_strength", "conviction"):
        assert banned not in str(cand).lower(), (
            f"the payload carries a {banned!r} field — discovery has NO ground truth "
            "and must not imply one")
    assert "not a signal" in blob and "review" in blob
    assert cand["traction"]["deviation_multiple"] == 11.0     # a measurement, not a score


def test_the_endpoint_is_read_only():
    """No write verb reachable in the router."""
    import inspect
    from api.routers import discovery as mod
    body = "\n".join(l.split("#")[0] for l in inspect.getsource(mod).splitlines())
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ",
                 "commit("):
        assert verb.upper() not in body.upper(), f"{verb.strip()} in a read-only router"


# ── THE DISCIPLINE CRUX: never a gate leg ────────────────────────────────────

def test_discovery_adds_NO_gate_instrument():
    """The one that would actually damage Scope.

    RULE_10's instrument count is the moat. A social-buzz leg would let reddit chatter
    manufacture a corroboration, which is precisely what the gate exists to prevent.
    """
    from jpt_common import RULE_10_EXCLUDED, RULE_10_INSTRUMENTS, rule10_instruments

    assert "RULE_REDDIT" in RULE_10_EXCLUDED, "RULE_REDDIT left the exclusion set"
    assert disc.RULE not in RULE_10_INSTRUMENTS, "discovery was given an instrument"
    assert rule10_instruments([disc.RULE]) == [] or disc.RULE in RULE_10_EXCLUDED

    # the phantom-instrument trap: an eligible-but-unmapped rule becomes its OWN
    # instrument, so being absent from the map is NOT enough — it must be excluded.
    assert rule10_instruments(["RULE_REDDIT"]) == [], \
        "RULE_REDDIT resolves to an instrument — it would open corroborations"


def test_a_discovered_ticker_does_not_contribute_to_a_convergence():
    """Behavioural: put a ticker in the pool, run the real gate, prove it changes nothing."""
    from scripts import rule_10_corroboration as r10

    conn = db_connection()
    disc.ensure_tables(conn)
    disc.upsert_watch(conn, {
        "ticker": "POOLONLY", "mention_count": 50, "baseline": 1.0, "deviation": 50.0,
        "market_cap": 100_000_000, "trigger": "deviation", "subreddits": "stocks",
        "sample_days": 14})
    # plus a RULE_REDDIT alert, the strongest form of the same question
    conn.execute("INSERT INTO alerts (rule, ticker, severity, headline, created_at) "
                 "VALUES ('RULE_REDDIT','POOLONLY','HIGH','buzz', datetime('now'))")
    conn.commit()

    fires = r10.find_corroborated_tickers(conn, 14 * 24)
    instruments = r10.instruments_for([
        {"rule": r[0]} for r in conn.execute(
            "SELECT rule FROM alerts WHERE ticker='POOLONLY'").fetchall()])
    conn.close()

    assert "POOLONLY" not in fires, "a discovery-only ticker reached the gate"
    assert instruments == [], f"discovery contributed instruments: {instruments}"


def test_no_corroboration_path_reads_the_watch_pool():
    """Structural: grep the gate's own sources for the pool table."""
    import inspect
    from scripts import rule_10_corroboration as r10
    import jpt_common
    for mod in (r10, jpt_common):
        assert "discovery_watch" not in inspect.getsource(mod), (
            f"{mod.__name__} reads the discovery pool — it must not")
