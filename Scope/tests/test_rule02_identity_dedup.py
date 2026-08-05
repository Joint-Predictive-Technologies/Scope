"""RULE_02 identity is the MEMBER SET, not the headline string.

Two defects, both reproduced on the working DB before this change:

  #3 IDENTITY. `alert_exists` keyed on `(rule, ticker, headline)`. The headline
     carries the member COUNT, so the sliding window's 4-member view and its
     3-member sub-view produced different strings and the dedup never saw them as
     related. 15 strict subset/superset pairs exist on the stored corpus, including

         id 8597  "Cluster: 4 members bought SPCX within 7 days (NET_LONG)"  HIGH
         id 8598  "Cluster: 3 members bought SPCX within 7 days (NET_LONG)"  HIGH

     both emitted at the SAME timestamp, from two overlapping anchor windows in one
     run — plus an MSFT chain 46 ⊃ 47 ⊃ 48.

  #4 REFIRE. The dedup looked back 7 days against a `--days` scan default of 90 —
     13x shorter. Under the identity this fix implements — (ticker, members,
     DIRECTION) — that is 2 refire groups / 3 redundant rows, the largest being the
     identical AAPL member set fired three times: 2026-06-17, 07-09, 07-20.
     (An earlier count of "4 groups" ignored direction and swept in two same-run
     MIXED/NET_LONG pairs that this fix deliberately KEEPS.)

RULE_CLUSTER already solved both; this mirrors it, including the cross-symbol
collision fix its 2026-08-03 session added.

⭐ THE COLLISION GUARD IS LOAD-BEARING. After the ticker-resolution fix every
unvalidated cluster stores `ticker=''`. A dedup narrowed on the stored ticker would
lump every unvalidated cluster of every company into one namespace, and since the
identity test compares member sets, two DIFFERENT companies could dedup against each
other. So candidates are matched on the symbol recovered from their own fingerprint.

⚠️ CALIBRATION, so this is not oversold: the gate counts DISTINCT RULES, and
`rule10_instruments(["RULE_02","RULE_02"])` is one `congressional` instrument — a
double-fire never double-counted at the gate. This is duplicate-card and refire
noise, NOT gate fabrication. RULE_02 is unsigned, so retraction is gate-cosmetic and
the stored cleanup is correctness-of-record.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import rule_02_cluster as r02  # noqa: E402
from jpt_common import db_connection  # noqa: E402


def cluster(members, ticker, direction="NET_LONG", resolved=True, count=None):
    ids = sorted(members)
    n = count if count is not None else len(ids)
    return {
        "resolved": resolved,
        "ticker": ticker,
        "headline": f"Cluster: {n} members bought {ticker} within 7 days ({direction})",
        "severity": "HIGH",
        "tags": ",".join(f"Member {m}" for m in ids),
        "member_count": n,
        "net_direction": direction,
        "members": ids,
        "fingerprint": r02._fingerprint(ids, ticker, direction),
    }


def stored(conn):
    conn.commit()
    return conn.execute(
        "SELECT id, ticker, headline, lifecycle_stage, why_matters FROM alerts "
        "WHERE rule='RULE_02' ORDER BY id"
    ).fetchall()


# --------------------------------------------------------------------------
# #3 — the nested subset
# --------------------------------------------------------------------------

def test_the_SPCX_subset_no_longer_double_fires():
    """8597 (4 members) and 8598 (3 members, a strict subset) both fired, one run."""
    four = cluster(["A", "B", "C", "D"], "SPCX")
    three = cluster(["A", "B", "C"], "SPCX")
    with db_connection() as conn:
        n = r02.emit_alerts(conn, [three, four])   # deliberately smallest-first
        rows = stored(conn)
    assert n == 1, f"expected 1 emission, got {n}"
    assert len(rows) == 1
    assert "4 members" in rows[0]["headline"], "the FULLER cluster must be the one kept"


def test_the_subset_is_suppressed_whatever_order_they_arrive_in():
    """emit_alerts sorts largest-first, so the outcome cannot depend on find order."""
    four = cluster(["A", "B", "C", "D"], "SPCX")
    three = cluster(["A", "B", "C"], "SPCX")
    for order in ([three, four], [four, three]):
        with db_connection() as conn:
            r02.emit_alerts(conn, list(order))
            rows = stored(conn)
        assert len(rows) == 1 and "4 members" in rows[0]["headline"]


def test_an_EXPANDED_member_set_supersedes_rather_than_being_dropped():
    """A later run finding a bigger cluster must record it AND retire the smaller."""
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster(["A", "B", "C"], "NVDA")])
        assert len(stored(conn)) == 1
        r02.emit_alerts(conn, [cluster(["A", "B", "C", "D"], "NVDA")])
        rows = stored(conn)
    assert len(rows) == 2, "the expansion must be recorded, not silently dropped"
    small = [r for r in rows if "3 members" in r["headline"]][0]
    big = [r for r in rows if "4 members" in r["headline"]][0]
    assert small["lifecycle_stage"] == "superseded"
    assert big["lifecycle_stage"] == "created"


def test_a_shrunk_view_after_a_superset_is_recorded_is_not_emitted():
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster(["A", "B", "C", "D"], "NVDA")])
        n = r02.emit_alerts(conn, [cluster(["A", "B"], "NVDA")])
        rows = stored(conn)
    assert n == 0 and len(rows) == 1


# --------------------------------------------------------------------------
# #4 — the refire
# --------------------------------------------------------------------------

def test_the_identical_AAPL_member_set_does_not_refire_across_runs():
    """Fired 2026-06-17, 07-09 and 07-20 — 23 and 11 days apart, past the old 7."""
    c = cluster(["A", "B", "C"], "AAPL")
    with db_connection() as conn:
        assert r02.emit_alerts(conn, [c], days=90) == 1
        assert r02.emit_alerts(conn, [c], days=90) == 0, "second run must dedup"
        assert r02.emit_alerts(conn, [c], days=90) == 0, "third run must dedup"
        assert len(stored(conn)) == 1


def test_the_dedup_window_follows_days_not_a_hardcoded_7():
    """The old lookback was 7 against a 90-day scan — 13x too short."""
    import inspect
    src = inspect.getsource(r02._prior_alerts)
    assert "'-7 days'" not in src and '"-7 days"' not in src
    assert "days" in inspect.signature(r02._prior_alerts).parameters
    assert "days" in inspect.signature(r02.emit_alerts).parameters


# --------------------------------------------------------------------------
# ⭐ The collision guard — the load-bearing check
# --------------------------------------------------------------------------

def test_two_DIFFERENT_companies_with_a_blank_ticker_do_not_dedup_together():
    """Both store ticker='', and the identity test compares member sets.

    Without recovering each candidate's own symbol from its fingerprint, an
    unvalidated FOO cluster and an unvalidated BAR cluster with the same members
    would dedup against each other — the exact bug RULE_CLUSTER hit and fixed.
    """
    foo = cluster(["A", "B", "C"], "FOOX", resolved=False)
    bar = cluster(["A", "B", "C"], "BARX", resolved=False)
    with db_connection() as conn:
        assert r02.emit_alerts(conn, [foo]) == 1
        n = r02.emit_alerts(conn, [bar])
        rows = stored(conn)
    assert n == 1, "a different company must still emit despite the shared blank key"
    assert len(rows) == 2
    assert all(r["ticker"] == "" for r in rows)
    fps = {r02._extract_fingerprint(r["why_matters"]) for r in rows}
    assert len(fps) == 2, "the two must carry distinct identities"


def test_a_blank_ticker_cluster_does_not_supersede_another_companys():
    """The superset rule must not reach across the shared '' namespace either."""
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster(["A", "B", "C"], "FOOX", resolved=False)])
        r02.emit_alerts(conn, [cluster(["A", "B", "C", "D"], "BARX", resolved=False)])
        rows = stored(conn)
    assert len(rows) == 2
    assert [r["lifecycle_stage"] for r in rows] == ["review", "review"], \
        "neither may be marked superseded by the other company"


def test_the_same_company_still_dedups_when_its_ticker_is_blank():
    """The guard must not disable dedup for a genuinely repeated unvalidated cluster."""
    c = cluster(["A", "B", "C"], "FOOX", resolved=False)
    with db_connection() as conn:
        assert r02.emit_alerts(conn, [c]) == 1
        assert r02.emit_alerts(conn, [c]) == 0
        assert len(stored(conn)) == 1


# --------------------------------------------------------------------------
# Controls — nothing real may be lost
# --------------------------------------------------------------------------

def test_a_genuinely_new_cluster_still_fires():
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster(["A", "B", "C"], "NVDA")])
        n = r02.emit_alerts(conn, [cluster(["X", "Y", "Z"], "AMD")])
        assert n == 1 and len(stored(conn)) == 2


def test_a_disjoint_cluster_on_the_SAME_ticker_still_fires():
    """Different members, same symbol — a second real signal, not a duplicate."""
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster(["A", "B", "C"], "NVDA")])
        n = r02.emit_alerts(conn, [cluster(["X", "Y", "Z"], "NVDA")])
        assert n == 1 and len(stored(conn)) == 2


def test_the_same_members_in_the_OPPOSITE_direction_still_fires():
    """Identity is (members, ticker, DIRECTION) — a reversal is a new signal."""
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster(["A", "B", "C"], "NVDA", "NET_LONG")])
        n = r02.emit_alerts(conn, [cluster(["A", "B", "C"], "NVDA", "NET_SHORT")])
        assert n == 1 and len(stored(conn)) == 2


def test_identity_rides_in_why_matters_NOT_detail():
    """⚠️ `detail` is rendered as PROSE to users and must stay empty here.

    `api/receipts.py::_generic`, `api/static/congress.html` (which fetches
    rule=RULE_02 by name), `scripts/telegram_bot.py` and `scripts/generate_brief.py`
    all print `alerts.detail` straight out. An earlier draft stored the identity
    there as JSON, which would have shown users
    `{"fingerprint": "RULE02::C001123+M001217::SPCX::NET_LONG", ...}` where a
    sentence belongs. RULE_CLUSTER escapes that only because it has a dedicated
    receipt builder; RULE_02 has none.

    `tags` also stays a comma-joined NAME string — the #1 remap segments it.
    """
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster(["A", "B", "C"], "NVDA")])
        row = stored(conn)[0]
        detail = conn.execute(
            "SELECT detail FROM alerts WHERE rule='RULE_02'").fetchone()["detail"]
    assert detail is None, "detail is user-facing prose — identity must not live there"
    fp = r02._extract_fingerprint(row["why_matters"])
    assert fp == "RULE02::A+B+C::NVDA::NET_LONG"
    assert r02._fingerprint_parts(fp) == ({"A", "B", "C"}, "NET_LONG")
    assert row["ticker"] == "NVDA"


def test_the_fingerprint_carries_the_GROUP_symbol_even_when_stored_blank():
    """That is what makes the collision guard work for unvalidated clusters."""
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster(["A", "B", "C"], "FOOX", resolved=False)])
        row = stored(conn)[0]
    assert row["ticker"] == ""
    assert r02._fingerprint_ticker(r02._extract_fingerprint(row["why_matters"])) == "FOOX"


@pytest.mark.parametrize("fp,expected", [
    ("RULE02::A+B+C::NVDA::NET_LONG", "NVDA"),
    ("RULE02::A::FOOX::MIXED", "FOOX"),
    ("CLUSTER::A+B::NVDA::consensus_buy", "NVDA"),
    ("malformed", None),
    ("", None),
    (None, None),
])
def test_fingerprint_ticker_extraction(fp, expected):
    assert r02._fingerprint_ticker(fp) == expected


def test_the_RULE02_prefix_cannot_be_confused_with_RULE_CLUSTERs():
    assert r02._fingerprint(["A"], "NVDA", "NET_LONG").startswith("RULE02::")


# --------------------------------------------------------------------------
# Legacy rows
# --------------------------------------------------------------------------

def test_a_legacy_row_without_detail_still_dedups_by_headline():
    """Every stored RULE_02 alert has detail IS NULL.

    Without this fallback the first run after deploy would re-emit the whole corpus,
    because `_prior_alerts` can only see rows that carry an identity.
    """
    c = cluster(["A", "B", "C"], "NVDA")
    with db_connection() as conn:
        conn.execute(
            "INSERT INTO alerts (rule, headline, severity, tags, ticker) VALUES (?,?,?,?,?)",
            ("RULE_02", c["headline"], "HIGH", c["tags"], "NVDA"),
        )
        conn.commit()
        n = r02.emit_alerts(conn, [c])
    assert n == 0, "a legacy row must still suppress its own re-emission"


def test_the_legacy_fallback_is_scoped_to_rows_WITHOUT_an_identity():
    """⚠️ Regression pin for a flaw this file's own control caught.

    The headline carries only the member COUNT, so two genuinely distinct 3-member
    NVDA clusters share a headline exactly. An unscoped legacy fallback therefore
    suppressed the second — reintroducing the identity blindness the fingerprint
    exists to remove, on a path no fingerprint test would have covered.

    `AND detail IS NULL` confines it to rows that carry no identity.
    """
    import inspect
    assert "NOT LIKE" in inspect.getsource(r02.legacy_alert_exists)

    a = cluster(["A", "B", "C"], "NVDA")
    b = cluster(["X", "Y", "Z"], "NVDA")
    assert a["headline"] == b["headline"], "the fixture must exercise the collision"
    with db_connection() as conn:
        assert r02.emit_alerts(conn, [a]) == 1
        assert r02.emit_alerts(conn, [b]) == 1, \
            "a distinct member set must not be suppressed by a shared headline"
        assert len(stored(conn)) == 2


def test_a_prior_alert_30_DAYS_OLD_still_suppresses_a_refire():
    """BEHAVIOURAL proof that the window follows --days, not a hardcoded 7.

    The source-inspection test above cannot catch a revert to 7 days, because rows
    written during a test are seconds old and fall inside either window. This backdates
    the prior alert to 30 days — outside the old 7-day lookback, inside the 90-day scan
    — which is exactly the AAPL case (fired 06-17, 07-09, 07-20).
    """
    c = cluster(["A", "B", "C"], "AAPL")
    with db_connection() as conn:
        assert r02.emit_alerts(conn, [c], days=90) == 1
        conn.execute(
            "UPDATE alerts SET created_at = datetime('now','-30 days') WHERE rule='RULE_02'")
        conn.commit()
        assert r02.emit_alerts(conn, [c], days=90) == 0, \
            "a 30-day-old identical cluster must still suppress the refire"
        assert len(stored(conn)) == 1


def test_a_prior_alert_OUTSIDE_the_window_is_correctly_ignored():
    """Control for the above — the lookback must still have an edge."""
    c = cluster(["A", "B", "C"], "AAPL")
    with db_connection() as conn:
        r02.emit_alerts(conn, [c], days=90)
        conn.execute(
            "UPDATE alerts SET created_at = datetime('now','-200 days') WHERE rule='RULE_02'")
        conn.commit()
        assert r02.emit_alerts(conn, [c], days=90) == 1, \
            "beyond the window it is a genuinely new observation"


# --------------------------------------------------------------------------
# Mutations the verifier found my tests did NOT catch
# --------------------------------------------------------------------------

def test_the_LEGACY_window_also_follows_days_not_7():
    """🔴 The most important gap the verifier found.

    All 82 stored RULE_02 alerts predate the fingerprint, so `legacy_alert_exists` is
    the ONLY dedup that can see them until the corpus turns over — and the AAPL refire
    IS a legacy-row refire. My 30-day backdate test seeded a FINGERPRINT row, so it
    exercised the wrong path: reverting only the legacy window to 7 days re-opened
    defect #4 with the entire suite green.
    """
    c = cluster(["A", "B", "C"], "AAPL")
    with db_connection() as conn:
        # a LEGACY row: no identity in why_matters, backdated past the old 7 days
        conn.execute(
            "INSERT INTO alerts (rule, headline, severity, tags, ticker, created_at) "
            "VALUES (?,?,?,?,?, datetime('now','-30 days'))",
            ("RULE_02", c["headline"], "HIGH", c["tags"], "AAPL"),
        )
        conn.commit()
        assert r02.emit_alerts(conn, [c], days=90) == 0, \
            "a 30-day-old LEGACY row must still suppress the refire"


def test_the_superset_rule_is_direction_BLIND_by_design_KNOWN_TRADEOFF():
    """Pins the design decision so it cannot drift either way, silently.

    A 2-member NET_LONG subset of a 4-member NET_SHORT window IS suppressed. On the
    stored corpus 7 of 13 redundant subsets differ in direction from the canonical
    they fold into, and in a full-history replay it is 59 of 131 suppressions, 13 of
    them strict LONG<->SHORT. The rationale is that overlapping 7-day windows on one
    ticker are slices of one event and the fuller read wins — but it is inconsistent
    with identity itself, where direction DOES distinguish. Deliberate, disclosed.
    """
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster(["A", "B", "C", "D"], "MSFT", "NET_SHORT")])
        n = r02.emit_alerts(conn, [cluster(["A", "B"], "MSFT", "NET_LONG")])
    assert n == 0, "a direction-flipped SUBSET is folded into the fuller window"


def test_a_superseded_row_is_not_a_dedup_candidate():
    """Once superseded, a row must stop suppressing anything.

    Re-admitting superseded rows as candidates passed the whole suite.
    """
    import inspect
    assert "!= 'superseded'" in inspect.getsource(r02._prior_alerts)
    with db_connection() as conn:
        r02.emit_alerts(conn, [cluster(["A", "B", "C"], "NVDA")])
        r02.emit_alerts(conn, [cluster(["A", "B", "C", "D"], "NVDA")])   # supersedes it
        # the superseded 3-member identity must now be re-emittable
        n = r02.emit_alerts(conn, [cluster(["A", "B", "C"], "NVDA")])
    assert n == 0, "still suppressed — by the 4-member superset, not by the retired row"


def test_emit_alerts_defaults_to_the_90_day_scan_window():
    """A default of 7 would silently reinstate defect #4 for any caller that omits it."""
    import inspect
    assert inspect.signature(r02.emit_alerts).parameters["days"].default == 90


def test_a_symbol_containing_the_separator_does_not_break_the_guard():
    """The verifier planted 'X::B' and made a DIFFERENT company dedup against it.

    A fixed `parts[2]` desynchronises; joining parts[2:-1] does not. No transaction
    carries ':' today, so this is latent — and inherited from rule_cluster.py, which
    still has the fixed-index form.
    """
    assert r02._fingerprint_ticker("RULE02::A+B::X::B::NET_LONG") == "X::B"
    with db_connection() as conn:
        assert r02.emit_alerts(conn, [cluster(["A", "B", "C"], "X::B", resolved=False)]) == 1
        n = r02.emit_alerts(conn, [cluster(["A", "B", "C"], "X", resolved=False)])
    assert n == 1, "'X' must not dedup against 'X::B'"
