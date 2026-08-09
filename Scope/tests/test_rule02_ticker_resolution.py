"""RULE_02 must key clusters on a RESOLVED symbol, never on the raw parse string.

The defect: `fetch_transactions` selected `raw_ticker_string AS ticker` and grouped
on it, with no `tickers` join and no `normalize_ticker`. Two consequences, both
measured on the working DB before the fix:

  * PHANTOM KEYS. `US` is not a symbol. 213 transactions carry it, all of them
    unlinked, and it produced alerts 66/67/68/69. A fifth phantom, `CA` (60 txns,
    all unlinked), produced alert 16. 5 of 82 stored RULE_02 alerts key on an
    unresolved symbol; 4 are HIGH. `_candidate_alerts` has no idea they are junk.
  * DISCARDED ROWS. `WHERE raw_ticker_string IS NOT NULL` threw away 30 rows that
    carried a `ticker_id` but no raw string.

The landing mirrors RULE_01B #4: resolved -> a corroboration key; unresolved -> NOT
a corroboration key but KEPT and flagged. Absence from `tickers` is a coverage gap,
not proof the symbol is fake — a listed company can be missing from the table.

⚠️ ONLY the raw string canonicalising into `tickers` confers a corroboration key.
`transactions.ticker_id` never does, because it is assigned by a company-NAME
matcher and is wrong often enough to be unusable for keying:

  With a raw string present, three of its groupings join distinct companies —
    IDEXX -> DLB   ("DC Laboratories, Inc." — IDEXX Labs is IDXX, not Dolby)
    MTRS  -> GIS   ("SP GENERAL FINL CO INC" — not General Mills)
    CNSWF -> STZ   ("CONSTELLATION SFTWRE" — not Constellation Brands)

  With NO raw string, **14 of the 30 recoverable rows are mis-linked** —
    ASCIX x13      `tickers` "Angel Oak Strategic Credit Fund"
                   vs filing "Oaktree Strategic Credit Fund"
    RBBN           `tickers` "Ribbon Communications"
                   vs filing "Verizon Communications"

An earlier draft of this fix treated that second group as resolved, reasoning that
an empty raw string leaves no competing signal so the link must be safe. That is
wrong, and the verifier caught it: no contradicting signal is not the same as no
contradiction — it only removes the means of detecting one. Those rows are now
recovered for visibility and flagged, never keyed.

So split variants are NOT merged, and nothing is keyed on a link. The tests below
pin both: distinct tickers never merge, and a mis-linked recovery never keys.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import rule_02_cluster as r02  # noqa: E402
from jpt_common import db_connection  # noqa: E402

D1 = "2026-06-01"
D2 = "2026-06-03"
D3 = "2026-06-05"

# A deliberately small resolution set: these are "in `tickers`", nothing else is.
VALID = {"NVDA", "WMT", "DIS", "BRK.B", "PLTR", "DLB", "GIS", "STZ", "IDXX"}


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


def txn(member, key, ttype, when=D1, name=None, resolved=True, party=None):
    return {
        "member_id": member,
        "ticker": key,
        "resolved": resolved,
        "transaction_type": ttype,
        "transaction_date": when,
        "full_name": name if name is not None else f"Member {member}",
        "party": party if party is not None else _auto_party(member),
    }


def best(clusters):
    assert clusters, "expected at least one cluster"
    return max(clusters, key=lambda c: c["member_count"])


# --------------------------------------------------------------------------
# resolve_key — the three-rung ladder
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,fk,expect_key,expect_resolved,why",
    [
        # rung 1 — the raw string canonicalises into `tickers`
        ("NVDA", None, "NVDA", True, "plain hit"),
        ("nvda", None, "NVDA", True, "case folded"),
        ("$NVDA", None, "NVDA", True, "dollar stripped"),
        ("BRK-B", None, "BRK.B", True, "dash canonicalised to dot"),
        ("BRK.B", None, "BRK.B", True, "already canonical"),
        # No raw string -> no key at all. The FK never participates: an FK-derived
        # key is in `tickers` by construction, so admitting one would collide with
        # the genuine cluster for that symbol and demote it.
        (None, "PLTR", "", False, "no raw string -> no key, even when linked"),
        ("", "PLTR", "", False, "empty raw is the same as absent"),
        (None, "ASCIX", "", False, "mis-linked row confers nothing"),
        # rung 2 — unresolved: kept as a group key, but not a corroboration key
        ("US", None, "US", False, "phantom"),
        ("CA", None, "CA", False, "phantom"),
        ("CORP", None, "CORP", False, "legal-form fragment"),
        ("SPCX", None, "SPCX", False, "REAL but missing from tickers"),
        # the FK is ignored outright — this is what keeps the mis-links apart
        ("IDEXX", "DLB", "IDEXX", False, "mis-link must not resolve to DLB"),
        ("MTRS", "GIS", "MTRS", False, "mis-link must not resolve to GIS"),
        ("CNSWF", "STZ", "CNSWF", False, "mis-link must not resolve to STZ"),
        # a present raw that DOES resolve wins on its own merit, not via the FK
        ("WMT", "WMT", "WMT", True, "agreeing FK is irrelevant"),
        ("CS", "WMT", "CS", False, "split variant stays unresolved, NOT merged"),
    ],
)
def test_resolve_key_ladder(raw, fk, expect_key, expect_resolved, why):
    assert r02.resolve_key(raw, fk, VALID) == (expect_key, expect_resolved), why


@pytest.mark.parametrize("raw", ["NVDA", "brk-b", "US", "CS", "IDEXX", "", None, "A B", "$wmt"])
@pytest.mark.parametrize("fk", [None, "PLTR", "DLB", "ASCIX"])
def test_resolution_is_a_pure_function_of_the_key(raw, fk):
    """`resolved is True` <=> `key in valid`, for every input shape.

    THE load-bearing invariant. Cluster resolution is read off the group's rows,
    which is only sound if resolution cannot vary within a group — and a group IS
    a key. An earlier draft violated this by returning an FK-derived key (in
    `tickers` by construction) with `resolved=False`, so a recovered row joined
    the genuine cluster for that symbol and `all()` demoted it. Real `MRK` and
    `PLTR` clusters lost their corroboration key that way.
    """
    key, resolved = r02.resolve_key(raw, fk, VALID)
    assert resolved == (key in VALID), (
        f"resolve_key({raw!r}, {fk!r}) -> ({key!r}, {resolved}) breaks the invariant"
    )


def test_the_foreign_key_is_ignored_entirely():
    """No `ticker_id` value may change the outcome for a given raw string."""
    for raw in ("NVDA", "US", "CS", "IDEXX", "SPCX", None, ""):
        outcomes = {r02.resolve_key(raw, fk, VALID)
                    for fk in (None, "PLTR", "DLB", "GIS", "STZ", "ASCIX", "NVDA")}
        assert len(outcomes) == 1, f"the FK changed the outcome for raw={raw!r}: {outcomes}"


# --------------------------------------------------------------------------
# The phantom stops being a corroboration key
# --------------------------------------------------------------------------

def test_a_phantom_cluster_is_still_emitted_but_carries_no_key():
    """`US` must not vanish — it must stop being a corroboration key."""
    rows = [txn(f"M{i:06d}", "US", "purchase", D1, resolved=False) for i in range(3)]
    c = best(r02.find_clusters(rows, min_members=3))
    assert c["resolved"] is False
    assert c["member_count"] == 3          # KEPT, not dropped
    assert "US" in c["headline"]           # the raw symbol stays visible

    with db_connection() as conn:
        assert r02.emit_alerts(conn, [c]) == 1
        row = conn.execute(
            "SELECT ticker, lifecycle_stage, why_matters FROM alerts WHERE rule='RULE_02'"
        ).fetchone()
    assert row["ticker"] == ""             # <- what actually removes it from the gate
    assert row["lifecycle_stage"] == "review"
    assert "UNVERIFIED->no corroboration" in row["why_matters"]
    assert "US" in row["why_matters"]      # preserved for triage


def test_the_blank_key_is_what_the_gate_filters_on():
    """`_candidate_alerts` requires `ticker != ''` — so '' genuinely leaves the gate.

    Pinned because it is the load-bearing difference from defect #1, where
    retraction was gate-COSMETIC: RULE_02 is unsigned, so `alert_corroborates`
    short-circuits True regardless of `lifecycle_stage`. Key removal bites;
    retraction does not.
    """
    with db_connection() as conn:
        r02.emit_alerts(conn, [{
            "resolved": False, "ticker": "US", "headline": "Cluster: 3 members bought US within 7 days (NET_LONG)",
            "severity": "HIGH", "tags": "A,B,C", "member_count": 3, "net_direction": "NET_LONG",
        }])
        # Ask the GATE, not a re-implementation of it. An earlier version of this
        # test asserted a phrase in `_candidate_alerts.__doc__` and then re-ran the
        # predicate inline — it passed with the gate's own filter deleted.
        import scripts.rule_10_corroboration as r10
        candidates = r10._candidate_alerts(conn, 72)
        tickers = [c["ticker"] for c in candidates]
    assert "US" not in tickers, "the phantom must not be a gate candidate"
    assert all(t for t in tickers), "no blank-ticker alert may be a candidate"


def test_a_resolved_cluster_is_unchanged():
    rows = [txn(f"M{i:06d}", "NVDA", "purchase", D1) for i in range(3)]
    c = best(r02.find_clusters(rows, min_members=3))
    assert c["resolved"] is True
    assert c["ticker"] == "NVDA"
    with db_connection() as conn:
        r02.emit_alerts(conn, [c])
        row = conn.execute("SELECT ticker, lifecycle_stage, why_matters FROM alerts").fetchone()
    assert (row["ticker"], row["lifecycle_stage"]) == ("NVDA", "created")
    # `why_matters` is no longer None: the identity/dedup fix appends the cluster
    # fingerprint here (rule_cluster's "Identity {fp}" convention). What must stay
    # absent is the UNRESOLVED flag — a resolved cluster carries no coverage caveat.
    assert r02.UNRESOLVED_FLAG not in (row["why_matters"] or "")


# --------------------------------------------------------------------------
# The SPCX lesson: missing from `tickers` is a coverage gap, not fakeness
# --------------------------------------------------------------------------

def test_a_real_symbol_absent_from_tickers_is_flagged_not_dropped_and_not_fuzzed():
    """SPCX is a listed company that simply is not in the table.

    It must be KEPT (the cluster still emits), FLAGGED (no corroboration key), and
    NOT fuzzy-resolved to some near neighbour. Dropping it would lose a real
    signal; fuzzing it would invent one.
    """
    key, resolved = r02.resolve_key("SPCX", None, VALID)
    assert (key, resolved) == ("SPCX", False)
    assert key not in VALID          # not silently snapped to anything
    rows = [txn(f"M{i:06d}", "SPCX", "purchase", D1, resolved=False) for i in range(3)]
    c = best(r02.find_clusters(rows, min_members=3))
    assert c["member_count"] == 3    # kept
    assert c["resolved"] is False    # flagged
    assert "SPCX" in c["headline"]   # recoverable by a human


def test_the_validity_set_canonicalises_the_tickers_side_too():
    """`tickers` stores 551 dash-form symbols and ZERO dot-form.

    So dropping `normalize_ticker` from `_validity_set` un-keys BRK.B, BF.B and
    HEI.A on real data — BRK.B is an actually-emitted cluster key. Canonicalising
    only the raw side is not enough, and the test set below is deliberately stored
    dash-form to prove the set does the work.
    """
    with db_connection() as conn:
        conn.execute("INSERT INTO tickers (id, symbol) VALUES (21, 'BRK-B'), (22, 'BF-B')")
        valid = r02._validity_set(conn)
    assert "BRK.B" in valid and "BF.B" in valid, "the tickers side must be canonicalised"
    assert r02.resolve_key("BRK.B", None, valid) == ("BRK.B", True)
    assert r02.resolve_key("BRK-B", None, valid) == ("BRK.B", True)


def test_multi_symbol_baskets_are_not_clustered():
    """A space-containing string is a basket, not a symbol. RULE_CLUSTER skips these too."""
    with db_connection() as conn:
        conn.execute("INSERT INTO tickers (id, symbol) VALUES (31, 'NVDA')")
        conn.execute("INSERT INTO members (bioguide_id, full_name) VALUES ('Z000001','Z')")
        for raw in ("NVDA AMD", "SPY QQQ", "NVDA"):
            conn.execute(
                "INSERT INTO transactions (member_id, ticker_id, raw_ticker_string, "
                "transaction_type, transaction_date) VALUES (?,?,?,?,date('now','-2 days'))",
                ("Z000001", None, raw, "purchase"),
            )
        keys = {r["ticker"] for r in r02.fetch_transactions(conn, 90)}
    assert keys == {"NVDA"}, f"baskets must not cluster, got {keys}"


def test_dedup_is_keyed_on_the_STORED_ticker_not_the_group_key():
    """`alert_exists` must be asked about what will actually be written.

    Checking the group key instead would look up `US` while storing `''`, so an
    already-stored unresolved alert would never suppress a re-emission.
    """
    cluster = {"resolved": False, "ticker": "US", "severity": "HIGH", "tags": "A,B,C",
               "headline": "Cluster: 3 members bought US within 7 days (NET_LONG)",
               "member_count": 3, "net_direction": "NET_LONG"}
    with db_connection() as conn:
        assert r02.emit_alerts(conn, [cluster]) == 1
        assert r02.emit_alerts(conn, [cluster]) == 0, "second emit must be suppressed"
        n = conn.execute("SELECT COUNT(*) FROM alerts WHERE rule='RULE_02'").fetchone()[0]
    assert n == 1


# --------------------------------------------------------------------------
# The ONLY merge: canonicalisation of the same symbol
# --------------------------------------------------------------------------

def test_dash_and_dot_variants_are_one_cluster():
    """BRK-B and BRK.B are the same symbol; this is canonicalisation, not resolution."""
    assert r02.resolve_key("BRK-B", None, VALID)[0] == r02.resolve_key("BRK.B", None, VALID)[0]
    rows = [
        txn("M000001", r02.resolve_key("BRK-B", None, VALID)[0], "purchase", D1),
        txn("M000002", r02.resolve_key("BRK.B", None, VALID)[0], "purchase", D2),
        txn("M000003", r02.resolve_key("brk.b", None, VALID)[0], "purchase", D3),
    ]
    c = best(r02.find_clusters(rows, min_members=3))
    assert c["ticker"] == "BRK.B"
    assert c["member_count"] == 3, "the three variants must be ONE cluster of three"


# --------------------------------------------------------------------------
# Distinct tickers must NEVER merge — the three known mis-links
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,fk_symbol,description",
    [
        ("IDEXX", "DLB", "IDEXX Laboratories is IDXX, not Dolby"),
        ("MTRS", "GIS", "GENERAL FINL CO is not General Mills"),
        ("CNSWF", "STZ", "Constellation SOFTWARE is not Constellation BRANDS"),
    ],
)
def test_the_known_mislinks_do_not_merge(raw, fk_symbol, description):
    """The ingestion linker matched these on company name. They are different firms.

    Confining the `ticker_id` rung to rows with NO raw string is what makes this
    structural rather than a special case: each raw variant is absent from
    `tickers`, so it lands on the unresolved rung and is flagged.
    """
    key, resolved = r02.resolve_key(raw, fk_symbol, VALID)
    assert key != fk_symbol, description
    assert resolved is False


def test_a_mislinked_row_does_not_join_the_other_companys_cluster():
    """End-to-end: two DLB buyers plus one IDEXX buyer is NOT a 3-member DLB cluster."""
    rows = [
        txn("M000001", "DLB", "purchase", D1),
        txn("M000002", "DLB", "purchase", D2),
        txn("M000003", r02.resolve_key("IDEXX", "DLB", VALID)[0], "purchase", D3, resolved=False),
    ]
    clusters = r02.find_clusters(rows, min_members=3)
    assert clusters == [], "IDEXX must not complete a DLB cluster"
    dlb = r02.find_clusters(rows, min_members=2)
    assert {c["ticker"] for c in dlb} == {"DLB"}
    assert best(dlb)["member_count"] == 2


# --------------------------------------------------------------------------
# End-to-end through fetch_transactions, against a real schema
# --------------------------------------------------------------------------

#: Alternating parties. RULE_02's cross-partisan gate means a cluster only forms
#: when both major parties are present, and a real member always HAS a party —
#: a partyless seed row is the unrealistic case. These tests are about the KEY;
#: the gate itself is covered in test_rule02_cross_partisan.py.
_SEED_PARTIES = ("Democratic", "Republican", "Democratic")


def _seed(conn):
    conn.execute("INSERT INTO tickers (id, symbol) VALUES (1, 'PLTR'), (2, 'NVDA'), (3, 'DLB')")
    for i in range(3):
        conn.execute("INSERT INTO members (bioguide_id, full_name, party) VALUES (?,?,?)",
                     (f"M00000{i}", f"Member {i}", _SEED_PARTIES[i]))


def test_rows_with_a_ticker_id_but_no_raw_string_stay_excluded():
    """These 30 rows are a COVERAGE problem, not a keying problem — scoped out.

    An earlier draft admitted them as unresolved "recoveries". That was worse than
    leaving them out: an FK-derived key comes from `tickers.symbol` and so is in
    the validity set BY CONSTRUCTION, which meant a recovered row joined the
    genuine cluster for that symbol and dragged it to unresolved. Real `MRK` and
    `PLTR` clusters lost their corroboration key and were labelled
    "symbol not in `tickers`" — about symbols that are in `tickers`.

    14 of the 30 are mis-linked anyway. They belong to the ingestion linker.
    """
    with db_connection() as conn:
        _seed(conn)
        for i in range(3):
            conn.execute(
                "INSERT INTO transactions (member_id, ticker_id, raw_ticker_string, "
                "transaction_type, transaction_date) VALUES (?,?,?,?,date('now','-2 days'))",
                (f"M00000{i}", 1, None, "purchase"),
            )
        rows = r02.fetch_transactions(conn, 90)
    assert rows == [], "a row with no raw string confers no key and must not cluster"


def test_a_genuine_cluster_keeps_its_key_when_a_linked_row_shares_the_symbol():
    """The regression the verifier found, pinned.

    Three real PLTR traders (raw='PLTR', resolves) plus one row linked to PLTR with
    no raw string. The genuine cluster must keep `ticker='PLTR'`; the linked row
    must not demote it.
    """
    with db_connection() as conn:
        _seed(conn)
        for i in range(3):
            conn.execute(
                "INSERT INTO transactions (member_id, ticker_id, raw_ticker_string, "
                "transaction_type, transaction_date) VALUES (?,?,?,?,date('now','-2 days'))",
                (f"M00000{i}", 1, "PLTR", "purchase"),
            )
        conn.execute("INSERT INTO members (bioguide_id, full_name, party) VALUES ('Q000001','Q','Republican')")
        conn.execute(
            "INSERT INTO transactions (member_id, ticker_id, raw_ticker_string, "
            "transaction_type, transaction_date) VALUES (?,?,?,?,date('now','-2 days'))",
            ("Q000001", 1, None, "purchase"),
        )
        clusters = r02.find_clusters(r02.fetch_transactions(conn, 90), min_members=3)
        assert len(clusters) == 1
        assert clusters[0]["resolved"] is True
        r02.emit_alerts(conn, clusters)
        row = conn.execute("SELECT ticker, lifecycle_stage, why_matters FROM alerts "
                           "WHERE rule='RULE_02'").fetchone()
    assert row["ticker"] == "PLTR", "a resolving cluster must keep its corroboration key"
    assert row["lifecycle_stage"] == "created"
    # Identity now lives in why_matters; only the UNRESOLVED claim must be absent.
    assert r02.UNRESOLVED_FLAG not in (row["why_matters"] or ""),         "must not claim PLTR is absent from `tickers`"


def test_two_distinct_companies_can_share_an_UNKEYED_cluster_KNOWN_RESIDUAL():
    """`raw='CS'` exists under BOTH "Walmart Inc." and "The Walt Disney Company".

    So an unresolved group conflates them into one "N members bought CS" alert.
    That is a defect of the parse string and is NOT fixed here — but it is strictly
    better than before, when the same cluster carried `ticker='CS'` and could
    corroborate. This test pins the improvement (no key) and documents the residual
    (still one conflated alert). It SHOULD fail when the parser is fixed.
    """
    with db_connection() as conn:
        conn.execute("INSERT INTO tickers (id, symbol) VALUES (41,'WMT'), (42,'DIS')")
        for i, fk in enumerate((41, 41, 42)):
            conn.execute("INSERT INTO members (bioguide_id, full_name, party) VALUES (?,?,?)",
                         (f"C00000{i}", f"Member {i}", _SEED_PARTIES[i]))
            conn.execute(
                "INSERT INTO transactions (member_id, ticker_id, raw_ticker_string, "
                "transaction_type, transaction_date) VALUES (?,?,?,?,date('now','-2 days'))",
                (f"C00000{i}", fk, "CS", "purchase"),
            )
        clusters = r02.find_clusters(r02.fetch_transactions(conn, 90), min_members=3)
        assert len(clusters) == 1 and clusters[0]["resolved"] is False
        r02.emit_alerts(conn, clusters)
        row = conn.execute("SELECT ticker FROM alerts WHERE rule='RULE_02'").fetchone()
    assert row["ticker"] == "", "conflated companies must at least not reach the gate"


def test_fetch_resolves_and_flags_in_one_pass():
    with db_connection() as conn:
        _seed(conn)
        rows_in = [("M000000", 2, "NVDA"), ("M000001", None, "US"), ("M000002", 3, "IDEXX")]
        for mid, tid, raw in rows_in:
            conn.execute(
                "INSERT INTO transactions (member_id, ticker_id, raw_ticker_string, "
                "transaction_type, transaction_date) VALUES (?,?,?,?,date('now','-2 days'))",
                (mid, tid, raw, "purchase"),
            )
        got = {r["ticker"]: r["resolved"] for r in r02.fetch_transactions(conn, 90)}
    assert got == {"NVDA": True, "US": False, "IDEXX": False}, \
        "IDEXX must stay IDEXX/unresolved even though its row is linked to DLB"


def test_the_ASCIX_mislink_never_reaches_clustering_at_all():
    """`tickers` says "Angel Oak Strategic Credit Fund"; the filing says "Oaktree".

    13 real rows. They carry a `ticker_id` and no raw string, so under the current
    contract they are excluded outright — the strongest possible guarantee that a
    mis-link cannot key, demote, or conflate anything.
    """
    with db_connection() as conn:
        conn.execute("INSERT INTO tickers (id, symbol, company_name) "
                     "VALUES (9, 'ASCIX', 'Angel Oak Strategic Credit Fund')")
        for i in range(3):
            conn.execute("INSERT INTO members (bioguide_id, full_name, party) VALUES (?,?,?)",
                         (f"X00000{i}", f"Member {i}", _SEED_PARTIES[i]))
            conn.execute(
                "INSERT INTO transactions (member_id, ticker_id, raw_ticker_string, "
                "raw_description, transaction_type, transaction_date) "
                "VALUES (?,?,?,?,?,date('now','-2 days'))",
                (f"X00000{i}", 9, None, "SP Oaktree Strategic Credit Fund Class", "purchase"),
            )
        rows = r02.fetch_transactions(conn, 90)
        assert rows == []
        assert r02.find_clusters(rows, min_members=3) == []
        assert conn.execute("SELECT COUNT(*) FROM alerts WHERE rule='RULE_02'").fetchone()[0] == 0
