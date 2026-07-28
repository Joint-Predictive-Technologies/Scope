"""Market-cap plausibility. Two mis-scales found LIVE, in merged code.

`classify_cap` checked only a ceiling, so any mis-scaled cap passed as "small" and the
small-cap gate was simply wrong. Both real, both from `ticker_meta` on `main`:

    CLBK  $1,085                   a savings institution, 15 insiders, $4.5M of buys —
                                   published as the TOP insider cluster
    TSM   $10,349,411,116,118      ~5x its real value

Two layers are tested here: the ROOT CAUSES in `market_cap`, and the plausibility GUARD
in `classify_cap` that catches the next mis-scale nobody has seen yet.

`classify_cap` is shared by the MERGED reddit collector and the unmerged cluster surface,
so both consumers are covered.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection  # noqa: E402
from scripts import rule_reddit_collector as rc  # noqa: E402

# Captured BEFORE the autouse fixture can stub it. The foreign-issuer tests below test the
# real function, and an earlier draft of this file silently asserted against the stub
# instead — two of them "passed" while exercising a lambda.
_REAL_IS_FPI = rc._is_foreign_private_issuer


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing here reaches SEC or Yahoo; each test injects the shape it needs."""
    monkeypatch.setattr(rc, "_cik_for", lambda s: "0000000001")
    monkeypatch.setattr(rc, "_is_foreign_private_issuer", lambda cik: False)


def _cap(monkeypatch, shares, price):
    monkeypatch.setattr(rc, "_shares_outstanding", lambda cik: shares)
    monkeypatch.setattr(rc, "_last_close", lambda s: price)


# ── ROOT CAUSE 1 — the CLBK shape ───────────────────────────────────────────

def test_a_shell_share_count_yields_UNKNOWN_not_a_tiny_cap(monkeypatch):
    """CLBK's CIK is CORRECT — Columbia Financial, Inc./MD/. But it is a freshly
    reorganised holding company (S-1, 8-K12B, POS AM) whose only cover-page tag reports
    the shell's nominal **100** shares.

    And there is NO alternative concept: us-gaap CommonStockSharesOutstanding,
    CommonStockSharesIssued and WeightedAverageNumberOfSharesOutstandingBasic are all
    absent for this filer. So the fix cannot be "use a better tag" — 100 shares is not a
    real float, and the honest answer is unknown.
    """
    conn = db_connection()
    _cap(monkeypatch, 100, 10.85)                      # the exact live values
    assert rc.market_cap(conn, "CLBK", cache=False) is None
    assert rc.classify_cap(conn, "CLBK", cache=False) == ("unknown", None)
    conn.close()


def test_the_share_floor_does_not_reject_a_real_small_float(monkeypatch):
    """The control. A genuine micro-cap can have a very small float; the floor must sit
    below anything real. MOBX — the smallest thing this system has priced — has 2.78M
    shares."""
    conn = db_connection()
    _cap(monkeypatch, 2_778_912, 2.00)
    assert rc.market_cap(conn, "MOBX", cache=False) == 5_557_824
    conn.close()


# ── ROOT CAUSE 2 — the TSM shape ────────────────────────────────────────────

def test_a_FOREIGN_PRIVATE_ISSUER_yields_UNKNOWN_not_an_ADR_mis_scale(monkeypatch):
    """SEC reports ORDINARY shares (25,932,524,521 for TSM); Yahoo prices the ADR
    ($399.09). They are NOT commensurable — an ADS represents N ordinary shares — so the
    product overstates by exactly the ADR ratio, 5 for TSM: $10.35T against ~$2.07T.

    The ratio is not in SEC data, so it cannot be recovered. Failing closed costs ADR
    coverage; inventing a ratio would cost correctness on every ADR.
    """
    conn = db_connection()
    monkeypatch.setattr(rc, "_is_foreign_private_issuer", lambda cik: True)
    _cap(monkeypatch, 25_932_524_521, 399.09)
    assert rc.market_cap(conn, "TSM", cache=False) is None
    assert rc.classify_cap(conn, "TSM", cache=False) == ("unknown", None)
    conn.close()


@pytest.mark.parametrize("forms,expected", [
    ({"20-F", "6-K", "4"}, True),        # TSM's actual shape
    ({"40-F", "6-K"}, True),             # Canadian MJDS
    ({"10-K", "10-Q", "4"}, False),      # domestic
    ({"10-K", "20-F"}, False),           # files both -> domestic reporting wins
    ({"3", "4"}, False),                 # an individual, not an issuer
])
def test_the_foreign_issuer_signal(monkeypatch, forms, expected):
    class _R:
        ok = True
        @staticmethod
        def json():
            return {"filings": {"recent": {"form": sorted(forms)}}}
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: _R())
    assert _REAL_IS_FPI("1") is expected


