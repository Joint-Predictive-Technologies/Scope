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
import inspect
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

    ⚠️ THIS DOCSTRING WAS ITSELF THE FABRICATION, AND SAID SO OF THE TRUE NUMBER.
    It claimed `2,778,912 x $2.00` was "INVENTED" and that MOBX "actually reports
    10,599,296 shares at ~$0.52". Neither is so. The correction was derived from CIK
    `0001855555` — which is **ACCRETION ACQUISITION CORP**, not MOBX — and the name the
    CIK returned was never checked. MOBX is CIK `0001855467`, **MOBIX LABS, INC**, and it
    really does report **2,778,912** shares (end 2023-11-14, the newest of ten facts).
    `10,599,296` appears in no concept for the real MOBX at all.

    So the ORIGINAL figure was right the whole time. That is the rule this file now
    follows: no share figure without the CIK it came from AND the name that CIK returned.

    Anchored instead on a real, independently-derived float: SEB/Seaboard, 957,794 shares
    — ~9.6x above the share floor.

    ⚠️ NOT "the smallest across all listed filers". That claim rested on the same
    discredited recent-quarters sample. Over the FULL population smaller FRESH floats
    exist, and the module's own `MIN_PLAUSIBLE_CAP` comment names two of them a few lines
    apart: ADTX 815,921 and RUBI 385,501.
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


def test_the_SHARE_FLOOR_path_writes_an_unknown_to_the_cache(monkeypatch):
    """⚠️ RENAMED, because the old name lied about which branch it reached.

    It was called `test_an_implausible_computed_cap_is_NEVER_written_to_the_cache`, but
    with `shares=100` it returns at the SHARE FLOOR and never reaches the computed-cap
    branch it was named for — a line trace showed the computed-cap lines never executing,
    and a mutant deleting `_cache_unknown` from that branch survived the whole suite while
    this test stayed green. The computed-cap branch has its own test below
    (`test_the_COMPUTED_CAP_branch_caches_its_unknown`), which does reach it.

    What this actually tests — and now says — is the SHARE-FLOOR path's caching. Kept,
    because that path needs the same guarantee: a bad value must never enter the cache,
    or the read-side self-heal re-resolves it against SEC and Yahoo on every run forever.
    """
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
    (5_557_824, "small"),            # a real micro-cap magnitude (MOBX's 2023 fact x
                                     # ~$2.00 — correct arithmetic on a STALE input; the
                                     # staleness is caught upstream, not by this band)
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
    """The MOBX shape, with its REAL reported float: 2,778,912 at end 2023-11-14 — the
    number an earlier version of this file wrongly branded invented."""
    conn = db_connection()
    _cap(monkeypatch, 2_778_912, 2.00, as_of="2023-11-14")
    assert rc.market_cap(conn, "MOBX", cache=False) is None
    conn.close()


def test_a_FRESH_share_fact_of_the_same_size_is_accepted(monkeypatch):
    """The control that makes the test above mean something: identical numbers, current
    date. Staleness must be the ONLY thing that rejected it."""
    conn = db_connection()
    _cap(monkeypatch, 2_778_912, 2.00)
    assert rc.market_cap(conn, "MOBX", cache=False) == 5_557_824
    conn.close()


@pytest.mark.parametrize("age_days,accepted", [
    (343, True),    # comfortably fresh
    (365, True),    # ~90% of real listed filers fall within this
    (540, True),    # exactly the threshold
    (541, False),   # one day past
    (987, False),   # MOBX's cover-page tag
])
def test_the_staleness_threshold_boundary(monkeypatch, age_days, accepted):
    """Both sides of the boundary, pinned.

    ⚠️ THIS DOCSTRING USED TO CARRY THE FALSIFIED MEASUREMENT — "MEASURED, not guessed.
    Across all 5,109 listed filers … 100% are within 365 days and the stalest is 343 — so
    540 drops zero real filers" — on the very parametrisation that pins the threshold.
    That figure came from `xbrl/frames` over recent quarters, a sample that BY
    CONSTRUCTION could only contain recent dates. The module comment was corrected and
    this one was missed, so the discredited number outlived its own retraction by a commit.

    POPULATION, re-derived per-CIK over every entry in `company_tickers.json` (8,017
    CIKs; 5,687 carry a fact): **89.96% within 365 days, stalest 6,053 days (CMCSA,
    end=2009-12-31), and 447 (7.86%) more than 540 days from today in either direction.**

    So 540 does NOT "drop zero real filers" — alone it would drop hundreds. What keeps
    real large-caps out of `unknown` is the `_SHARE_CONCEPTS` fallback plus the rule that
    staleness only fails closed on a cap that would read SMALL. The threshold is one
    component, not the protection.
    """
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


