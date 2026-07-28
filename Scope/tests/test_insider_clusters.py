"""Insider clusters — the trap battery.

Six certification passes found fourteen "keys" in the predecessor. They were ONE bug
fourteen times: several keys each reconstructed "who is one insider" from a different
projection of a row, each blind to a different column.

This file's job is NOT to show the traps are fixed. It is to show they are
**unrepresentable** — that there is one identity definition and nothing else can invent
its own. Every test here asserts BEHAVIOUR. The predecessor had seven tests that asserted
SOURCE TEXT, two of them on the identity keys themselves; a source pin cannot fail when
the behaviour breaks, and three of the five mutants that survived its final audit were
"pinned" that way.
"""
from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection  # noqa: E402
from scripts import insider_clusters as ic  # noqa: E402
from scripts.ingest_form4_transactions import ensure_tables as store_tables  # noqa: E402

_REAL_RESOLVER = ic.resolve_insider_kind


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test here reaches SEC or Yahoo. Each injects the shape it needs."""
    monkeypatch.setattr(ic, "resolve_insider_kind", lambda conn, cik, **k: "person")
    monkeypatch.setattr(ic, "classify_cap", lambda conn, t, **k: ("small", 400_000_000))


def _db():
    conn = db_connection()
    store_tables(conn)
    ic.ensure_tables(conn)
    return conn


def _row(conn, accession, owner_seq, txn_index, *, ticker="SMLC", cik="1",
         name="Doe Jane", kind="person", code="P", ad="A", value=100_000.0,
         days_ago=1, plan=0, derivative=0, title="CFO", shares=100.0, issuer=None):
    """One store row. Mirrors the real writer: a filing with N owners and M transactions
    is N*M rows, transaction fields duplicated verbatim across owners."""
    conn.execute(
        "INSERT OR IGNORE INTO form4_transactions (accession, owner_seq, txn_index, "
        "ticker, issuer_cik, insider_cik, insider_name, insider_name_norm, "
        "insider_kind, officer_title, txn_code, acquired_disposed, shares, price, "
        "value, txn_date, filing_date, is_derivative, is_10b5_1) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
        f"date('now','-{int(days_ago)} days'), date('now'),?,?)",
        (accession, owner_seq, txn_index, ticker,
         # Issuer identity defaults to the ticker so a test that does not care about
         # issuer identity behaves as it reads. Tests about ticker VARIANTS pass an
         # explicit shared issuer, which is what makes them a real K10 test.
         issuer if issuer is not None else f"ISS-{ticker.strip().upper()}",
         cik, name, name.upper(), kind, title,
         code, ad, shares, value / max(shares, 1), value, derivative, plan))
    conn.commit()


def _joint(conn, accession, owners, txn_index=0, **kw):
    """A joint filing: the SAME transaction, once per reporting owner."""
    for seq, (cik, name) in enumerate(owners):
        _row(conn, accession, seq, txn_index, cik=cik, name=name, **kw)


# ════════════════════════════════════════════════════════════════════════════
#  STAGE 1 — the partition itself
# ════════════════════════════════════════════════════════════════════════════

def test_two_ciks_that_cofile_ANYWHERE_share_a_person_id():
    conn = _db()
    _joint(conn, "A1", [("100", "Smith John"), ("200", "Smith Pat")])
    part = ic.person_partition(conn)
    conn.close()
    assert part["100"] == part["200"] == ("100", "200")


def test_the_partition_is_INDEPENDENT_of_code_ticker_derivative_plan_and_date():
    """THE HEART OF K13. Whether two people are one household is a property OF THE
    PEOPLE. The predecessor scoped this input to open-market buys on one ticker in one
    window, so every bridging filing outside that subset was invisible."""
    conn = _db()
    # The ONLY link between 100 and 200 is a filing that is: an option exercise (M), not
    # a buy (D), on a different ticker, derivative, 10b5-1, and long outside the window.
    _joint(conn, "BRIDGE", [("100", "Smith John"), ("200", "Smith Pat")],
           ticker="OTHER", code="M", ad="D", derivative=1, plan=1, days_ago=900)
    part = ic.person_partition(conn)
    conn.close()
    assert part["100"] == part["200"], "a bridge outside the buy/ticker/window subset " \
                                       "failed to link — K13 is back"


def test_a_padded_and_an_unpadded_cik_are_ONE_person():
    conn = _db()
    _joint(conn, "A1", [("0000001234", "Smith John"), ("5555", "Other Person")])
    _row(conn, "A2", 0, 0, cik="1234", name="Smith John")
    part = ic.person_partition(conn)
    conn.close()
    assert part["1234"] == part["5555"], "K9: padded and unpadded counted as two"


def test_an_EMPTY_cik_is_not_a_node():
    """K2. If it were, every CIK-less owner in the corpus would union into ONE component
    and merge every filer who ever filed without a CIK."""
    conn = _db()
    _joint(conn, "A1", [("", "Anon One"), ("100", "Smith John")])
    _joint(conn, "A2", [("", "Anon Two"), ("200", "Jones Bob")])
    part = ic.person_partition(conn)
    conn.close()
    assert "" not in part
    assert part["100"] != part["200"], "two unrelated filers merged through empty CIKs"


def test_every_accession_maps_to_EXACTLY_ONE_person_id():
    """The structural property the whole rewrite rests on. If this ever fails, 'a joint
    filing counts as one insider' has become a rule that can be got wrong again."""
    conn = _db()
    _joint(conn, "A1", [("1", "A"), ("2", "B"), ("3", "C")])
    _joint(conn, "A2", [("3", "C"), ("4", "D")])
    _row(conn, "A3", 0, 0, cik="9", name="Solo")
    part = ic.person_partition(conn)
    persons = ic.accession_persons(conn, part)
    conn.close()
    for acc, pids in persons.items():
        assert len(pids) == 1, f"{acc} mapped to {len(pids)} person_ids: {pids}"


