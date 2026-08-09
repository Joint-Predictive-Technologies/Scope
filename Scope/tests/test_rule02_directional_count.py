"""RULE_02 must count and name only the members who actually traded directionally.

The defect: `find_clusters` took its member count from every member with any row
in the window, and its verb from a net over each member's FIRST row. So the count
and the verb were computed over different populations, and neither required the
member to have expressed a direction at all.

Measured on the real corpus before the fix — 4 of 82 stored RULE_02 alerts, 3 of
them HIGH:

    id 73 [HIGH]  "3 members sold WAT"     -> 1 directional (2 exchanges)
    id 74 [HIGH]  "2 members sold WAT"     -> 1 directional (1 exchange)
    id  5 [HIGH]  "2 members bought ABT"   -> 1 directional (1 exchange)
    id 72 [MEDIUM] "2 members traded VSNT" -> 0 directional (2 exchanges)

Both WAT alerts and the ABT one are the same shape: a real trader plus one or more
members whose only activity on the ticker was an exchange. This is the RULE_10
"signed leg" principle applied one level down — a member being PRESENT is not the
member SAYING the thing.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import rule_02_cluster as r02  # noqa: E402


# Fixed dates on purpose. `find_clusters` never reads a clock — only
# `fetch_transactions` does — so nothing here may depend on today's date.
D1 = "2026-06-01"
D2 = "2026-06-03"
D3 = "2026-06-05"


def _auto_party(member: str) -> str:
    """Deterministic D/R so any multi-member fixture is CROSS-PARTISAN by default.

    ⚠️ These tests are about the COUNT, the VERB and the KEY — not about party.
    RULE_02's cross-partisan hard gate (added later) means a cluster only forms
    when both major parties are present, so a fixture with no party at all now
    forms nothing and every assertion here would vanish rather than fail loudly.
    Party is derived from the member id, so it is stable and order-independent.
    The gate itself is exercised in test_rule02_cross_partisan.py; pass `party=`
    explicitly if a test ever needs to care.
    """
    # ⚠️ THIS DOES NOT GUARANTEE A MIXED FIXTURE — do not assume it does. It
    # alternates for the synthetic sequential ids these files mostly use, but REAL
    # bioguide ids collide freely: `C001123` and `G000583` both end in '3';
    # `B000000` and `S000000` both end in '0'. Three fixtures here pass `party=`
    # explicitly for exactly that reason.
    # ⚠️ THE FAILURE MODE IS SILENT: a same-last-digit pair forms NO cluster, so an
    # `== []`-shaped assertion passes vacuously. If a new fixture starts passing
    # for no visible reason, suspect this first and pass `party=` explicitly.
    return r02.DEMOCRAT if ord(member[-1]) % 2 == 0 else r02.REPUBLICAN


def txn(member, ticker, ttype, when=D1, name=None, party=None):
    return {
        "member_id": member,
        "ticker": ticker,
        "transaction_type": ttype,
        "transaction_date": when,
        "full_name": name if name is not None else f"Member {member}",
        "party": party if party is not None else _auto_party(member),
    }


def best(clusters):
    """The widest cluster found.

    `find_clusters` slides the window from every anchor row, so a shape spread
    over several dates yields overlapping clusters — the full set from the
    earliest anchor, then subsets from later ones. That is pre-existing and out
    of scope here; it is harmless in production because `alert_exists` dedups on
    the headline, so identical (count, verb, ticker) triples collapse to one
    alert (pinned in test_overlapping_anchors_collapse_to_one_headline).
    Assertions about "the cluster" mean the widest one.
    """
    assert clusters, "expected at least one cluster"
    return max(clusters, key=lambda c: c["member_count"])


# --------------------------------------------------------------------------
# The four stored shapes that were wrong
# --------------------------------------------------------------------------

def test_the_WAT_shape_no_longer_fires():
    """id 73: 3 members, only 1 directional. Was "3 members sold WAT (NET_SHORT)"."""
    rows = [
        txn("D000624", "WAT", "exchange", D1, "Dingell, Debbie"),
        txn("H001082", "WAT", "exchange", D2, "Hern, Kevin"),
        txn("K000395", "WAT", "sale_partial", D3, "Kean, Thomas H."),
    ]
    assert r02.find_clusters(rows, min_members=3) == []


def test_the_WAT_shape_had_three_present_but_one_directional():
    """The old count's input really was 3 — this is what made the alert look sound.

    Asserted through `find_clusters` rather than by re-deriving the predicate in
    the test. An earlier version recomputed `member_direction(...) in
    COUNTED_DIRECTIONS` itself and so was killed by no mutation of the code under
    test — it restated the implementation instead of checking it.

    ⚠️ WIDENED FROM THE ORIGINAL 3-present/1-directional SHAPE, and here is why.
    This used to probe at `min_members=1` and assert `member_count == 1`. RULE_02's
    cross-partisan gate makes that impossible by construction: a LONE member can
    never span both parties, so a 1-member cluster can no longer form at any
    threshold. The shape is therefore 4 present / 2 directional, with the two
    directional members spanning both parties. THE GUARD IS UNCHANGED — the two
    exchange-only members must still be absent from both the count and the tags,
    and reverting the directional-count fix still makes this read 4, not 2.
    """
    rows = [
        txn("D000624", "WAT", "exchange", D1),
        txn("H001082", "WAT", "exchange", D2),
        txn("K000395", "WAT", "sale_partial", D3, "Kean, Thomas H.", party=r02.REPUBLICAN),
        txn("P000034", "WAT", "sale", D3, "Pallone, Frank", party=r02.DEMOCRAT),
    ]
    assert len({r["member_id"] for r in rows}) == 4
    c = best(r02.find_clusters(rows, min_members=1))
    assert c["member_count"] == 2, "the two exchange-only members must not be counted"
    assert c["tags"] == "Kean, Thomas H.,Pallone, Frank"
    assert c["net_direction"] == "NET_SHORT"


def test_the_VSNT_shape_has_no_direction_at_all():
    """id 72: 2 exchange-only members, 0 directional.

    Asserted at min_members=2 deliberately. At the production default of 3 this
    shape is below threshold under the OLD logic too, so it cannot tell the two
    implementations apart and proves nothing. At 2 it discriminates: the old code
    saw two distinct members and emitted "2 members traded VSNT (MIXED)".
    """
    rows = [
        txn("D000624", "VSNT", "exchange", D1, "Dingell, Debbie"),
        txn("P000197", "VSNT", "exchange", D2, "Pelosi, Nancy"),
    ]
    assert r02.find_clusters(rows, min_members=2) == []


def test_the_ABT_shape_no_longer_fires():
    """id 5: 2 members, 1 exchange + 1 purchase. Was "2 members bought ABT"."""
    rows = [
        txn("F000474", "ABT", "exchange", D1, "Friedman, Laura"),
        txn("M001210", "ABT", "purchase", D2, "McCormick, Richard"),
    ]
    assert r02.find_clusters(rows, min_members=2) == []


# --------------------------------------------------------------------------
# The control: real clusters must survive
# --------------------------------------------------------------------------

def test_a_genuine_three_buyer_cluster_still_fires():
    rows = [
        txn("A000001", "NVDA", "purchase", D1),
        txn("B000002", "NVDA", "purchase", D2),
        txn("C000003", "NVDA", "purchase", D3),
    ]
    clusters = r02.find_clusters(rows, min_members=3)
    assert len(clusters) == 1
    assert clusters[0]["member_count"] == 3
    assert clusters[0]["net_direction"] == "NET_LONG"
    assert "3 members bought NVDA" in clusters[0]["headline"]
    assert clusters[0]["severity"] == "HIGH"


def test_an_uncounted_member_deflates_neither_the_cluster_nor_the_verb():
    """3 real sellers + 1 exchange-only member: still fires, but says 3, not 4."""
    rows = [
        txn("A000001", "WAT", "sale", D1),
        txn("B000002", "WAT", "sale_partial", D2),
        txn("C000003", "WAT", "sale", D3),
        txn("D000624", "WAT", "exchange", D2, "Dingell, Debbie"),
    ]
    clusters = r02.find_clusters(rows, min_members=3)
    assert len(clusters) == 1
    assert clusters[0]["member_count"] == 3
    assert "3 members sold WAT" in clusters[0]["headline"]
    assert clusters[0]["net_direction"] == "NET_SHORT"


def test_tags_name_exactly_the_counted_members():
    """The receipt has to describe the same set as the headline it evidences."""
    rows = [
        txn("A000001", "WAT", "sale", D1, "Alpha, Ann"),
        txn("B000002", "WAT", "sale", D2, "Bravo, Ben"),
        txn("C000003", "WAT", "sale", D3, "Charlie, Cy"),
        txn("D000624", "WAT", "exchange", D2, "Dingell, Debbie"),
    ]
    clusters = r02.find_clusters(rows, min_members=3)
    tags = clusters[0]["tags"]
    assert "Dingell, Debbie" not in tags
    assert tags == "Alpha, Ann,Bravo, Ben,Charlie, Cy"
    assert clusters[0]["member_count"] == 3


# --------------------------------------------------------------------------
# The asymmetry that the fix turned from cosmetic into a suppressor
# --------------------------------------------------------------------------

def test_purchase_partial_is_a_buy_not_neutral():
    """`direction()` prefixed the sale arm but equality-matched the purchase arm.

    Before the fix this was cosmetic — a neutral row only weakened the verb. After
    it, an uncounted member is DROPPED, so the same asymmetry silently suppresses a
    genuine 3-buyer cluster. This test is the reason `direction()` could not be
    left alone.
    """
    assert r02.direction("Purchase (Partial)") == "buy"
    rows = [
        txn("A000001", "NVDA", "Purchase (Partial)", D1),
        txn("B000002", "NVDA", "Purchase (Partial)", D2),
        txn("C000003", "NVDA", "purchase", D3),
    ]
    clusters = r02.find_clusters(rows, min_members=3)
    assert len(clusters) == 1
    assert clusters[0]["member_count"] == 3
    assert clusters[0]["net_direction"] == "NET_LONG"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("purchase", "buy"),
        ("Purchase", "buy"),
        ("Purchase (Partial)", "buy"),
        ("purchase_partial", "buy"),
        ("sale", "sell"),
        ("Sale (Full)", "sell"),
        ("Sale (Partial)", "sell"),
        ("sale_partial", "sell"),
        ("sale_full", "sell"),
        ("exchange", "neutral"),
        ("Exchange", "neutral"),
        (None, "neutral"),
        ("", "neutral"),
    ],
)
def test_direction_arms_are_symmetric(raw, expected):
    assert r02.direction(raw) == expected


def test_sale_full_is_directional_by_composition():
    """`sale_full` needs no entry here — `direction()` prefixes, so it is covered.

    This is the divergence that bit rule_cluster._member_direction, which tests a
    literal {"sale", "sale_partial"} set and therefore had to be taught `sale_full`
    separately.

    NOTE this passes on the unfixed baseline too — the sale arm always prefixed.
    It pins a property the fix must not lose, and documents why composing
    `direction()` is the right shape; it is not evidence FOR the fix.
    """
    assert r02.member_direction([txn("A", "T", "sale_full")]) == "sell"
    rows = [
        txn("A000001", "WAT", "sale_full", D1),
        txn("B000002", "WAT", "sale_full", D2),
        txn("C000003", "WAT", "sale_full", D3),
    ]
    clusters = r02.find_clusters(rows, min_members=3)
    assert len(clusters) == 1
    assert clusters[0]["net_direction"] == "NET_SHORT"


# --------------------------------------------------------------------------
# member_direction: over ALL the member's rows, not the first one
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "types,expected",
    [
        (["exchange"], "neutral"),
        (["exchange", "exchange"], "neutral"),
        (["exchange", "sale"], "sell"),
        (["sale", "exchange"], "sell"),
        (["exchange", "purchase"], "buy"),
        (["purchase", "sale"], "mixed"),
        (["sale", "purchase"], "mixed"),
        ([], "neutral"),
    ],
)
def test_member_direction_is_row_order_independent(types, expected):
    """The old code voted on the member's FIRST row, so order decided the verb."""
    rows = [txn("A000001", "T", t) for t in types]
    assert r02.member_direction(rows) == expected