# ════════════════════════════════════════════════════════════════════════════
#  The certification's three instances — pinned
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("as_of,label", [
    ("2033-10-31", "THM — declared age -2652d, really 993d stale"),
    ("2033-09-12", "AXR — declared age -2603d, really 1049d stale"),
    ("2029-04-03", "REPX — declared age -980d, really 846d stale"),
])
def test_INSTANCE2_a_FUTURE_as_of_date_is_caught(monkeypatch, as_of, label):
    """THE FIFTH DIMENSION: the bound was `age > MAX`, so a NEGATIVE age — an `end` date
    in the future — was never greater and passed unchallenged. These three are real
    filers, and they were publishing as confident small caps. The most obviously
    malformed input in the corpus was the one input the guard ignored."""
    conn = db_connection()
    _cap(monkeypatch, 50_000_000, 10.0, as_of=as_of)     # $500M -> would read SMALL
    assert rc.market_cap(conn, "X", cache=False) is None, label
    conn.close()


@pytest.mark.parametrize("age_days", [541, 987, 6053])
def test_INSTANCE1_a_stale_count_that_still_reads_LARGE_is_KEPT(monkeypatch, age_days):
    """A blanket staleness rejection dropped 443 real filers to `unknown` — Comcast,
    Visa, UPS, Mastercard, Accenture, Ford — and because the collector FAILS OPEN they
    were then COLLECTED, defeating gate 3 ("$AAPL does not need discovering").

    Staleness only misleads in the direction that matters: a stale count still yielding
    >= LARGE_CAP_MIN is a confirmed large cap either way. 6053 days is Comcast's real
    cover-page age.
    """
    conn = db_connection()
    as_of = (_dt.date.today() - _dt.timedelta(days=age_days)).isoformat()
    _cap(monkeypatch, 2_000_000_000, 25.0, as_of=as_of)  # $50B
    cap = rc.market_cap(conn, "CMCSA", cache=False)
    status, _ = rc.classify_cap(conn, "CMCSA", cache=False)
    conn.close()
    assert cap == 50_000_000_000, f"a stale LARGE cap was dropped to unknown ({age_days}d)"
    assert status == "excluded", "and gate 3 must still exclude it"


@pytest.mark.parametrize("age_days", [541, 987, 6053])
def test_INSTANCE1_the_other_direction_a_stale_SMALL_cap_still_fails_closed(
        monkeypatch, age_days):
    """The control that keeps the exemption honest. Without it the rule would read
    'staleness never matters', which is the mis-scale this dimension exists to stop."""
    conn = db_connection()
    as_of = (_dt.date.today() - _dt.timedelta(days=age_days)).isoformat()
    _cap(monkeypatch, 2_778_912, 2.00, as_of=as_of)      # $5.56M -> SMALL
    assert rc.market_cap(conn, "MOBX", cache=False) is None
    conn.close()


def test_the_large_cap_exemption_is_at_the_LARGE_CAP_MIN_boundary(monkeypatch):
    conn = db_connection()
    stale = (_dt.date.today() - _dt.timedelta(days=1000)).isoformat()
    _cap(monkeypatch, 1_000_000_000, 10.0, as_of=stale)          # exactly $10B
    assert rc.market_cap(conn, "X", cache=False) == 10_000_000_000
    _cap(monkeypatch, 999_999_999, 10.0, as_of=stale)            # a dollar under
    assert rc.market_cap(conn, "X", cache=False) is None
    conn.close()