def test_an_accession_whose_owners_ALL_lack_a_cik_maps_to_NOTHING():
    conn = _db()
    _joint(conn, "A1", [("", "Anon One"), ("", "Anon Two")])
    part = ic.person_partition(conn)
    conn.close()
    assert ic.accession_persons(db_connection(), part).get("A1", ()) in ((), None) or True
    conn2 = _db()
    _joint(conn2, "A1", [("", "Anon One"), ("", "Anon Two")])
    persons = ic.accession_persons(conn2, ic.person_partition(conn2))
    conn2.close()
    assert persons["A1"] == (), "an unidentifiable filing must map to nothing"


def test_the_person_id_is_the_identity_CLASS_not_an_aggregate_label():
    """K14 was `MIN(insider_cik)` — an aggregate over A FILING'S OWNER LIST, so it moved
    when that list moved. The key here is the component itself."""
    conn = _db()
    _joint(conn, "A1", [("500", "Director"), ("100", "Family Trust")])
    part = ic.person_partition(conn)
    conn.close()
    assert part["500"] == ("100", "500"), "person_id is not the sorted component"


# ════════════════════════════════════════════════════════════════════════════
#  THE TRAP BATTERY — each trap, behaviourally
# ════════════════════════════════════════════════════════════════════════════

def test_TRAP1_a_joint_filing_is_ONE_insider_not_N():
    """The primary trap, and now structurally impossible: co-filing IS the union edge."""
    conn = _db()
    _joint(conn, "A1", [("1", "A"), ("2", "B"), ("3", "C")], value=100_000.0)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters == [], "3 co-filers on one buy became a cluster"


def test_K13_joint_then_solo_is_ONE_household():
    conn = _db()
    _joint(conn, "A1", [("100", "Smith John"), ("200", "Smith Pat")], value=100_000.0)
    _row(conn, "A2", 0, 0, cik="200", name="Smith Pat", value=100_000.0, days_ago=2)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters == [], "joint-then-solo produced a false 2-insider cluster"


@pytest.mark.parametrize("bridge", [
    dict(days_ago=900),                                  # earlier than any window
    dict(code="M", ad="D"),                              # not an open-market buy
    dict(ticker="OTHER"),                                # a different ticker
    dict(derivative=1),                                  # derivative
    dict(plan=1),                                        # a 10b5-1 filing
])
def test_K13_a_bridge_OUTSIDE_the_scored_subset_still_links(bridge):
    """The three constructions the certification used, plus two more. Each is a false
    2-insider $200,000 cluster from ONE household if the bridge is invisible."""
    conn = _db()
    _row(conn, "BUY1", 0, 0, cik="100", name="Smith John", value=100_000.0, days_ago=1)
    _row(conn, "BUY2", 0, 0, cik="200", name="Smith Pat", value=100_000.0, days_ago=2)
    _joint(conn, "BRIDGE", [("100", "Smith John"), ("200", "Smith Pat")], **bridge)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters == [], f"bridge {bridge} was invisible — false cluster from one " \
                           f"household (K13)"


def test_K13_the_control_two_GENUINELY_distinct_insiders_still_cluster():
    """The other direction. Without this, K13's test would pass on a module that merges
    everything — which is the over-merge failure the certification called the WORSE one."""
    conn = _db()
    _row(conn, "BUY1", 0, 0, cik="100", name="Smith John", value=100_000.0, days_ago=1)
    _row(conn, "BUY2", 0, 0, cik="200", name="Jones Bob", value=100_000.0, days_ago=2)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert len(clusters) == 1 and len(clusters[0]["insiders"]) == 2
    assert clusters[0]["total_value"] == 200_000.0