def test_a_member_who_exchanged_then_sold_is_counted_as_a_seller():
    """3 sellers, one of whom also exchanged. Sanity check, NOT a mutation kill.

    Billed in an earlier revision as the end-to-end proof of order independence,
    which it is not: it survives the first-row-only mutation, because the sliding
    anchor re-emits the same headline from a later window that begins at the
    member's sale. That rescue looks structural — any anchor starting on the
    directional row makes the member directional again. The order dependence is
    killed by the `member_direction` unit cases above, not by this.
    """
    rows = [
        txn("A000001", "WAT", "exchange", D1),
        txn("A000001", "WAT", "sale", D2),
        txn("B000002", "WAT", "sale", D2),
        txn("C000003", "WAT", "sale", D3),
    ]
    c = best(r02.find_clusters(rows, min_members=3))
    assert c["member_count"] == 3
    assert c["net_direction"] == "NET_SHORT"


def test_a_mixed_member_counts_but_forces_the_verb_to_MIXED():
    """A member who both bought and sold is counted, but is not "a buyer".

    Letting them merely abstain from the vote reintroduced the original bug in a
    new form. Caught on the real corpus: MSFT has one buyer (Cisneros) plus one
    member who bought AND sold in the window (Gottheimer). With abstention that
    emitted "2 members bought MSFT (NET_LONG)" on the strength of a single buyer —
    count and verb describing different populations, which is the whole defect.
    """
    rows = [
        txn("A000001", "NVDA", "purchase", D1),
        txn("A000001", "NVDA", "sale", D2),
        txn("B000002", "NVDA", "purchase", D2),
        txn("C000003", "NVDA", "purchase", D3),
    ]
    c = best(r02.find_clusters(rows, min_members=3))
    assert c["member_count"] == 3
    assert c["net_direction"] == "MIXED"
    assert "3 members traded NVDA" in c["headline"]
    assert c["severity"] == "MEDIUM"


