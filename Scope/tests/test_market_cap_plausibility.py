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

import datetime as _dt
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


def _cap(monkeypatch, shares, price, as_of=None):
    """`_shares_outstanding` returns (shares, as_of_date) — the date is part of the
    answer. Defaults to today so tests that are not about staleness are unaffected."""
    as_of = as_of or _dt.date.today().isoformat()
    monkeypatch.setattr(rc, "_shares_outstanding", lambda cik: (shares, as_of))
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
    """The control: the share floor must sit below anything real.

    An earlier version of this test asserted `2_778_912 x $2.00 == $5,557,824` and called
    it MOBX. That decomposition was INVENTED to reach a product I had observed — MOBX
    actually reports 10,599,296 shares at ~$0.52. The verifier then repeated my fabricated
    figure back to me, which is precisely how an unfounded specific becomes "established".

    Anchored instead on a real, independently-derived float: SEB/Seaboard, 957,794 shares
    — the smallest genuine common float found across all 5,109 listed filers carrying a
    dei share fact, and still ~9.6x above the floor.
    """
    conn = db_connection()
    _cap(monkeypatch, 957_794, 2_500.00)
    assert rc.market_cap(conn, "SEB", cache=False) == 2_394_485_000
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
    """This test's NAME used to assert the opposite of what it proved.

    It asserted `== 50_000_000_000` — i.e. fail-OPEN — under a name promising fail-closed,
    while `_is_foreign_private_issuer`'s docstring claimed "a network error is not mistaken
    for domestic". At the call site `if _is_foreign_private_issuer(cik):`, None is FALSY
    and WAS treated as domestic, re-enabling the ADR mis-scale during exactly the SEC
    degradation the None was introduced to signal.

    The call site now tests `is not False`, so all three of the name, the docstring and
    the behaviour finally agree.
    """
    conn = db_connection()
    monkeypatch.setattr(rc, "_is_foreign_private_issuer", lambda cik: None)
    _cap(monkeypatch, 1_000_000_000, 50.0)
    assert rc.market_cap(conn, "ADR", cache=False) is None
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
    monkeypatch.setattr(
        rc, "_shares_outstanding",
        lambda cik: calls.append(1) or (60_000_000, _dt.date.today().isoformat()))
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


# ── STALENESS — the dimension every other guard was blind to ────────────────
#
# Found by the verifier, on MOBX: the ticker this module previously used to CALIBRATE
# MIN_PLAUSIBLE_CAP. Its only dei share fact is 2023-11-14 while it has filed a 10-Q as
# recently as 2026-05-20, so the "$5.6M MOBX micro-cap" anchor was itself a mis-scale of
# the same family the module exists to stop.
#
# A stale count is plausible in MAGNITUDE and wrong in FACT, so no bound can see it.

def test_a_YEARS_STALE_share_fact_yields_UNKNOWN(monkeypatch):
    """The MOBX shape, with its real reported float (10,599,296 — NOT the 2,778,912 an
    earlier version of this file invented)."""
    conn = db_connection()
    _cap(monkeypatch, 10_599_296, 0.52, as_of="2023-11-14")
    assert rc.market_cap(conn, "MOBX", cache=False) is None
    conn.close()


def test_a_FRESH_share_fact_of_the_same_size_is_accepted(monkeypatch):
    """The control that makes the test above mean something: identical numbers, current
    date. Staleness must be the ONLY thing that rejected it."""
    conn = db_connection()
    _cap(monkeypatch, 10_599_296, 0.52)
    assert rc.market_cap(conn, "MOBX", cache=False) == 5_511_633
    conn.close()


@pytest.mark.parametrize("age_days,accepted", [
    (343, True),    # the STALEST real filer measured across all 5,109
    (365, True),    # 100% of real listed filers fall within this
    (540, True),    # exactly the threshold
    (541, False),   # one day past
    (987, False),   # MOBX
])
def test_the_staleness_threshold_boundary(monkeypatch, age_days, accepted):
    """MEASURED, not guessed. Across all 5,109 listed filers carrying a dei share fact,
    100% are within 365 days and the stalest is 343 — so 540 drops zero real filers.
    Both sides of the boundary pinned."""
    conn = db_connection()
    as_of = (_dt.date.today() - _dt.timedelta(days=age_days)).isoformat()
    _cap(monkeypatch, 50_000_000, 10.0, as_of=as_of)
    got = rc.market_cap(conn, "X", cache=False)
    conn.close()
    assert (got is not None) is accepted, f"age={age_days} -> {got}"