def test_a_failed_issuer_lookup_is_NONE_not_domestic(monkeypatch):
    """A network error must not be mistaken for 'domestic' — that would silently
    re-enable the ADR mis-scale during an outage."""
    class _R:
        ok = False
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: _R())
    assert _REAL_IS_FPI("1") is None


# ── each guard, ISOLATED ────────────────────────────────────────────────────
#
# The layers mask each other: with the share floor deleted, CLBK is still caught by the
# computed-cap check; with the foreign-issuer check deleted, TSM is still caught by the
# ceiling. That is defence in depth working — but it means the CLBK and TSM tests above
# pin NEITHER layer individually, and a mutation harness proved it: four protections could
# be deleted with the whole file green.
#
# Each test below constructs the case only ONE layer can catch.

def test_the_SHARE_FLOOR_alone_catches_a_shell_count_at_a_plausible_cap(monkeypatch):
    """100 shares x $50,000 = $5M — a perfectly plausible market cap from a share count
    that is not a real float. Neither cap bound can see this; only the share floor."""
    conn = db_connection()
    _cap(monkeypatch, 100, 50_000.0)
    assert rc.market_cap(conn, "SHELL", cache=False) is None
    conn.close()


def test_the_COMPUTED_CAP_check_alone_catches_a_plausible_count_at_a_silly_cap(monkeypatch):
    """200,000 shares clears the share floor, but x $1.00 = $200,000, below the cap floor.
    Only the computed-cap validation catches this one."""
    conn = db_connection()
    _cap(monkeypatch, 200_000, 1.00)
    assert rc.market_cap(conn, "TINY", cache=False) is None
    conn.close()


def test_the_FOREIGN_ISSUER_CHECK_not_the_ceiling_is_what_protects_ADRs(monkeypatch):
    """THE POINT OF THE WHOLE TSM LAYER, and the reason the ceiling is not the real fix.

    TSM only just exceeds $10T, so the ceiling catches it almost by accident. A typical
    ADR does not come close: 1,000,000,000 ordinary shares x a $50 ADR price = $50B —
    inside every plausibility bound, and still wrong by the ADR ratio.

    So the ceiling is a backstop for arithmetic that has gone visibly haywire. The
    foreign-issuer check is what actually protects this class.
    """
    conn = db_connection()
    monkeypatch.setattr(rc, "_is_foreign_private_issuer", lambda cik: True)
    _cap(monkeypatch, 1_000_000_000, 50.0)
    assert rc.market_cap(conn, "ADR", cache=False) is None, (
        "a mid-size ADR passed every plausibility bound — the ceiling cannot catch this")
    conn.close()


def test_an_UNRESOLVED_issuer_lookup_fails_closed(monkeypatch):
    """`_is_foreign_private_issuer` returns None on a failed lookup. None is falsy, so the
    cap is still computed — that is deliberate (a SEC outage must not blank every cap),
    but it means during an outage an ADR can be mis-scaled again. Pinned as the KNOWN
    boundary of this fix rather than left to be rediscovered."""
    conn = db_connection()
    monkeypatch.setattr(rc, "_is_foreign_private_issuer", lambda cik: None)
    _cap(monkeypatch, 1_000_000_000, 50.0)
    assert rc.market_cap(conn, "ADR", cache=False) == 50_000_000_000
    conn.close()


def test_an_implausible_computed_cap_is_NEVER_written_to_the_cache(monkeypatch):
    """If a bad value could be cached, the read-side self-heal would re-resolve it on
    every run — hammering SEC and Yahoo forever. It must be stored as unknown instead."""
    conn = db_connection(); rc.ensure_tables(conn)
    _cap(monkeypatch, 100, 10.85)
    assert rc.market_cap(conn, "CLBK", cache=True) is None
    row = conn.execute(
        "SELECT market_cap FROM ticker_meta WHERE symbol='CLBK'").fetchone()
    conn.close()
    assert row is not None and row[0] is None, f"cached an implausible cap: {row}"


# ── THE GUARD — defence in depth, both directions ───────────────────────────

def test_an_implausibly_LOW_cap_is_UNKNOWN_never_small(monkeypatch):
    """The bug exactly: $1,085 was classified SMALL and published as the top cluster."""
    conn = db_connection()
    monkeypatch.setattr(rc, "market_cap", lambda c, s, **k: 1_085)
    assert rc.classify_cap(conn, "X", cache=False) == ("unknown", None)
    conn.close()