def test_the_share_concept_FALLBACK_prefers_the_freshest_fact(monkeypatch):
    """Instance 1's root cause: `companyconcept` returns only the ancient UNDIMENSIONED
    cover-page fact for hundreds of real filers (Comcast's newest is end=2009-12-31), so
    the current number lives in another concept. MOBX is the live case — its cover-page
    fact is 2023-11-14, but `WeightedAverageNumberOfSharesOutstandingBasic` is 2026-03-31.
    """
    facts = {
        ("dei", "EntityCommonStockSharesOutstanding"): (2_778_912.0, "2023-11-14"),
        ("us-gaap", "CommonStockSharesOutstanding"): (23_600_558.0, "2024-03-31"),
        ("us-gaap", "CommonStockSharesIssued"): (2_000_000.0, "2023-09-30"),
        ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"):
            (8_058_263.0, "2026-03-31"),
    }
    monkeypatch.setattr(rc, "_concept_fact", lambda cik, t, c: facts.get((t, c)))
    assert rc._shares_outstanding("1") == (8_058_263.0, "2026-03-31")


def test_the_fallback_STOPS_once_a_fresh_fact_is_found(monkeypatch):
    """~90% of filers have a fresh cover-page tag. Querying every fallback for all of them
    would multiply SEC traffic for no gain, so the search stops at the first fresh hit."""
    asked = []

    def _probe(cik, tax, con):
        asked.append(con)
        return (5_000_000.0, _dt.date.today().isoformat())
    monkeypatch.setattr(rc, "_concept_fact", _probe)
    rc._shares_outstanding("1")
    assert asked == ["EntityCommonStockSharesOutstanding"], \
        f"kept querying after a fresh fact: {asked}"


def test_the_fallback_returns_the_freshest_even_when_ALL_are_stale(monkeypatch):
    facts = {("dei", "EntityCommonStockSharesOutstanding"): (100.0, "2009-12-31"),
             ("us-gaap", "CommonStockSharesOutstanding"): (200.0, "2015-06-30")}
    monkeypatch.setattr(rc, "_concept_fact", lambda cik, t, c: facts.get((t, c)))
    assert rc._shares_outstanding("1") == (200.0, "2015-06-30")


def test_a_filer_with_NO_share_fact_anywhere_is_unknown(monkeypatch):
    monkeypatch.setattr(rc, "_concept_fact", lambda cik, t, c: None)
    assert rc._shares_outstanding("1") is None


def test_LINT_INSTANCE3_no_fabricated_MOBX_figure_survives():
    """A PROVENANCE LINT — explicitly NOT a protection, and nothing rests on it. The real
    protections are the behavioural tests above; this only stops a known-bad literal
    creeping back in.

    `10,599,296` came from CIK `0001855555` — ACCRETION ACQUISITION CORP — and appears in
    no concept for the real MOBX (CIK `0001855467`, MOBIX LABS, INC). The literals are
    ASSEMBLED below so this test does not match itself.
    """
    # Only the PYTHON-LITERAL forms are linted. The prose above deliberately names the
    # fabricated values so the mistake stays legible; a value pinned as CODE is the thing
    # that must not come back. Literals are assembled so this test cannot match itself.
    fabricated = ["10" + "_599_296", "5_511" + "_633"]
    src = inspect.getsource(rc)
    body = open(__file__).read()
    for bad in fabricated:
        assert bad not in src, f"{bad} is back in the module"
        assert bad not in body, f"{bad} is pinned again in this file"


# ════════════════════════════════════════════════════════════════════════════
#  The SECOND verifier pass — defects this repair itself introduced
# ════════════════════════════════════════════════════════════════════════════

def test_the_fallback_takes_the_FRESHEST_not_the_first_fresh(monkeypatch):
    """HEICO, live: the cover-page tag is stale, `CommonStockSharesOutstanding`
    (55,143,000 @2025-10-31) is INSIDE the 540-day window so the loop stopped there — and
    never saw `WeightedAverageNumberOfSharesOutstandingBasic` 139,464,000 @2026-04-30.
    Published $19.9B against a real ~$50B, unflagged, because "first fresh" is not
    "freshest"."""
    facts = {
        ("dei", "EntityCommonStockSharesOutstanding"): (10_000.0, "2015-12-15"),
        ("us-gaap", "CommonStockSharesOutstanding"): (55_143_000.0, "2025-10-31"),
        ("us-gaap", "CommonStockSharesIssued"): None,
        ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"):
            (139_464_000.0, "2026-04-30"),
    }
    monkeypatch.setattr(rc, "_concept_fact", lambda cik, t, c: facts.get((t, c)))
    assert rc._shares_outstanding("1") == (139_464_000.0, "2026-04-30")