def test_an_UNPARSEABLE_as_of_date_fails_closed(monkeypatch):
    conn = db_connection()
    _cap(monkeypatch, 50_000_000, 10.0, as_of="not-a-date")
    assert rc.market_cap(conn, "X", cache=False) is None
    conn.close()


def test_the_share_fact_carries_its_DATE_not_a_bare_number(monkeypatch):
    """The structural fix. `_shares_outstanding` returning a bare float was the reason
    staleness was invisible: a value carrying no information about the dimension that
    invalidates it. The date now travels with it so a caller cannot forget to check."""
    class _R:
        ok = True
        @staticmethod
        def json():
            return {"units": {"shares": [
                {"end": "2024-01-01", "val": 111, "filed": "2024-02-01"},
                {"end": "2026-01-01", "val": 222, "filed": "2026-02-01"}]}}
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: _R())
    assert rc._shares_outstanding("1") == (222.0, "2026-01-01")


def test_a_RESTATEMENT_at_the_same_period_end_takes_the_LATER_FILING(monkeypatch):
    """`max` on `end` alone leaves a tie to whatever order the rows arrived in — the same
    arbitrary-representative class as the insider module's K12."""
    class _R:
        ok = True
        @staticmethod
        def json():
            return {"units": {"shares": [
                {"end": "2026-01-01", "val": 111, "filed": "2026-02-01"},
                {"end": "2026-01-01", "val": 222, "filed": "2026-05-01"}]}}
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: _R())
    assert rc._shares_outstanding("1") == (222.0, "2026-01-01")


# ── boundaries and branches the verifier's mutants found unpinned ───────────

@pytest.mark.parametrize("cap,expected", [
    (rc.MAX_PLAUSIBLE_CAP - 1, "excluded"),   # just inside the ceiling
    (rc.MAX_PLAUSIBLE_CAP, "excluded"),       # EXACTLY the ceiling -> inclusive
    (rc.MAX_PLAUSIBLE_CAP + 1, "unknown"),    # one dollar past
])
def test_the_CEILING_boundary_is_inclusive(monkeypatch, cap, expected):
    conn = db_connection()
    monkeypatch.setattr(rc, "market_cap", lambda c, s, **k: cap)
    assert rc.classify_cap(conn, "X", cache=False)[0] == expected, cap
    conn.close()


@pytest.mark.parametrize("shares,accepted", [
    (rc.MIN_PLAUSIBLE_SHARES - 1, False),
    (rc.MIN_PLAUSIBLE_SHARES, True),          # EXACTLY the floor -> inclusive
])
def test_the_SHARE_FLOOR_boundary_is_inclusive(monkeypatch, shares, accepted):
    conn = db_connection()
    _cap(monkeypatch, shares, 100.0)
    assert (rc.market_cap(conn, "X", cache=False) is not None) is accepted
    conn.close()


@pytest.mark.parametrize("cap,accepted", [
    (rc.MIN_PLAUSIBLE_CAP, True),             # computed-cap bound, low side, inclusive
    (rc.MAX_PLAUSIBLE_CAP, True),             # high side, inclusive
])
def test_the_COMPUTED_CAP_bounds_are_inclusive(monkeypatch, cap, accepted):
    conn = db_connection()
    _cap(monkeypatch, 1_000_000, cap / 1_000_000)
    assert (rc.market_cap(conn, "X", cache=False) is not None) is accepted
    conn.close()


def test_a_stale_cached_cap_ABOVE_the_ceiling_is_also_re_resolved(monkeypatch):
    """The self-heal's HIGH side — TSM's direction. Only the low (CLBK) side was tested,
    so a mutant narrowing the check to `row[0] < MIN` survived the whole suite."""
    conn = db_connection()
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    conn.execute("INSERT INTO ticker_meta (symbol, market_cap, cap_updated) "
                 "VALUES ('TSM', 10349411116118, ?)", (now,))
    conn.commit()
    calls = []
    monkeypatch.setattr(
        rc, "_shares_outstanding",
        lambda cik: calls.append(1) or (50_000_000, _dt.date.today().isoformat()))
    monkeypatch.setattr(rc, "_last_close", lambda s: 40.0)
    cap = rc.market_cap(conn, "TSM", cache=True)
    conn.close()
    assert calls, "an implausibly HIGH cached cap was served from cache"
    assert cap == 2_000_000_000