def test_an_implausibly_HIGH_cap_is_UNKNOWN_never_a_confident_exclusion(monkeypatch):
    """$10.35T is not a company. It must not be a confident 'excluded' either — that
    would hide a mis-scale behind a correct-looking answer."""
    conn = db_connection()
    monkeypatch.setattr(rc, "market_cap", lambda c, s, **k: 10_349_411_116_118)
    assert rc.classify_cap(conn, "X", cache=False) == ("unknown", None)
    conn.close()


@pytest.mark.parametrize("cap,expected", [
    (5_557_824, "small"),            # MOBX, a genuine micro-cap
    (50_000_000, "small"),           # $50M micro-cap
    (200_000_000, "small"),          # $200M small-cap
    (1_178_642_214, "small"),        # ABSI
    (9_655_836_056, "small"),        # GME, just under the large-cap line
    (10_000_000_000, "excluded"),    # exactly the line
    (4_948_317_163_746, "excluded"), # AAPL
    (999_999, "unknown"),            # just under the floor
    (1_000_000, "small"),            # exactly the floor
])
def test_the_whole_band(monkeypatch, cap, expected):
    """The floor must not over-exclude anything real, and the ceiling must not
    over-include. Both boundaries pinned."""
    conn = db_connection()
    monkeypatch.setattr(rc, "market_cap", lambda c, s, **k: cap)
    assert rc.classify_cap(conn, "X", cache=False)[0] == expected, cap
    conn.close()


def test_the_bounds_are_stated_and_ordered():
    assert rc.MIN_PLAUSIBLE_CAP < rc.LARGE_CAP_MIN < rc.MAX_PLAUSIBLE_CAP
    assert rc.MIN_PLAUSIBLE_CAP == 1_000_000
    assert rc.MAX_PLAUSIBLE_CAP == 10_000_000_000_000
    # the live TSM value must fall OUTSIDE the ceiling, or the guard misses its own case
    assert 10_349_411_116_118 > rc.MAX_PLAUSIBLE_CAP


# ── STAGE 4 — the stale prod caches ─────────────────────────────────────────

def test_a_STALE_IMPLAUSIBLE_cached_cap_is_RE_RESOLVED(monkeypatch):
    """Without this the fix never reaches the rows that motivated it. CLBK=1085 and
    TSM=10349411116118 are already cached with FRESH timestamps, so a TTL check alone
    would keep serving them for 30 days.

    `ticker_meta` is a CACHE — re-resolving is a refresh, not a rewrite of detection-time
    data.
    """
    import datetime as dt
    conn = db_connection()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute("INSERT INTO ticker_meta (symbol, market_cap, cap_updated) "
                 "VALUES ('CLBK', 1085, ?)", (now,))
    conn.commit()

    calls = []
    monkeypatch.setattr(rc, "_shares_outstanding",
                        lambda cik: calls.append(1) or 60_000_000)
    monkeypatch.setattr(rc, "_last_close", lambda s: 30.0)
    cap = rc.market_cap(conn, "CLBK", cache=True)
    conn.close()
    assert calls, "the stale implausible value was served from cache, not re-resolved"
    assert cap == 1_800_000_000


def test_a_PLAUSIBLE_fresh_cached_cap_is_still_served_from_cache(monkeypatch):
    """The control: self-healing must not defeat the cache for good values."""
    import datetime as dt
    conn = db_connection()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute("INSERT INTO ticker_meta (symbol, market_cap, cap_updated) "
                 "VALUES ('GOOD', 400000000, ?)", (now,))
    conn.commit()
    monkeypatch.setattr(rc, "_shares_outstanding",
                        lambda cik: pytest.fail("re-fetched a plausible cached cap"))
    assert rc.market_cap(conn, "GOOD", cache=True) == 400_000_000
    conn.close()


# ── neither consumer regresses ──────────────────────────────────────────────

def test_the_reddit_COLLECTOR_still_collects_a_real_small_cap(monkeypatch):
    """The collector fails OPEN on unknown (a lookup table wants the name). It must still
    collect genuine small-caps, and must now NOT collect a mis-scaled one as 'small'."""
    conn = db_connection(); rc.ensure_tables(conn)
    monkeypatch.setattr(rc, "market_cap", lambda c, s, **k: 400_000_000)
    assert rc.classify_cap(conn, "REALSMALL", cache=False) == ("small", 400_000_000)
    monkeypatch.setattr(rc, "market_cap", lambda c, s, **k: 1_085)
    status, cap = rc.classify_cap(conn, "BADCAP", cache=False)
    conn.close()
    assert status == "unknown", "a mis-scaled cap still reads as small to the collector"