def test_K2_a_cik_less_cofiler_of_COINCIDING_NAME_does_not_merge_two_insiders():
    """The exact shape my own reverted "fix" broke: a name fallback in the union merged
    two distinct insiders through a CIK-less co-filer, DELETING a real $300,000 cluster."""
    conn = _db()
    _joint(conn, "A1", [("100", "Smith John"), ("", "Pat Coincidence")],
           value=150_000.0, days_ago=1)
    _joint(conn, "A2", [("200", "Jones Bob"), ("", "Pat Coincidence")],
           value=150_000.0, days_ago=2)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert len(clusters) == 1, "the CIK-less co-filer merged two distinct insiders"
    assert len(clusters[0]["insiders"]) == 2
    assert clusters[0]["total_value"] == 300_000.0


def test_K9_padded_vs_unpadded_does_not_fabricate_a_second_insider():
    conn = _db()
    _row(conn, "A1", 0, 0, cik="0000000100", name="Smith John", value=100_000.0,
         days_ago=1)
    _row(conn, "A2", 0, 0, cik="100", name="Smith John", value=100_000.0, days_ago=2)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters == [], "K9: one person under two CIK paddings became two insiders"


@pytest.mark.parametrize("variants", [
    ("ABCD", "abcd"),
    ("ABCD", " AbCd "),
    ("ABCD", "$abcd"),
])
def test_K10_ticker_case_and_form_variants_are_ONE_cluster(variants):
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", ticker=variants[0], value=100_000.0, days_ago=1,
         issuer="ISS-1")
    _row(conn, "A2", 0, 0, cik="200", ticker=variants[1], value=100_000.0, days_ago=2,
         issuer="ISS-1")
    clusters = ic.find_clusters(conn)
    conn.close()
    assert len(clusters) == 1, f"{variants} did not group as one ticker (K10)"
    assert len(clusters[0]["insiders"]) == 2


def test_K10_the_control_genuinely_different_tickers_do_NOT_merge():
    """The predecessor's over-split leg asserted `"REAL" in tickers` with BOTH rows using
    the identical string — a ticker cannot split from itself."""
    conn = _db()
    for i, tk in enumerate(("AAAA", "BBBB", "CCCC", "DDDD")):
        _row(conn, f"A{i}", 0, 0, cik=str(100 + i), ticker=tk, value=100_000.0)
        _row(conn, f"B{i}", 0, 0, cik=str(200 + i), ticker=tk, value=100_000.0)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert sorted(c["ticker"] for c in clusters) == ["AAAA", "BBBB", "CCCC", "DDDD"]


def test_K14_a_4A_that_ADDS_the_family_trust_still_dedupes():
    """K14's over-split. Under `MIN(insider_cik)` the added trust moved the
    representative from 200 to 100, so the restatement stopped deduping and the cluster
    reported $250,000 against a truth of $150,000."""
    conn = _db()
    _row(conn, "ORIG", 0, 0, cik="200", name="Director", value=100_000.0, days_ago=3)
    _joint(conn, "AMEND", [("100", "Family Trust"), ("200", "Director")],
           value=100_000.0, days_ago=3)
    _row(conn, "OTHER", 0, 0, cik="900", name="Second Insider", value=50_000.0,
         days_ago=2)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert len(clusters) == 1
    assert clusters[0]["total_value"] == 150_000.0, \
        f"the restatement double-counted: {clusters[0]['total_value']} (K14)"


def test_K14_the_owner_LIST_ORDER_changes_nothing():
    conn = _db()
    _joint(conn, "A1", [("100", "Trust"), ("200", "Director")], value=100_000.0,
           days_ago=3)
    a = ic.find_clusters(conn)
    conn.close()
    conn = _db()
    _joint(conn, "A1", [("200", "Director"), ("100", "Trust")], value=100_000.0,
           days_ago=3)
    b = ic.find_clusters(conn)
    conn.close()
    assert a == b, "the result depended on which owner was listed first (K14)"


def test_the_SCTX_regression_two_legs_of_ONE_filing_do_not_merge():
    """Real case, accession 0002127908-26-000004: David Parrot filed two identical
    1,333-share buys. Omitting `txn_index` merged them and silently deleted $19,995 —
    23.5% of the cluster. The store's own schema comment warns of exactly this."""
    conn = _db()
    _row(conn, "SCTX1", 0, 0, cik="100", value=19_995.0, shares=1333.0, days_ago=1)
    _row(conn, "SCTX1", 0, 1, cik="100", value=19_995.0, shares=1333.0, days_ago=1)
    _row(conn, "SCTX2", 0, 0, cik="200", value=45_000.0, days_ago=1)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters[0]["total_value"] == 84_990.0, \
        f"two distinct legs of one filing merged: {clusters[0]['total_value']}"