@pytest.mark.parametrize("n_buy,n_sell", [(1, 1), (2, 2), (3, 3)])
def test_a_buy_sell_TIE_is_MIXED_and_never_a_direction(n_buy, n_sell):
    """An even split must read "traded ... (MIXED)" at MEDIUM, never a direction.

    Found by the verifier as an uncaught escape in the rewritten `net_direction`:
    making the tie return NET_LONG instead of MIXED passed all 1291 tests, and on
    the real corpus flipped 10 clusters in the 90-day window (19 at 730 days) from
    MEDIUM/MIXED to HIGH/NET_LONG — e.g. "2 members traded AAPL (MIXED)" becoming
    "2 members bought AAPL (NET_LONG)". A 1-buy/1-sell cluster reported as a
    HIGH-severity consensus buy is precisely the failure this whole fix exists to
    prevent, so the tie branch is pinned here rather than left to inference.
    """
    # Explicit parties: B* and S* end in the same digit, so the auto rule makes
    # this single-party and the cross-partisan gate correctly forms no cluster at
    # all. The tie semantics under test are about DIRECTION, not party.
    rows = [txn(f"B{i:06d}", "AAPL", "purchase", D1, party=r02.DEMOCRAT) for i in range(n_buy)]
    rows += [txn(f"S{i:06d}", "AAPL", "sale", D1, party=r02.REPUBLICAN) for i in range(n_sell)]
    c = best(r02.find_clusters(rows, min_members=n_buy + n_sell))
    assert c["member_count"] == n_buy + n_sell
    assert c["net_direction"] == "MIXED"
    assert f"{n_buy + n_sell} members traded AAPL" in c["headline"]
    assert c["severity"] == "MEDIUM"