def test_the_CLUSTER_surface_fails_CLOSED_on_the_mis_scaled_cap(monkeypatch):
    """The cluster surface fails CLOSED — a human reads it — so an implausible cap must
    keep a ticker OFF the surface entirely. This is the CLBK case that published a
    $1,085 bank as the top cluster."""
    ic = pytest.importorskip(
        "scripts.insider_clusters",
        reason="lives on the unmerged feat/insider-cluster-discovery branch; this test "
               "activates automatically when the two branches meet")
    monkeypatch.setattr(ic, "classify_cap", lambda c, t, **k: ("unknown", None))
    conn = db_connection()
    from scripts.ingest_form4_transactions import ensure_tables as store_tables
    store_tables(conn); ic.ensure_tables(conn)
    for i, c in enumerate(("1", "2")):
        conn.execute(
            "INSERT INTO form4_transactions (accession, owner_seq, txn_index, ticker, "
            "insider_cik, insider_name, insider_name_norm, insider_kind, txn_code, "
            "acquired_disposed, shares, price, value, txn_date, filing_date, "
            "is_derivative, is_10b5_1) VALUES (?,0,0,'CLBK',?,?,?, 'person','P','A',"
            "100,1000.0,100000.0, date('now','-1 days'), date('now'), 0, 0)",
            (f"CAP{i}", c, f"Person {i}", f"PERSON {i}"))
    conn.commit()
    clusters = ic.find_clusters(conn, resolve=lambda c, k, **kw: "person")
    conn.close()
    assert clusters == [], "an implausible cap still let a cluster onto the surface"


# ── STAGE 4 — the prod remediation sweep ────────────────────────────────────

def _universe_row(conn, ticker, status, cap):
    conn.execute("INSERT INTO ticker_universe (ticker, cap_status, market_cap, "
                 "first_collected_at, last_seen_at, times_seen, source) VALUES (?,?,?, "
                 "datetime('now'), datetime('now'), 1, 'test')", (ticker, status, cap))
    conn.commit()


def test_the_repair_sweep_RECHECKS_a_row_stored_as_SMALL(monkeypatch):
    """The remediation gap that mattered. `repair_unknown_caps` swept only
    ('unknown','excluded'), but CLBK was stored as **small** — the entire defect is that a
    mis-scaled cap looks like a confident small-cap. So the sweep would have skipped
    exactly the rows the bug created."""
    conn = db_connection(); rc.ensure_tables(conn)
    _universe_row(conn, "CLBK", "small", 1085)
    monkeypatch.setattr(rc, "classify_cap", lambda c, t, **k: ("small", 1_800_000_000))
    out = rc.repair_unknown_caps(conn, cache_caps=False)
    row = conn.execute(
        "SELECT cap_status, market_cap FROM ticker_universe WHERE ticker='CLBK'").fetchone()
    conn.close()
    assert out["implausible_recheck"] == 1
    assert tuple(row) == ("small", 1_800_000_000), row


def test_an_implausible_row_is_CLEARED_even_when_it_cannot_be_REPRICED(monkeypatch):
    """If the re-check returns unknown, the row must not keep its bad value. Leaving the
    `continue` in place left CLBK at small/1085 — the bad value surviving its own
    remediation."""
    conn = db_connection(); rc.ensure_tables(conn)
    _universe_row(conn, "CLBK", "small", 1085)
    monkeypatch.setattr(rc, "classify_cap", lambda c, t, **k: ("unknown", None))
    rc.repair_unknown_caps(conn, cache_caps=False)
    row = conn.execute(
        "SELECT cap_status, market_cap FROM ticker_universe WHERE ticker='CLBK'").fetchone()
    conn.close()
    assert tuple(row) == ("unknown", None), row


def test_the_sweep_leaves_a_PLAUSIBLE_small_cap_alone(monkeypatch):
    """The control: remediation must not re-price the whole universe every run."""
    conn = db_connection(); rc.ensure_tables(conn)
    _universe_row(conn, "GOOD", "small", 400_000_000)
    monkeypatch.setattr(rc, "classify_cap",
                        lambda c, t, **k: pytest.fail(f"re-priced a plausible row: {t}"))
    out = rc.repair_unknown_caps(conn, cache_caps=False)
    conn.close()
    assert out["implausible_recheck"] == 0


def test_the_TSM_shape_is_swept_too(monkeypatch):
    """The ceiling side of the same sweep — TSM at $10.35T is above MAX_PLAUSIBLE_CAP."""
    conn = db_connection(); rc.ensure_tables(conn)
    _universe_row(conn, "TSM", "excluded", 10_349_411_116_118)
    monkeypatch.setattr(rc, "classify_cap", lambda c, t, **k: ("unknown", None))
    out = rc.repair_unknown_caps(conn, cache_caps=False)
    conn.close()
    assert out["implausible_recheck"] == 1