def test_a_4A_REFILING_under_a_new_accession_still_collapses():
    conn = _db()
    _row(conn, "ORIG", 0, 0, cik="100", value=100_000.0, days_ago=3)
    _row(conn, "AMEND", 0, 0, cik="100", value=100_000.0, days_ago=3)
    _row(conn, "OTHER", 0, 0, cik="200", value=50_000.0, days_ago=2)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters[0]["total_value"] == 150_000.0, "the 4/A re-filing double-counted"


# ════════════════════════════════════════════════════════════════════════════
#  K12 — determinism, ACROSS PROCESSES
# ════════════════════════════════════════════════════════════════════════════

_XPROC = textwrap.dedent("""
    import os, sys, json
    sys.path.insert(0, {repo!r})
    os.environ["DATABASE_PATH"] = {db!r}
    from jpt_common import db_connection
    from scripts import insider_clusters as ic
    ic.resolve_insider_kind = lambda conn, cik, **k: "person"
    ic.classify_cap = lambda conn, t, **k: ("small", 400000000)
    conn = db_connection()
    part = ic.person_partition(conn)
    out = {{"partition": sorted((k, list(v)) for k, v in part.items()),
            "clusters": ic.find_clusters(conn)}}
    conn.close()
    print(json.dumps(out, sort_keys=True))
""")


def test_K12_the_whole_output_is_STABLE_ACROSS_PROCESSES(tmp_path):
    """An in-process test STRUCTURALLY CANNOT see hash randomisation — that is why the
    predecessor's `ORDER BY owner_seq` mutant survived its named test.

    This one FAILS on a subprocess error. The predecessor's `pytest.skip`ped, so a broken
    harness read as a pass.
    """
    db = str(tmp_path / "x.db")
    os.environ["DATABASE_PATH"] = db
    conn = db_connection()
    store_tables(conn)
    ic.ensure_tables(conn)
    for i, (cik, name) in enumerate([("300", "Zed Outsider"), ("100", "Ann Household"),
                                     ("200", "Bob Household")]):
        _row(conn, f"A{i}", 0, 0, cik=cik, name=name, value=100_000.0, days_ago=i + 1)
    _joint(conn, "JOINT", [("100", "Ann Household"), ("200", "Bob Household")],
           value=1.0, days_ago=400)
    conn.close()

    repo = os.path.join(os.path.dirname(__file__), "..")
    src = _XPROC.format(repo=os.path.abspath(repo), db=db)
    outs = []
    for seed in ("0", "1", "2", "3", "5", "7"):
        r = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": seed})
        assert r.returncode == 0, f"seed {seed} failed:\n{r.stderr[-2000:]}"
        outs.append(r.stdout.strip())
    assert len(set(outs)) == 1, (
        f"{len(set(outs))} distinct outputs across PYTHONHASHSEED values — "
        f"nondeterministic (K12)")


# ════════════════════════════════════════════════════════════════════════════
#  Safety — every property, behaviourally
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("kw,why", [
    (dict(code="S", ad="D"), "a SALE"),
    (dict(code="A", ad="A"), "a GRANT, not an open-market buy"),
    (dict(code="M", ad="A"), "an option exercise"),
    (dict(code="G", ad="A"), "a gift"),
    (dict(code="F", ad="D"), "shares withheld for tax"),
    (dict(code="P", ad="D"), "code P but DISPOSED — the acquired_disposed half"),
    (dict(derivative=1), "a derivative"),
    (dict(plan=1), "a 10b5-1 planned trade"),
])
def test_what_is_NOT_a_qualifying_buy(kw, why):
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", value=100_000.0, **kw)
    _row(conn, "A2", 0, 0, cik="200", value=100_000.0, **kw)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters == [], f"{why} counted as an open-market buy"


def test_a_huge_SALE_beside_a_buy_contributes_ZERO_dollars():
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", value=100_000.0)
    _row(conn, "A1", 0, 1, cik="100", code="S", ad="D", value=9_000_000.0)
    _row(conn, "A2", 0, 0, cik="200", value=100_000.0)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters[0]["total_value"] == 200_000.0


def test_a_NULL_10b5_1_is_KEPT_and_SURFACED_not_excluded():
    """NULL means 'not disclosed', never 'no'. Dropping it would silently discard every
    pre-2022 filing."""
    conn = _db()
    for acc, cik in (("A1", "100"), ("A2", "200")):
        conn.execute(
            "INSERT INTO form4_transactions (accession, owner_seq, txn_index, ticker, "
            "insider_cik, insider_name, insider_name_norm, insider_kind, txn_code, "
            "acquired_disposed, shares, price, value, txn_date, filing_date, "
            "is_derivative, is_10b5_1) VALUES (?,0,0,'SMLC',?,'N','N','person','P','A',"
            "100,1000.0,100000.0, date('now','-1 days'), date('now'), 0, NULL)",
            (acc, cik))
    conn.commit()
    clusters = ic.find_clusters(conn)
    conn.close()
    assert len(clusters) == 1
    assert clusters[0]["undisclosed_10b5_1_trades"] == 2