def test_the_MSFT_shape_keeps_the_verb_it_already_had():
    """Regression pin for the above, in the shape that exposed it."""
    rows = [
        # Both ids end in '3', so the auto rule makes this single-party. This
        # fixture is about the VERB, so the pair is made cross-partisan to let a
        # cluster form at all.
        txn("C001123", "MSFT", "purchase", D1, "Cisneros, Gilbert Ray", party=r02.DEMOCRAT),
        txn("G000583", "MSFT", "sale", D2, "Gottheimer, Josh", party=r02.REPUBLICAN),
        txn("G000583", "MSFT", "purchase", D2, "Gottheimer, Josh", party=r02.REPUBLICAN),
    ]
    c = best(r02.find_clusters(rows, min_members=2))
    assert c["member_count"] == 2
    assert c["net_direction"] == "MIXED"
    assert "2 members traded MSFT" in c["headline"]


# --------------------------------------------------------------------------
# Residual, deliberately NOT fixed here
# --------------------------------------------------------------------------

def test_the_verb_is_a_majority_not_unanimity_KNOWN_RESIDUAL():
    """RULE_02's verb is a MAJORITY. rule_cluster._cluster_direction requires unanimity.

    So "5 members bought NVDA" can still emit over 3 buyers and 2 sellers. This
    fix repaired the COUNT, not the verb's threshold, and the two rules remain
    deliberately different — do not "align" them without sign-off, because RULE_02's
    7-day window is the looser sibling by design.

    This test pins current behaviour and SHOULD fail when someone fixes it.
    """
    rows = [
        txn("A000001", "NVDA", "purchase", D1),
        txn("B000002", "NVDA", "purchase", D2),
        txn("C000003", "NVDA", "purchase", D3),
        txn("D000004", "NVDA", "sale", D2),
        txn("E000005", "NVDA", "sale", D3),
    ]
    c = best(r02.find_clusters(rows, min_members=3))
    assert c["member_count"] == 5
    assert c["net_direction"] == "NET_LONG"
    assert "5 members bought NVDA" in c["headline"]


def test_overlapping_anchors_collapse_to_one_headline():
    """Adjacent anchors over the same member set produce the SAME headline.

    `find_clusters` can return the same cluster from more than one anchor date.
    Because `alert_exists` keys on (rule, ticker, headline), that redundancy
    collapses at emit time rather than spamming alerts — but only as long as the
    headline stays a pure function of (count, verb, ticker).

    NOTE this passes on the unfixed baseline too. It pins pre-existing behaviour
    the fix must not break, not behaviour the fix introduces.
    """
    rows = [
        txn("A000001", "WAT", "sale", D1),
        txn("B000002", "WAT", "sale", D1),
        txn("C000003", "WAT", "sale", D2),
    ]
    clusters = r02.find_clusters(rows, min_members=3)
    full = [c for c in clusters if c["member_count"] == 3]
    assert len({c["headline"] for c in full}) == 1