def test_a_BOGUS_FUTURE_date_does_not_win_the_freshest_contest(monkeypatch):
    """ASLE/THM/AXR/REPX each carry a typo'd future `end` AND a perfectly good current
    fact one concept away. Ranking by "latest end" — a string compare — buried the real
    fact behind the typo and dropped four priceable filers to `unknown`. Ranking by
    DISTANCE FROM TODAY puts the real one first."""
    facts = {
        ("dei", "EntityCommonStockSharesOutstanding"): (5_271_309.0, "2033-09-12"),
        ("us-gaap", "CommonStockSharesOutstanding"): (5_305_199.0, "2026-04-30"),
    }
    monkeypatch.setattr(rc, "_concept_fact", lambda cik, t, c: facts.get((t, c)))
    assert rc._shares_outstanding("1") == (5_305_199.0, "2026-04-30")


def test_the_cover_page_tag_still_short_circuits_when_FRESH(monkeypatch):
    """The saving must survive the fix: ~90% of filers have a fresh cover-page tag, and
    querying three more concepts for all of them would multiply SEC traffic for nothing."""
    asked = []

    def _probe(cik, tax, con):
        asked.append(con)
        return (5_000_000.0, _dt.date.today().isoformat())
    monkeypatch.setattr(rc, "_concept_fact", _probe)
    rc._shares_outstanding("1")
    assert asked == ["EntityCommonStockSharesOutstanding"], \
        f"kept querying after a fresh COVER-PAGE fact: {asked}"


def test_a_stale_cover_page_tag_does_NOT_short_circuit(monkeypatch):
    """The other half — the bug was short-circuiting on ANY fresh concept, not the
    cover-page one."""
    asked = []

    def _probe(cik, tax, con):
        asked.append(con)
        return (1_000.0, "2015-01-01") if con == "EntityCommonStockSharesOutstanding" \
            else (9_000_000.0, _dt.date.today().isoformat())
    monkeypatch.setattr(rc, "_concept_fact", _probe)
    got = rc._shares_outstanding("1")
    assert len(asked) == 4, f"stopped early on a stale cover-page tag: {asked}"
    assert got[0] == 9_000_000.0


def test_a_start_date_TIE_is_broken_by_the_shorter_period(monkeypatch):
    """MOBX, live: two weighted-average rows tie on BOTH `end` and `filed` and differ only
    in `start`. Sorting on `(end, filed)` alone left a 22% swing in the published cap to
    JSON row order.

    ⚠️ The LATER `start` must win, and the VALUE must not decide it. A first version of
    this test used MOBX's real rows, where the later `start` also happens to carry the
    larger `val` — so deleting `start` from the sort key left `val` picking the same
    winner and the mutant SURVIVED. These rows invert that: the later, shorter period
    carries the SMALLER count, so only `start` gives the right answer.
    """
    class _R:
        ok = True
        @staticmethod
        def json():
            return {"units": {"shares": [
                {"start": "2025-10-01", "end": "2026-03-31", "filed": "2026-07-09",
                 "val": 9_000_000},          # six-month average, LARGER
                {"start": "2026-01-01", "end": "2026-03-31", "filed": "2026-07-09",
                 "val": 7_000_000},          # three-month average, more current
            ]}}
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: _R())
    for _ in range(5):      # deterministic, not row-order luck
        assert rc._concept_fact("1", "us-gaap", "X") == (7_000_000.0, "2026-03-31")


# ── the web layer, which had ZERO coverage ──────────────────────────────────