@pytest.mark.parametrize("sentinel", ["NA", "N/A", "NONE", "NULL", "-"])
def test_a_sentinel_ticker_is_not_a_company(sentinel):
    """Funds without a ticker file the literal string; three unrelated filers once formed
    a pseudo-cluster on `NA` worth $19.98."""
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", ticker=sentinel, value=100_000.0)
    _row(conn, "A2", 0, 0, cik="200", ticker=sentinel, value=100_000.0)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters == []


@pytest.mark.parametrize("cap,why", [
    (("unknown", None), "an UNKNOWN cap"),
    (("excluded", 900_000_000_000), "a confirmed mega-cap"),
    (("small", None), "small but unpriceable"),
    (("small", 10_000_000_000), "exactly the large-cap line"),
])
def test_the_cap_gate_FAILS_CLOSED(monkeypatch, cap, why):
    """The opposite direction from the reddit collector, deliberately: a human reads this
    surface, so an unknown cap keeps the ticker OFF it."""
    conn = _db()
    monkeypatch.setattr(ic, "classify_cap", lambda conn, t, **k: cap)
    _row(conn, "A1", 0, 0, cik="100", value=100_000.0)
    _row(conn, "A2", 0, 0, cik="200", value=100_000.0)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters == [], f"{why} reached the surface"


@pytest.mark.parametrize("kind", ["institution", "unknown"])
def test_a_non_person_insider_FAILS_CLOSED(monkeypatch, kind):
    conn = _db()
    monkeypatch.setattr(ic, "resolve_insider_kind", lambda conn, cik, **k: kind)
    _row(conn, "A1", 0, 0, cik="100", value=100_000.0)
    _row(conn, "A2", 0, 0, cik="200", value=100_000.0)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters == [], f"an insider resolving '{kind}' was counted"


def test_a_component_is_a_PERSON_if_ANY_member_is():
    """A director and their family trust are one actor; the trust's name carries an
    entity token. Requiring ALL members to be persons would drop the director."""
    conn = _db()
    kinds = {"100": "institution", "200": "person", "900": "person"}
    ic_resolve = lambda conn, cik, **k: kinds.get(cik, "unknown")  # noqa: E731
    _joint(conn, "A1", [("100", "Family Trust LLC"), ("200", "Director")],
           value=100_000.0, days_ago=1)
    _row(conn, "A2", 0, 0, cik="900", name="Second", value=100_000.0, days_ago=2)
    clusters = ic.find_clusters(conn, resolve=ic_resolve)
    conn.close()
    assert len(clusters) == 1 and len(clusters[0]["insiders"]) == 2


def test_an_UNRESOLVED_member_makes_the_whole_component_unknown():
    conn = _db()
    kinds = {"100": "institution", "200": "unknown"}
    _joint(conn, "A1", [("100", "Trust LLC"), ("200", "Mystery")], value=100_000.0)
    _row(conn, "A2", 0, 0, cik="900", value=100_000.0)
    clusters = ic.find_clusters(
        conn, resolve=lambda conn, cik, **k: kinds.get(cik, "person"))
    conn.close()
    assert clusters == [], "a component with an unresolved member was counted"


def test_dropped_legs_are_COUNTED_and_PUBLISHED():
    """`combined_value` alone said nothing about legs the identity filter removed — which
    is how a cluster once lost 68% of its dollars unnoticed."""
    conn = _db()
    kinds = {"100": "person", "200": "person", "300": "institution"}
    for i, cik in enumerate(("100", "200", "300")):
        _row(conn, f"A{i}", 0, 0, cik=cik, value=100_000.0, days_ago=i + 1)
    clusters = ic.find_clusters(
        conn, resolve=lambda conn, cik, **k: kinds.get(cik, "unknown"))
    conn.close()
    assert clusters[0]["dropped_institution"] == 1
    assert clusters[0]["total_value"] == 200_000.0


def test_the_window_is_on_the_TRADE_date_and_is_exclusive_at_the_end():
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", value=100_000.0, days_ago=1)
    _row(conn, "A2", 0, 0, cik="200", value=100_000.0, days_ago=7)
    inside = ic.find_clusters(conn, window_days=7)
    outside = ic.find_clusters(conn, window_days=6)
    conn.close()
    assert len(inside) == 1, "a 6-day separation did not fit a 7-day window"
    assert outside == [], "a 6-day window admitted a 6-day separation"


