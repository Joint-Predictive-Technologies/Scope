"""RULE_02 means "a Democrat AND a Republican agree" — not "N members clustered".

The mechanical repairs (#1 directional count, #2 ticker resolution, #3 identity,
#4 refire) fixed how RULE_02 COUNTS. This is the DEFINITION: a same-party cluster
is a real but different, weaker signal, and under the hard gate it does not emit.

Measured on the snapshot before this change:

    live 90d window   4 clusters   4 cross-partisan    0 single-party
    365d             47 clusters  36 cross-partisan   11 single-party (8 HIGH)
    stored corpus    82 alerts    54 cross-partisan   28 single-party (17 HIGH+)

⚠️ THE GATE IS ON THE COUNTED MEMBERS. A member present in the window but not
trading directionally cannot supply the agreement, exactly as they cannot supply
the count (#1). ⚠️ INDEPENDENTS AND UNKNOWN PARTY satisfy neither side and block
nothing — an unknown must not be able to manufacture agreement, nor to destroy it.

⚠️ Party comes from `members.party` via a join RULE_02 ALREADY had for
`full_name`. There is no hardcoded member->party map anywhere in this change.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import rule_02_cluster as r02  # noqa: E402
from jpt_common import db_connection  # noqa: E402

D = r02.DEMOCRAT
R = r02.REPUBLICAN
IND = "Independent"

# Fixed dates: `find_clusters` never reads a clock.
D1, D2, D3 = "2026-06-01", "2026-06-02", "2026-06-03"


def txn(member, ticker, ttype, party, when=D1, name=None):
    return {
        "member_id": member,
        "ticker": ticker,
        "transaction_type": ttype,
        "transaction_date": when,
        "full_name": name if name is not None else f"Member {member}",
        "party": party,
    }


def best(clusters):
    assert clusters, "expected at least one cluster"
    return max(clusters, key=lambda c: c["member_count"])


# ---------------------------------------------------------------------------
# The definition
# ---------------------------------------------------------------------------

def test_a_genuine_D_plus_R_cluster_FIRES():
    cl = r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D, D2),
        txn("R1", "NVDA", "Purchase", R, D3),
    ], min_members=3)
    c = best(cl)
    assert c["member_count"] == 3
    assert c["party_split"] == (2, 1)


def test_the_headline_SAYS_cross_partisan_and_gives_the_split():
    cl = r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D, D2),
        txn("R1", "NVDA", "Purchase", R, D3),
    ], min_members=3)
    h = best(cl)["headline"]
    assert h.startswith("Cross-partisan cluster:"), h
    assert "[2D/1R]" in h, h
    assert "NVDA" in h and "NET_LONG" in h


@pytest.mark.parametrize("party", [D, R])
def test_a_SINGLE_PARTY_cluster_does_NOT_fire(party):
    """The whole point. All-D and all-R both stop."""
    assert r02.find_clusters([
        txn("M1", "NVDA", "Purchase", party),
        txn("M2", "NVDA", "Purchase", party, D2),
        txn("M3", "NVDA", "Purchase", party, D3),
    ], min_members=3) == []


def test_D_plus_R_plus_INDEPENDENT_fires_the_independent_does_not_block():
    cl = r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("R1", "NVDA", "Purchase", R, D2),
        txn("I1", "NVDA", "Purchase", IND, D3),
    ], min_members=3)
    c = best(cl)
    assert c["member_count"] == 3
    # The Independent is counted as a MEMBER but not as either party.
    assert c["party_split"] == (1, 1)


def test_an_UNKNOWN_party_member_does_not_block_an_existing_D_plus_R():
    """A NULL party is an unmatched filer. It must not veto real agreement."""
    cl = r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("R1", "NVDA", "Purchase", R, D2),
        txn("U1", "NVDA", "Purchase", None, D3),
    ], min_members=3)
    assert best(cl)["member_count"] == 3


def test_an_UNKNOWN_party_member_cannot_SUBSTITUTE_for_the_missing_side():
    """The other half of the asymmetry: unknown never manufactures agreement."""
    assert r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D, D2),
        txn("U1", "NVDA", "Purchase", None, D3),
    ], min_members=3) == []


def test_an_INDEPENDENT_cannot_substitute_for_the_missing_side_either():
    assert r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D, D2),
        txn("I1", "NVDA", "Purchase", IND, D3),
    ], min_members=3) == []


# ---------------------------------------------------------------------------
# The gate is on the COUNTED members, not everyone present
# ---------------------------------------------------------------------------

def test_an_EXCHANGE_ONLY_republican_cannot_supply_the_agreement():
    """#1 says presence is not direction; the same must hold for party.

    Three Democrats buy and one Republican merely exchanges. The Republican is
    in the window but not in `counted`, so this is a single-party cluster.
    """
    assert r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D, D2),
        txn("D3", "NVDA", "Purchase", D, D2),
        txn("R1", "NVDA", "Exchange", R, D3),
    ], min_members=3) == []


def test_a_DIRECTIONAL_republican_in_the_same_shape_DOES_supply_it():
    """Control for the test above — only the transaction type differs."""
    cl = r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D, D2),
        txn("D3", "NVDA", "Purchase", D, D2),
        txn("R1", "NVDA", "Purchase", R, D3),
    ], min_members=3)
    assert best(cl)["party_split"] == (3, 1)


def test_a_rejected_single_party_window_does_not_suppress_a_wider_CROSS_one():
    """The first anchor sees 3 Democrats; a later Republican makes a 4-member
    cross-partisan set on the same ticker. The 4-member cluster must still emit.

    ⚠️ THIS DOES NOT TEST THE GATE'S POSITION relative to `seen_windows`, and an
    earlier docstring claimed it did. `window_key` already contains `anchor_date`,
    so the two placements are indistinguishable — moving the gate below
    `seen_windows.add` kills no test here and changes no output on real data. What
    this DOES pin is the property that matters: a single-party subset must not
    prevent its cross-partisan superset from firing.
    """
    cl = r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D),
        txn("D3", "NVDA", "Purchase", D),
        txn("R1", "NVDA", "Purchase", R, D2),
    ], min_members=3)
    c = best(cl)
    assert c["member_count"] == 4
    assert c["party_split"] == (3, 1)


# ---------------------------------------------------------------------------
# The predicate itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("parties", [
    [D, R], [R, D], [D, D, R], [D, R, IND], [D, R, None], [R, IND, D, None],
])
def test_is_cross_partisan_accepts(parties):
    assert r02.is_cross_partisan(parties) is True


@pytest.mark.parametrize("parties", [
    [], [D], [R], [D, D], [R, R], [IND], [None], [D, IND], [R, None],
    [IND, None], [D, D, D], ["Libertarian", "New Progressive"],
])
def test_is_cross_partisan_rejects(parties):
    assert r02.is_cross_partisan(parties) is False


def test_party_split_counts_only_the_two_major_parties():
    assert r02.party_split([D, D, R, IND, None, "Libertarian"]) == (2, 1)


def test_the_predicate_uses_the_roster_spelling_not_an_initial():
    """`members.party` stores 'Democratic'/'Republican', not 'D'/'R'."""
    assert r02.is_cross_partisan(["D", "R"]) is False
    assert r02.is_cross_partisan([D, R]) is True


# ---------------------------------------------------------------------------
# 🔴 The legacy-dedup regression the headline change would otherwise cause
# ---------------------------------------------------------------------------

def test_the_LEGACY_headline_is_kept_in_the_OLD_format():
    c = best(r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D, D2),
        txn("R1", "NVDA", "Purchase", R, D3),
    ], min_members=3))
    assert c["legacy_headline"] == "Cluster: 3 members bought NVDA within 7 days (NET_LONG)"
    assert c["legacy_headline"] != c["headline"]


def test_a_STORED_LEGACY_ROW_STILL_SUPPRESSES_ITS_CLUSTER():
    """⚠️ Without `legacy_headline` this re-emits the whole surviving corpus.

    Every stored RULE_02 alert predates the fingerprint, so `_prior_alerts`
    cannot see it and `legacy_alert_exists` — which matches on HEADLINE TEXT —
    is the only thing standing between a deploy and 54 duplicate alerts.
    """
    cluster = best(r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D, D2),
        txn("R1", "NVDA", "Purchase", R, D3),
    ], min_members=3))

    with db_connection() as conn:
        # A legacy row: OLD headline, no identity in why_matters.
        conn.execute(
            "INSERT INTO alerts (rule, ticker, headline, severity, tags) VALUES (?,?,?,?,?)",
            ("RULE_02", "NVDA", "Cluster: 3 members bought NVDA within 7 days (NET_LONG)",
             "HIGH", "a,b,c"),
        )
        conn.commit()
        before = conn.execute("SELECT COUNT(*) FROM alerts WHERE rule='RULE_02'").fetchone()[0]
        r02.emit_alerts(conn, [cluster], days=90)
        after = conn.execute("SELECT COUNT(*) FROM alerts WHERE rule='RULE_02'").fetchone()[0]

    assert after == before, "the legacy row must still suppress its own cluster"


def test_a_cross_partisan_cluster_with_NO_legacy_row_DOES_emit():
    """Control: the suppression above is the legacy row, not a broken emitter."""
    cluster = best(r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D, D2),
        txn("R1", "NVDA", "Purchase", R, D3),
    ], min_members=3))
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster], days=90)
        row = conn.execute(
            "SELECT headline FROM alerts WHERE rule='RULE_02'").fetchone()
    assert row is not None
    assert row[0].startswith("Cross-partisan cluster:")


# ---------------------------------------------------------------------------
# Identity is unchanged — party is derivable from the member set
# ---------------------------------------------------------------------------

def test_the_FINGERPRINT_does_not_mention_party():
    c = best(r02.find_clusters([
        txn("D1", "NVDA", "Purchase", D),
        txn("D2", "NVDA", "Purchase", D, D2),
        txn("R1", "NVDA", "Purchase", R, D3),
    ], min_members=3))
    assert c["fingerprint"] == r02._fingerprint(["D1", "D2", "R1"], "NVDA", "NET_LONG")
    for token in ("Democratic", "Republican", "D/", "/R"):
        assert token not in c["fingerprint"]