def _meta(monkeypatch, cap):
    from fastapi.testclient import TestClient
    import api.routers.tickers as rt
    from api.main import app
    monkeypatch.setattr(rt, "_fetch_market_cap", lambda conn, s: cap)
    monkeypatch.setattr(rt, "_fetch_social_spike", lambda s: False)
    return TestClient(app)


def test_the_ticker_meta_endpoint_flags_a_KNOWN_cap(monkeypatch):
    d = _meta(monkeypatch, 134_000_000_000).get("/tickers/LMT/meta").json()
    assert d["market_cap"] == 134_000_000_000
    assert d["cap_status"] == "known" and d["cap_resolved"] is True


def test_the_ticker_meta_endpoint_flags_an_UNKNOWN_cap(monkeypatch):
    d = _meta(monkeypatch, None).get("/tickers/CLBK/meta").json()
    assert d["market_cap"] is None
    assert d["cap_status"] == "unknown" and d["cap_resolved"] is True


def test_the_CACHE_HIT_path_carries_the_same_flags_as_the_miss(monkeypatch):
    """The gap a verifier found: the 24h cache hit returned a bare `dict(row)`, so
    `cap_resolved` was absent on every request after the first and the page fell back to
    the dead-end 'Market cap unavailable'. Since the endpoint stamps a fresh
    `cap_updated` on every resolve, the honest wording was reachable at most once per
    ticker per day."""
    c = _meta(monkeypatch, None)
    first = c.get("/tickers/CLBK/meta").json()
    second = c.get("/tickers/CLBK/meta").json()          # served from ticker_meta
    assert first["cap_resolved"] is True
    assert second["cap_resolved"] is True, "the cache hit lost cap_resolved"
    assert second["cap_status"] == "unknown"


def test_the_endpoint_writes_ONLY_ticker_meta(monkeypatch):
    conn = db_connection()
    before = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("alerts", "themes", "activity_log", "watchlist")}
    conn.close()
    _meta(monkeypatch, 5_000_000_000).get("/tickers/LMT/meta")
    conn = db_connection()
    after = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
    cached = conn.execute("SELECT market_cap FROM ticker_meta WHERE symbol='LMT'"
                          ).fetchone()
    conn.close()
    assert after == before, f"the ticker page wrote Scope data: {before} -> {after}"
    assert cached and cached[0] == 5_000_000_000


def test_the_page_distinguishes_resolved_unknown_from_never_looked_up():
    """The frontend contract. Both used to render the same dead-end string."""
    page = open(os.path.join(os.path.dirname(__file__), "..", "api", "static",
                             "ticker.html")).read()
    assert "Market cap unknown — no reliable share count" in page
    assert "d.cap_resolved" in page
    # and render must not block on the network call
    assert "withTimeout(8000)" in page


def test_the_ticker_page_does_NOT_double_write_the_cap_cache(monkeypatch):
    """The endpoint owns the `ticker_meta` write. Letting the resolver cache too
    (`cache=True`) produced two upserts per page view for the same symbol — harmless to
    the value, but it stamps `cap_updated` twice and doubles the write path on a surface
    whose whole claim is that it is read-only."""
    import api.routers.tickers as rt
    calls = []
    monkeypatch.setattr(
        "scripts.rule_reddit_collector.market_cap",
        lambda conn, sym, **kw: calls.append(kw.get("cache")) or 5_000_000_000)
    conn = db_connection()
    rt._fetch_market_cap(conn, "LMT")
    conn.close()
    assert calls == [False], f"the page's resolver was allowed to cache: {calls}"


def test_LINT_the_page_binds_its_wording_to_cap_resolved():
    """A TEMPLATE LINT, not a protection — there is no JS runtime in this suite, so the
    frontend conditional cannot be exercised behaviourally. A mutant that keeps the honest
    string but rewires the condition to `false` SURVIVES, and that is a stated limitation
    rather than a covered case. The endpoint side is pinned behaviourally above; this only
    checks the page reads the flag it is handed."""
    page = open(os.path.join(os.path.dirname(__file__), "..", "api", "static",
                             "ticker.html")).read()
    i = page.index("Market cap unknown — no reliable share count")
    assert "d.cap_resolved" in page[max(0, i - 400):i], \
        "the honest wording is not bound to cap_resolved"