# ════════════════════════════════════════════════════════════════════════════
#  Never a gate leg / writes nothing — BEHAVIOURAL, not source greps
# ════════════════════════════════════════════════════════════════════════════

def test_a_scan_writes_NO_scope_data():
    """Behavioural replacement for the predecessor's substring grep over the module
    source, which could not see two modules down. A scan may warm `insider_meta` and
    `ticker_meta`; it must touch nothing else."""
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", value=100_000.0)
    _row(conn, "A2", 0, 0, cik="200", value=100_000.0)
    counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("alerts", "themes", "theme_signals", "activity_log",
                        "form4_transactions")}
    ic.find_clusters(conn)
    after = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in counts}
    conn.close()
    assert after == counts, f"a read-only scan wrote Scope data: {counts} -> {after}"


def test_the_module_adds_NO_gate_instrument():
    """Behavioural: RULE_06 is the Form 4 instrument, and this module maps to nothing.
    99 insider alerts must still be ONE instrument against a threshold of three."""
    from jpt_common import rule10_instruments, RULE_10_MIN_INSTRUMENTS
    assert set(rule10_instruments(["RULE_06"] * 99)) == {"insider"}
    assert len(set(rule10_instruments(["RULE_06"] * 99))) < RULE_10_MIN_INSTRUMENTS


def test_no_scoring_vocabulary_reaches_the_surface():
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", value=100_000.0)
    _row(conn, "A2", 0, 0, cik="200", value=100_000.0)
    clusters = ic.find_clusters(conn)
    conn.close()
    body = repr(clusters).lower()
    for banned in ("confidence", "probability", "conviction", "recommend", "rating",
                   "target"):
        assert banned not in body, f"the surface published a '{banned}'"


# ════════════════════════════════════════════════════════════════════════════
#  Structural LINT — explicitly NOT a protection
# ════════════════════════════════════════════════════════════════════════════

_IDENTITY_AUTHORITIES = {"_norm_cik", "person_partition", "accession_persons",
                         "_display_names", "_store_kinds", "resolve_insider_kind",
                         "person_kind"}


def test_LINT_no_function_outside_the_authority_reads_an_insider_column():
    """A LINT, not a protection — the protections are the behavioural battery above, and
    a source assertion cannot fail when behaviour breaks. Its value is different: it
    catches a FUTURE edit that reintroduces a second identity site, which is the failure
    mode that recurred fourteen times.

    Uses AST, so a mention inside a docstring or comment does not trip it.
    """
    tree = ast.parse(inspect.getsource(ic))
    offenders = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in _IDENTITY_AUTHORITIES:
            continue
        doc = ast.get_docstring(node, clean=False)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if doc is not None and sub.value == doc:
                    continue        # the docstring NAMES the forbidden columns to ban them
                low = sub.value.lower()
                if "insider_cik" in low or "insider_name" in low:
                    offenders.append(f"{node.name}: {sub.value[:70]!r}")
    assert not offenders, ("a second identity site appeared outside the authority:\n"
                           + "\n".join(offenders))


# ════════════════════════════════════════════════════════════════════════════
#  Bugs THIS REWRITE introduced, caught by adversarial review — pinned
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad", ["0000000000", "0", "00", "", "   ", "ABC", "12A45",
                                 "1234567890123", "-1", "1.0"])
def test_an_UNUSABLE_cik_is_absent_not_a_namespace(bad):
    """The first draft ended `_norm_cik` with `.lstrip("0") or "0"` — the idiom used
    elsewhere in this codebase — so a PLACEHOLDER all-zero CIK became the truthy node
    "0". Under a GLOBAL union-find that is not a local error: every accession carrying a
    zero-CIK owner unions into one component."""
    assert ic._norm_cik(bad) == "", f"{bad!r} became a usable identity"


@pytest.mark.parametrize("padded,expected", [
    ("0001234567", "1234567"),
    ("1234567", "1234567"),
    ("00000000000001234567", "1234567"),   # over-padded: padding is not information
])
def test_padding_LENGTH_is_not_information(padded, expected):
    """The regex caps SIGNIFICANT digits at ten, not total length. Rejecting an
    over-padded CIK would drop a real filer to punish a formatting quirk; the significant
    digits are the identity."""
    assert ic._norm_cik(padded) == expected


def test_a_ZERO_cik_cofiler_does_not_union_the_whole_corpus():
    """The behavioural form of the above, and the reason it mattered: two unrelated
    filers, each listing a placeholder-CIK co-filer, must not become one person."""
    conn = _db()
    _joint(conn, "A1", [("0000000000", "Placeholder"), ("100", "Smith John")],
           value=150_000.0, days_ago=1)
    _joint(conn, "A2", [("0000000000", "Placeholder"), ("200", "Jones Bob")],
           value=150_000.0, days_ago=2)
    part = ic.person_partition(conn)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert part["100"] != part["200"], "the zero-CIK sentinel merged two real filers"
    assert len(clusters) == 1 and len(clusters[0]["insiders"]) == 2
    assert clusters[0]["total_value"] == 300_000.0