def test_the_FOREIGN_ISSUER_path_caches_its_unknown(monkeypatch):
    """Every fail-closed branch must record the unknown, or that ticker re-hits SEC and
    Yahoo on every single run — the refetch storm this module was already burned by."""
    conn = db_connection(); rc.ensure_tables(conn)
    monkeypatch.setattr(rc, "_is_foreign_private_issuer", lambda cik: True)
    _cap(monkeypatch, 1_000_000_000, 50.0)
    assert rc.market_cap(conn, "ADR", cache=True) is None
    row = conn.execute("SELECT market_cap, cap_updated FROM ticker_meta "
                       "WHERE symbol='ADR'").fetchone()
    conn.close()
    assert row is not None and row[0] is None and row[1], "FPI path cached nothing"


def test_the_COMPUTED_CAP_branch_caches_its_unknown(monkeypatch):
    """The branch `test_an_implausible_computed_cap_is_NEVER_written_to_the_cache` was
    NAMED for but never reached — a line trace showed it returning early via the share
    floor. This one reaches it: 200,000 shares clears the floor, x $1.00 does not clear
    the cap bound."""
    conn = db_connection(); rc.ensure_tables(conn)
    _cap(monkeypatch, 200_000, 1.00)
    assert rc.market_cap(conn, "TINY", cache=True) is None
    row = conn.execute("SELECT market_cap, cap_updated FROM ticker_meta "
                       "WHERE symbol='TINY'").fetchone()
    conn.close()
    assert row is not None and row[0] is None and row[1], "computed-cap path cached nothing"


def test_the_STALENESS_branch_caches_its_unknown(monkeypatch):
    conn = db_connection(); rc.ensure_tables(conn)
    _cap(monkeypatch, 50_000_000, 10.0, as_of="2023-11-14")
    assert rc.market_cap(conn, "MOBX", cache=True) is None
    row = conn.execute("SELECT market_cap FROM ticker_meta WHERE symbol='MOBX'").fetchone()
    conn.close()
    assert row is not None and row[0] is None


@pytest.mark.parametrize("forms,expected", [
    ({"20-F"}, True),      # 20-F ALONE
    ({"40-F"}, True),      # 40-F ALONE  — was never pinned; every True case carried 6-K
    ({"6-K"}, True),       # 6-K ALONE
    ({"10-Q"}, False),     # 10-Q ALONE proves domestic — and CLBK is exactly this shape
    ({"10-K"}, False),     # 10-K ALONE
    (set(), False),        # no forms at all
])
def test_each_form_signal_INDIVIDUALLY(monkeypatch, forms, expected):
    """The original five-case table looked thorough and pinned almost nothing: every
    'True' row carried a second foreign form, so no individual form was load-bearing, and
    the only 10-Q row also carried 10-K."""
    class _R:
        ok = True
        @staticmethod
        def json():
            return {"filings": {"recent": {"form": sorted(forms)}}}
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: _R())
    assert _REAL_IS_FPI("1") is expected


def test_an_EXCEPTION_during_the_issuer_lookup_is_NONE(monkeypatch):
    """The except branch was entirely untested — a mutant returning True survived."""
    def _boom(*a, **k):
        raise ValueError("network")
    monkeypatch.setattr(rc.requests, "get", _boom)
    assert _REAL_IS_FPI("1") is None


def test_the_repair_sweep_DEDUPES_its_worklist(monkeypatch):
    """A row that is BOTH implausible and cap_status='unknown' appears in both queries.
    Without the dedupe it is repriced twice — two SEC round trips per row per run."""
    conn = db_connection(); rc.ensure_tables(conn)
    _universe_row(conn, "DUP", "unknown", 500)
    seen = []
    monkeypatch.setattr(rc, "classify_cap",
                        lambda c, t, **k: seen.append(t) or ("unknown", None))
    rc.repair_unknown_caps(conn, cache_caps=False)
    conn.close()
    assert seen.count("DUP") == 1, f"repriced {seen.count('DUP')}x: {seen}"