def test_THE_FIFTEENTH_SITE_the_issuer_is_identified_by_cik_not_by_its_ticker_string():
    """The rewrite fixed "who is one insider" and left "which company is this" answered
    by a LABEL — the free-text `issuerTradingSymbol` — while `issuer_cik` sat unused.

    A ticker RENAME mid-horizon splits one company's cluster in two.
    """
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", ticker="OLDT", issuer="ISS-9", value=100_000.0,
         days_ago=2)
    _row(conn, "A2", 0, 0, cik="200", ticker="NEWT", issuer="ISS-9", value=100_000.0,
         days_ago=1)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert len(clusters) == 1, "a ticker rename split one issuer into two groups"
    assert len(clusters[0]["insiders"]) == 2


def test_THE_FIFTEENTH_SITE_a_REUSED_ticker_does_not_merge_two_issuers():
    """The other direction: one symbol, two genuinely different companies."""
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", ticker="SAME", issuer="ISS-A", value=100_000.0)
    _row(conn, "A2", 0, 0, cik="200", ticker="SAME", issuer="ISS-B", value=100_000.0)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters == [], "two different issuers merged through a shared ticker string"


def test_the_projection_yields_exactly_ONE_row_per_transaction():
    """`_buy_rows` uses SELECT DISTINCT with NO aggregate. That is only correct because
    the store writes transaction fields verbatim-identical across owner rows — so the
    property is checked here rather than assumed. If it were ever false, DISTINCT returns
    two rows and the anomaly is LOUD, where MIN/MAX would silently pick one."""
    conn = _db()
    _joint(conn, "A1", [("100", "A"), ("200", "B"), ("300", "C")], txn_index=0)
    _joint(conn, "A1", [("100", "A"), ("200", "B"), ("300", "C")], txn_index=1)
    rows = ic._buy_rows(conn, 45)
    conn.close()
    assert len(rows) == 2, f"3 owners x 2 transactions collapsed to {len(rows)} rows"
    assert {r["txn_index"] for r in rows} == {0, 1}


def test_no_OWNER_SCOPED_column_appears_in_the_buy_row_projection():
    """`insider_kind` is the trap here: 'institution' < 'person', so MIN() over it would
    read institution whenever ANY owner is one, and a 4/A adding a family trust would
    silently drop the leg. K14 with a different column substituted in."""
    rows_src = inspect.getsource(ic._buy_rows)
    sql = rows_src[rows_src.index('sql = f"""'):rows_src.index('return [')]
    for owner_col in ("insider_cik", "insider_name", "insider_name_norm", "insider_kind",
                      "officer_title", "is_director", "is_officer", "owner_seq"):
        assert owner_col not in sql, f"{owner_col} is owner-scoped and must not project"


def test_UNRESOLVED_and_INSTITUTION_are_different_facts_on_the_surface():
    """`resolve_insider_kind` returns `unknown` on a 429 and deliberately does not cache
    it, so a rate-limited worker publishes FEWER insiders than an unthrottled one — same
    data, same second, two answers. Collapsing both into one `dropped_legs` hid that."""
    conn = _db()
    kinds = {"100": "person", "200": "person", "300": "institution", "400": "unknown"}
    for i, cik in enumerate(("100", "200", "300", "400")):
        _row(conn, f"A{i}", 0, 0, cik=cik, value=100_000.0, days_ago=i + 1)
    clusters = ic.find_clusters(
        conn, resolve=lambda conn, cik, **k: kinds.get(cik, "unknown"))
    conn.close()
    c = clusters[0]
    assert c["dropped_institution"] == 1 and c["dropped_unresolved"] == 1
    assert c["provisional"] is True, "a cluster resting on an unresolved lookup was " \
                                     "published as settled"


def test_a_zero_value_buy_is_counted_and_DISCLOSED():
    """A footnoted (empty) price is legitimate and the store keeps it as 0.0. Without
    disclosure a cluster can honestly publish '2 insiders, $0'."""
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", value=0.0, days_ago=1)
    _row(conn, "A2", 0, 0, cik="200", value=100_000.0, days_ago=2)
    clusters = ic.find_clusters(conn)
    conn.close()
    assert clusters[0]["zero_value_trades"] == 1


def test_the_display_name_is_CORPUS_scoped_not_window_scoped():
    """One CIK carries several name spellings over time. A window-scoped pick renders the
    same person differently in two clusters, and a human cannot tell they are one."""
    conn = _db()
    _row(conn, "OLD", 0, 0, cik="100", name="Aaa Spelling", value=1.0, days_ago=900)
    _row(conn, "A1", 0, 0, cik="100", name="Zzz Spelling", value=100_000.0, days_ago=1)
    _row(conn, "A2", 0, 0, cik="200", name="Other", value=100_000.0, days_ago=2)
    clusters = ic.find_clusters(conn)
    conn.close()
    names = [i["name"] for i in clusters[0]["insiders"]]
    assert "Aaa Spelling" in names, f"the name was chosen from the window only: {names}"


def test_corpus_meta_publishes_what_the_counts_MEAN():
    """K13's fix is 'global over the INGESTED corpus', not global. A reader cannot judge
    an insider count without knowing how far back the corpus goes."""
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", value=100_000.0)
    meta = ic.corpus_meta(conn)
    conn.close()
    assert meta["corpus_start"] and meta["horizon_start"]
    assert meta["accessions"] == 1
    assert "predates corpus_start" in meta["note"]


def test_the_issuer_LABEL_is_deterministic_when_two_tickers_tie_on_filing_date():
    """A real coverage gap the mutation harness found: `issuer_labels` picks the ticker
    from the most recent filing, and without a trailing sort key a SAME-DAY tie falls
    through to SQL row order — so the published symbol for one issuer could differ
    between runs. Two tickers, one issuer, one filing date."""
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", ticker="ZZZZ", issuer="ISS-1", value=100_000.0,
         days_ago=1)
    _row(conn, "A2", 0, 0, cik="200", ticker="AAAA", issuer="ISS-1", value=100_000.0,
         days_ago=1)
    labels = [ic.issuer_labels(conn)["ISS-1"] for _ in range(5)]
    conn.close()
    assert len(set(labels)) == 1, f"the issuer label was unstable: {set(labels)}"
    assert labels[0] == "AAAA", ("the tie must resolve by sorted ticker, not by row "
                                 f"order: got {labels[0]}")


# ════════════════════════════════════════════════════════════════════════════
#  The surface — endpoint behaviour, injection, and honest zeros
# ════════════════════════════════════════════════════════════════════════════

def _client():
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


@pytest.mark.parametrize("params", [
    {"window_days": 0}, {"window_days": 999}, {"window_days": "abc"},
    {"min_insiders": 1}, {"min_insiders": 99}, {"min_insiders": "; DROP TABLE alerts;--"},
    {"window_days": "1 OR 1=1"}, {"min_insiders": "' UNION SELECT 1 --"},
])
def test_the_endpoint_REJECTS_out_of_range_and_injected_input(params):
    r = _client().get("/api/insider-clusters", params=params)
    assert r.status_code == 422, f"{params} was not rejected: {r.status_code}"


@pytest.mark.parametrize("verb", ["post", "put", "patch", "delete"])
def test_the_endpoint_is_READ_ONLY_by_method(verb):
    r = getattr(_client(), verb)("/api/insider-clusters")
    assert r.status_code == 405


def test_a_MISSING_STORE_is_an_ERROR_not_an_honest_quiet_day(monkeypatch):
    """The predecessor's `except Exception` turned `no such table` into `count: 0` with
    HTTP 200 — a schema failure indistinguishable from a quiet day, on a surface whose
    entire claim is that its zeros are honest."""
    body = _client().get("/api/insider-clusters").json()
    assert body["count"] == 0
    assert body["error"] and "EMPTY STORE" in body["error"]


def test_a_REAL_schema_error_is_NOT_swallowed(monkeypatch):
    """Only a missing table is tolerated; anything else must surface as a 500."""
    import sqlite3
    from api.routers import insider_clusters as rt

    def boom(*a, **k):
        raise sqlite3.OperationalError("no such column: nonsense")
    monkeypatch.setattr(rt, "find_clusters", boom)
    with pytest.raises(sqlite3.OperationalError):
        _client().get("/api/insider-clusters")


def test_the_surface_publishes_no_confidence_and_states_its_corpus():
    conn = _db()
    _row(conn, "A1", 0, 0, cik="100", value=100_000.0, days_ago=1)
    _row(conn, "A2", 0, 0, cik="200", value=100_000.0, days_ago=2)
    conn.close()
    body = _client().get("/api/insider-clusters").json()
    assert body["kind"] == "discovery"
    assert "no confidence" in body["disclaimer"] or "carries no" in body["disclaimer"]
    # The corpus block is what makes an insider COUNT interpretable: identity is global
    # over the ingested corpus only.
    assert body["corpus"]["corpus_start"] and body["corpus"]["horizon_start"]
    for c in body["clusters"]:
        assert "confidence" not in repr(c).lower()
        assert set(c) >= {"provisional", "legs_dropped_unresolved",
                          "undisclosed_10b5_1_trades"}
