#!/usr/bin/env python3
"""
rule_02_cluster.py

Detects when 3+ members of Congress trade the same ticker DIRECTIONALLY within
a 7-day rolling window and emits a RULE_02 cluster alert.

"Directionally" is load-bearing: a member whose only activity on the ticker in
the window is an exchange is present but expresses no direction, and counting
them inflated both the headline count and its verb.

TICKER RESOLUTION. A cluster confers a corroboration key only when its symbol
verifies against `tickers`. Unresolved clusters are still emitted — with
`ticker=''`, `lifecycle_stage='review'` and the symbol in `why_matters` — because
absence from `tickers` is a coverage gap, not proof the symbol is fake.

⚠️ KNOWN AND NOT FIXED HERE, two residuals of the raw parse string:

  * Unresolved rows group by that raw string, so DISTINCT companies can share one
    unkeyed cluster. Real example: `raw='CS'` appears under both "Walmart Inc."
    and "The Walt Disney Company", giving one "N members bought CS" alert. This is
    strictly better than before — that cluster used to carry `ticker='CS'` and
    could corroborate — but it is still a conflated alert. Fixing it needs the
    parser, not this rule.
  * 30 transactions carry a resolved `ticker_id` and NO raw string, and are
    excluded, exactly as they were before this change. They cannot be recovered
    through the link: 14 of the 30 are mis-linked, and an FK-derived key is in
    `tickers` by construction, so admitting them demotes the genuine cluster for
    that symbol. A COVERAGE concern for the ingestion linker; see `resolve_key`.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta

from jpt_common import db_connection, normalize_ticker


RULE = "RULE_02"
WINDOW_DAYS = 7

#: Fingerprint namespace. Distinct from rule_cluster's "CLUSTER::" so the two
#: rules' identities can never be mistaken for one another.
FP_PREFIX = "RULE02::"


def _validity_set(conn) -> set:
    """Canonical symbols from `tickers`, the resolution set.

    Deliberately the SAME set, built the same way, as
    `rule_01b_first_touch._validity_set` — `normalize_ticker` canonicalises '-' to
    '.', so BRK-B and BRK.B collapse to one symbol instead of splitting a company
    across two corroboration keys. A second canonicaliser here would be a second
    thing to keep in sync.
    """
    return {normalize_ticker(r["symbol"])
            for r in conn.execute("SELECT symbol FROM tickers")
            if r["symbol"]}


def resolve_key(raw: str | None, fk_symbol: str | None, valid: set) -> tuple[str, bool]:
    """(grouping key, resolved?) for one transaction row.

    A three-rung ladder, in this order, that never merges, never fuzzy-resolves
    and never drops:

      1. The raw string canonicalises into `tickers` -> that canonical symbol.
         This is RULE_01B #4's rule exactly.
      2. Otherwise UNRESOLVED: the row still groups (on its canonicalised raw, so
         it stays visible and clusters with its own kind) but confers no key.

    THE INVARIANT, which the rest of the rule depends on:

        resolved is True  <=>  key in valid

    So resolution is a pure function of the key, which is what makes it uniform
    across a group and lets a cluster's status be read off any of its rows.
    `test_resolution_is_a_pure_function_of_the_key` pins it.

    ⚠️ `transactions.ticker_id` NEVER participates. It is assigned by a company-NAME
    matcher and is not trustworthy in either direction:

      * With a raw string present, three groupings join DISTINCT companies —
        `IDEXX`->DLB, `MTRS`->GIS, `CNSWF`->STZ. So the FK must never override or
        supplement a raw string.
      * With NO raw string, 14 of the 30 such rows are mis-linked (`ASCIX` x13 is
        "Angel Oak Strategic Credit Fund" in `tickers` against "Oaktree Strategic
        Credit Fund" in the filing; `RBBN` is "Ribbon" against "Verizon").

    An earlier draft admitted that second group as a non-keying "recovery", on the
    reasoning that an empty raw string leaves no competing signal. That was wrong
    twice over. First, the absence of a contradicting signal is not the absence of
    a contradiction — it only removes the means of DETECTING one. Second, and
    worse, the verifier showed the recovery was not even inert: an FK-derived key
    comes from `tickers.symbol` and is therefore IN the validity set by
    construction, so a recovered row landed in the genuine cluster for that symbol
    and dragged it to unresolved — stripping the key off real `MRK` and `PLTR`
    clusters and labelling them "symbol not in `tickers`" about symbols that are.

    Those rows are therefore left out, exactly as before this change. They are a
    COVERAGE problem for the ingestion linker, not a keying problem for RULE_02;
    see the module docstring.
    """
    norm = normalize_ticker(raw) if raw else None
    if norm and norm in valid:
        return norm, True
    return (norm or ""), False


def fetch_transactions(conn, days: int) -> list[dict]:
    """Rows for clustering, keyed on the RESOLVED symbol where one exists.

    The old query selected `raw_ticker_string AS ticker` and filtered on it being
    non-NULL, so it (a) grouped and keyed on an unvalidated parse string — `US` is
    not a symbol, yet 213 transactions carry it and it produced four alerts — and
    (b) discarded every row that had a resolved `ticker_id` but no raw string.
    """
    valid = _validity_set(conn)
    rows = conn.execute(
        """
        SELECT
            t.member_id,
            t.raw_ticker_string  AS raw_symbol,
            t.transaction_type,
            t.transaction_date,
            m.full_name,
            -- The cross-partisan definition needs party, and this join already
            -- exists for `full_name` — so party is one column, not a new source.
            -- A LEFT JOIN means an unmatched filer yields NULL, which
            -- `is_cross_partisan` treats as "satisfies neither side", never as
            -- a guess.
            m.party
        FROM transactions t
        LEFT JOIN members m ON m.bioguide_id = t.member_id
        WHERE t.raw_ticker_string IS NOT NULL
          AND t.transaction_date >= date('now', ?)
          AND t.transaction_date <= date('now')
        ORDER BY t.raw_ticker_string, t.transaction_date
        """,
        (f"-{days} days",),
    ).fetchall()

    out = []
    for r in rows:
        row = dict(r)
        key, resolved = resolve_key(row["raw_symbol"], None, valid)
        if not key or " " in key:      # baskets / multi-symbol strings, as RULE_CLUSTER does
            continue
        row["ticker"] = key
        row["resolved"] = resolved
        out.append(row)
    return out


def direction(transaction_type: str | None) -> str:
    if not transaction_type:
        return "neutral"
    tt = transaction_type.lower()
    if tt.startswith("sale"):
        return "sell"
    # Prefix, not equality. The two arms were asymmetric — `startswith("sale")`
    # against `== "purchase"` — so "Purchase (Partial)" read neutral while
    # "Sale (Partial)" read sell. Harmless while a neutral row merely weakened
    # the verb; a SUPPRESSION path once neutral members stopped being counted.
    # `ingest_senate.transaction_verb` already prefixes both.
    if tt.startswith("purchase"):
        return "buy"
    return "neutral"


#: The two parties whose agreement RULE_02 is *about*. Values are `members.party`
#: verbatim — measured on the roster as 'Democratic' (1350) / 'Republican' (1328),
#: with zero NULLs. Not a hardcoded member→party map: the roster is the source.
DEMOCRAT = "Democratic"
REPUBLICAN = "Republican"


def is_cross_partisan(parties) -> bool:
    """Do the COUNTED members span both major parties?

    ⚠️ THE DEFINITION, NOT A HEURISTIC. RULE_02's refined meaning is "a Democrat
    AND a Republican agree" — not "N members clustered". A same-party cluster is
    a real but *different*, weaker signal, and under the hard gate it does not
    emit at all.

    ⚠️ INDEPENDENTS, MINOR PARTIES AND UNKNOWN PARTY SATISFY NEITHER SIDE BUT
    BLOCK NOTHING. `D + R + Independent` is cross-partisan; `D + Independent` is
    not. The same holds for a NULL party from an unmatched filer: it can never
    stand in for the missing side, and it can never veto a pair that is already
    present. That asymmetry is deliberate — an unknown must not be able to
    manufacture agreement, and must not be able to destroy it either.

    Measured on the roster: 12 Independents plus three singletons (New
    Progressive, Libertarian, Independent Democrat) exist, but **none of the 119
    members who have ever traded is one of them**, so this arm is currently
    unexercised in production and is held by fixture instead.
    """
    seen = set(parties)
    return DEMOCRAT in seen and REPUBLICAN in seen


def party_split(parties) -> tuple[int, int]:
    """(#Democrats, #Republicans) — for the headline only, never for the gate."""
    seq = list(parties)
    return sum(1 for p in seq if p == DEMOCRAT), sum(1 for p in seq if p == REPUBLICAN)


def member_direction(rows: list[dict]) -> str:
    """buy / sell / mixed / neutral for ONE member, over all their rows on the ticker.

    Composes direction() rather than testing a literal set of transaction_type
    strings. rule_cluster._member_direction keeps its own {"sale", "sale_partial"}
    literal, which is exactly how it came to miss "sale_full"; anything direction()
    learns to classify, this classifies too.

    "neutral" means the member traded but said nothing directional (exchange-only).
    They are present in the window and must NOT be counted as consensus.
    """
    dirs = {direction(r["transaction_type"]) for r in rows}
    has_buy = "buy" in dirs
    has_sell = "sell" in dirs
    if has_buy and has_sell:
        return "mixed"
    if has_buy:
        return "buy"
    if has_sell:
        return "sell"
    return "neutral"


#: A member counts toward the cluster if they traded directionally at all.
#: "mixed" counts — they did trade on a direction — matching how
#: rule_cluster._cluster_direction folds mixed members into its consensus set.
COUNTED_DIRECTIONS = ("buy", "sell", "mixed")


def net_direction(member_dirs: list[str]) -> str:
    """Cluster verb from the per-member directions of the COUNTED members.

    NOTE the buy-vs-sell contest is a MAJORITY, not unanimity — it is deliberately
    NOT the same rule as rule_cluster._cluster_direction, which returns "mixed"
    unless every member agrees. A 3-buy/2-sell cluster still reports NET_LONG here.
    That is pre-existing and out of scope for this fix; it is pinned as a residual
    in tests/test_rule02_directional_count.py.

    An individually MIXED member is different, and does force MIXED. They are
    counted — they did trade directionally — but they are not described by
    "bought" or "sold", and letting them merely abstain reintroduced the exact bug
    this fix exists to remove: on the real corpus, MSFT (one buyer + one member who
    both bought and sold) reported "2 members bought MSFT" on the strength of a
    single buyer. The count and the verb must describe the same members.
    """
    if any(d == "mixed" for d in member_dirs):
        return "MIXED"
    buys = sum(1 for d in member_dirs if d == "buy")
    sells = sum(1 for d in member_dirs if d == "sell")
    if buys > sells:
        return "NET_LONG"
    if sells > buys:
        return "NET_SHORT"
    return "MIXED"


def find_clusters(
    transactions: list[dict], min_members: int
) -> list[dict]:
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for txn in transactions:
        by_ticker[txn["ticker"]].append(txn)

    clusters = []

    for ticker, txns in by_ticker.items():
        txns_sorted = sorted(
            txns,
            key=lambda x: x["transaction_date"] or "",
        )

        # Slide a 7-day window: for each txn as window start, collect all
        # txns within WINDOW_DAYS, then check distinct member count.
        seen_windows: set[tuple] = set()

        for i, anchor in enumerate(txns_sorted):
            anchor_date_str = anchor["transaction_date"]
            if not anchor_date_str:
                continue
            try:
                anchor_date = date.fromisoformat(anchor_date_str)
            except ValueError:
                continue

            window_end = anchor_date + timedelta(days=WINDOW_DAYS - 1)

            in_window: list[dict] = []
            for txn in txns_sorted[i:]:
                txn_date_str = txn["transaction_date"]
                if not txn_date_str:
                    continue
                try:
                    txn_date = date.fromisoformat(txn_date_str)
                except ValueError:
                    continue
                if txn_date > window_end:
                    break
                in_window.append(txn)

            # Group every row the member has in this window BEFORE deciding
            # their direction. The old code kept each member's FIRST row and
            # voted on that alone, so a member who exchanged on Monday and sold
            # on Tuesday voted "neutral" — the vote depended on row order.
            rows_by_member: dict[str, list[dict]] = defaultdict(list)
            for t in in_window:
                if t["member_id"]:
                    rows_by_member[t["member_id"]].append(t)

            member_dirs = {
                mid: member_direction(rows) for mid, rows in rows_by_member.items()
            }

            # THE FIX: count only members who actually traded directionally.
            # An exchange-only member is present in the window but says nothing
            # about direction, and counting them inflated both the headline
            # number and the verb — "3 members sold WAT" over two exchanges and
            # one partial sale.
            counted = {
                mid for mid, d in member_dirs.items() if d in COUNTED_DIRECTIONS
            }
            if len(counted) < min_members:
                continue

            # THE DEFINITION: a Democrat AND a Republican, among the COUNTED
            # members. Gated on `counted`, not on everyone in the window — an
            # exchange-only member says nothing about direction and so cannot
            # supply the agreement either.
            #
            # ⚠️ The position relative to `seen_windows` is NOT load-bearing, and
            # an earlier version of this comment claimed it was. `window_key`
            # already includes `anchor_date`, so a rejected window can never have
            # occupied another window's slot wherever the check sits — moving this
            # block below `seen_windows.add` kills no test and produces
            # byte-identical output over 365d and 3650d of real data. It is here
            # only because rejecting early is cheaper.
            member_party = {
                mid: (rows[0].get("party") if rows else None)
                for mid, rows in rows_by_member.items()
            }
            cluster_parties = [member_party.get(mid) for mid in counted]
            if not is_cross_partisan(cluster_parties):
                continue

            # Deduplicate windows with the same member set + date span to
            # avoid reporting the same cluster from every anchor point.
            # Keyed on the COUNTED set: two windows differing only by an
            # uncounted member are the same cluster.
            window_key = (
                ticker,
                anchor_date,
                frozenset(counted),
            )
            if window_key in seen_windows:
                continue
            seen_windows.add(window_key)

            # One row per counted member, for naming only.
            seen_ids: set[str] = set()
            deduped: list[dict] = []
            for t in in_window:
                mid = t["member_id"]
                if mid in counted and mid not in seen_ids:
                    seen_ids.add(mid)
                    deduped.append(t)

            nd = net_direction([member_dirs[mid] for mid in counted])
            member_count = len(counted)
            action_word = "bought" if nd == "NET_LONG" else "sold" if nd == "NET_SHORT" else "traded"
            n_dem, n_rep = party_split(cluster_parties)
            # ⚠️ The composition goes at the END, deliberately. The core phrase
            # "{n} members {verb} {ticker}" is asserted as a SUBSTRING by the
            # existing #1 and #2 test suites and read by humans in the feed;
            # splicing "(2D/1R)" into the middle of it broke eleven of those
            # assertions and would have broken anything else matching on the
            # phrase. Prefix and suffix carry the new claim without disturbing it.
            headline = (
                f"Cross-partisan cluster: {member_count} members {action_word} "
                f"{ticker} within 7 days ({nd}) [{n_dem}D/{n_rep}R]"
            )
            # ⚠️ LOAD-BEARING, AND NOT COSMETIC. `emit_alerts` probes
            # `legacy_alert_exists` — which matches stored rows on HEADLINE TEXT —
            # for every row written before the fingerprint existed, and today that
            # is ALL 82 of them. Changing the headline without this would make
            # every one of those rows stop matching and re-emit the surviving
            # corpus as duplicates on the first run after deploy, which is the
            # exact failure that function's docstring warns about. So the OLD
            # string is retained verbatim, used ONLY as the legacy probe key and
            # never stored or displayed.
            legacy_headline = (
                f"Cluster: {member_count} members {action_word} {ticker} "
                f"within 7 days ({nd})"
            )
            severity = "MEDIUM" if nd == "MIXED" else "HIGH"
            # Names are the COUNTED members only — the tag list and the count
            # have to describe the same set, or the receipt contradicts the
            # headline it is meant to evidence.
            names = sorted(
                t["full_name"] or t["member_id"]
                for t in deduped
                if t["full_name"] or t["member_id"]
            )
            tags = ",".join(names)

            # Uniform across the group because `resolve_key` guarantees
            # `resolved is True <=> key in valid`, and the group IS the key.
            #
            # ⚠️ This was NOT true of an earlier draft, and the comment here
            # asserted it anyway. That draft let a row keyed from `ticker_id` be
            # unresolved while carrying a key drawn from `tickers.symbol` — in the
            # validity set by construction — so resolved and unresolved rows
            # collided in one group and `all()` silently demoted real clusters.
            # The invariant is now real; `any()` and `all()` agree by construction
            # and `test_resolution_is_a_pure_function_of_the_key` holds it there.
            resolved = all(t.get("resolved", True) for t in in_window)
            assert resolved == any(t.get("resolved", True) for t in in_window), (
                "mixed-resolution group — the resolve_key invariant has been broken"
            )

            # The member IDS, not their names. `tags` carries names and is left
            # alone — the directional-count remap segments member names out of it,
            # and the UI reads it — so identity travels separately.
            member_ids = sorted(counted)

            clusters.append(
                {
                    "resolved": resolved,
                    "ticker": ticker,
                    "headline": headline,
                    "legacy_headline": legacy_headline,
                    "party_split": (n_dem, n_rep),
                    "severity": severity,
                    "tags": tags,
                    "member_count": member_count,
                    "net_direction": nd,
                    "members": member_ids,
                    "fingerprint": _fingerprint(member_ids, ticker, nd),
                }
            )

    return clusters


def _fingerprint(members: list[str], ticker: str, direction: str) -> str:
    """Cluster identity: the MEMBER SET, the symbol, the direction.

    Mirrors `scripts/rule_cluster.py::_fingerprint`, with a `RULE02::` prefix so the
    two rules' identities can never be mistaken for one another.

    This replaces identity-by-headline. The headline carries the member COUNT, so a
    4-member window and its 3-member sub-window produced different strings and the
    dedup never saw them as related — 15 strict subset/superset pairs exist on the
    stored corpus, including SPCX ids 8597 (4 members) and 8598 (3 members), both
    HIGH and emitted in the SAME run.
    """
    return f"{FP_PREFIX}{'+'.join(sorted(members))}::{ticker}::{direction}"


def _fingerprint_ticker(fp: str | None) -> str | None:
    """The symbol embedded in a fingerprint: RULE02::members::TICKER::direction.

    ⚠️ Load-bearing, and the reason this exists rather than narrowing the dedup query
    on the stored ticker. After the ticker-resolution fix every unvalidated cluster
    stores `ticker=''`, so a ticker-narrowed lookup lumps ALL of them — of every
    company — into one namespace, and an identity test that compares member sets
    would dedup two DIFFERENT companies against each other. RULE_CLUSTER hit exactly
    this and fixed it the same way (`rule_cluster.py:165-168`).
    """
    parts = (fp or "").split("::")
    if len(parts) < 4:
        return None
    # rsplit-style: everything between the member segment and the direction. A
    # symbol containing "::" would desynchronise a fixed parts[2] — the verifier
    # planted "X::B" and made a DIFFERENT company dedup against it. No such row
    # exists today (0 transactions carry ':'), but the guard costs one line.
    return "::".join(parts[2:-1]) or None


def _extract_fingerprint(why: str | None) -> str | None:
    """Pull the fingerprint back out of `why_matters`.

    `why_matters` accumulates — the directional and ticker remaps both append to it —
    so this anchors on the marker and reads to the next whitespace rather than
    assuming position.
    """
    text = why or ""
    i = text.find(IDENTITY_MARKER + FP_PREFIX)
    if i < 0:
        return None
    token = text[i + len(IDENTITY_MARKER):].split()[0]
    return token.rstrip(".") or None


def _fingerprint_parts(fp: str) -> tuple[set, str | None]:
    """(member set, direction) from a fingerprint. The symbol is the ticker parser's."""
    parts = fp.split("::")
    if len(parts) < 4:
        return set(), None
    members = {m for m in parts[1].split("+") if m}
    return members, parts[-1]


def _prior_alerts(conn, group_ticker: str, days: int) -> list[dict]:
    """Recent non-superseded RULE_02 alerts for THIS cluster's symbol.

    Two changes from the `alert_exists` this replaces:

    * The lookback is `days` — the caller's `--days` scan window — not a hardcoded
      7. The old 7-day window was 13x shorter than the 90-day default scan, so the
      identical AAPL member set re-fired on 2026-06-17, 07-09 and 07-20.
    * Identity comes from the fingerprint recorded in `why_matters`, not from the
      headline string. It is NOT in `detail` — see IDENTITY_MARKER for why.

    Rows are matched on the fingerprint's OWN symbol, never on the stored ticker —
    see `_fingerprint_ticker`.
    """
    rows = conn.execute(
        f"""
        SELECT id, why_matters FROM alerts
        WHERE rule = ?
          AND why_matters LIKE ?
          AND COALESCE(lifecycle_stage,'') != 'superseded'
          AND datetime(created_at) >= datetime('now', '-{int(days)} days')
        """,
        (RULE, f"%{IDENTITY_MARKER}{FP_PREFIX}%"),
    ).fetchall()
    out = []
    for r in rows:
        fp = _extract_fingerprint(r["why_matters"])
        if not fp:
            continue
        if _fingerprint_ticker(fp) != group_ticker:
            continue          # a different company sharing the blank key
        members, direction = _fingerprint_parts(fp)
        out.append({"id": r["id"], "members": members, "direction": direction})
    return out


def legacy_alert_exists(conn, ticker: str, headline: str, days: int) -> bool:
    """The pre-fingerprint dedup, kept for rows written before this change.

    ⚠️ Every RULE_02 alert stored today predates the fingerprint, so `_prior_alerts`
    cannot see any of them. Without this fallback the first run after deploy would
    re-emit the whole corpus. Widened from 7 days to the scan window, which on its
    own also closes the AAPL refire for legacy rows.

    ⚠️⚠️ Scoping this to rows WITHOUT an identity is NOT optional. Headline identity is the very defect
    this change removes: the headline carries only the member COUNT, so two GENUINELY
    DISTINCT 3-member clusters on one ticker share a headline exactly. Left
    unscoped, this fallback suppressed the second one — reintroducing the blindness
    the fingerprint exists to fix, on a path no fingerprint test would cover.
    Restricting it to rows that carry no identity means legacy rows keep their old
    protection while anything this rule writes is judged on its member set.
    """
    row = conn.execute(
        f"""
        SELECT 1 FROM alerts
        WHERE rule = ?
          AND ticker = ?
          AND headline = ?
          AND COALESCE(why_matters,'') NOT LIKE ?
          AND datetime(created_at) >= datetime('now', '-{int(days)} days')
        LIMIT 1
        """,
        (RULE, ticker, headline, f"%{IDENTITY_MARKER}{FP_PREFIX}%"),
    ).fetchone()
    return row is not None


#: Appended to `why_matters` when the cluster's symbol did not resolve. Mirrors
#: rule_01b_first_touch's wording so the two rules' triage rows read alike.
UNRESOLVED_FLAG = "[UNVERIFIED->no corroboration] symbol not in `tickers`: "

#: Identity marker inside `why_matters`, mirroring rule_cluster's "Identity {fp}".
#:
#: ⚠️ NOT `detail`. An earlier draft stored the identity there as JSON, which is
#: machine-friendly and would have put raw JSON in front of users: `alerts.detail`
#: is treated as PROSE by `api/receipts.py::_generic`, `api/static/congress.html`
#: (which fetches rule=RULE_02 specifically), `scripts/telegram_bot.py` and
#: `scripts/generate_brief.py`. RULE_CLUSTER escapes that only because it has a
#: dedicated receipt builder; RULE_02 has none. `why_matters` already carries
#: RULE_02 prose, and the fingerprint embeds the member set, symbol and direction,
#: so nothing else needs storing.
IDENTITY_MARKER = "Identity "


def emit_alerts(conn, clusters: list[dict], days: int = 90) -> int:
    emitted = 0

    # LARGEST FIRST, mirroring `rule_cluster.py`'s `candidates.sort(...)`. The
    # superset rule below can only suppress a shrunk view if the full one is already
    # on record, so emission order decides the outcome; sorting makes it
    # order-independent within a run. This is what stops SPCX 8597/8598 — both
    # emitted in the SAME run, from two overlapping anchor windows.
    clusters = sorted(clusters, key=lambda c: c.get("member_count", 0), reverse=True)

    for cluster in clusters:
        resolved = cluster.get("resolved", True)

        # An unresolved symbol is NOT a corroboration key. Storing '' rather than
        # the parse string is what actually removes it from the gate:
        # rule_10_corroboration._candidate_alerts requires `ticker != ''`. The
        # alert is still emitted and the raw symbol is preserved below — absence
        # from `tickers` is a coverage gap for a human to triage, not evidence
        # that the symbol is fake (a listed company can be missing from the table).
        stored_ticker = cluster["ticker"] if resolved else ""
        lifecycle = "created" if resolved else "review"
        why_matters = None if resolved else UNRESOLVED_FLAG + repr(cluster["ticker"])

        # Legacy rows carry no `detail`, so the fingerprint cannot see them.
        # ⚠️ The LEGACY headline, not the new one. Every stored RULE_02 row
        # predates both the fingerprint and the cross-partisan headline, so this
        # probe has to ask the question in the old string's words or it matches
        # nothing and re-emits the corpus. Semantics are unchanged: same rows,
        # same window, same scoping — only the key is kept in its original form.
        # `.get`, not `[...]`: `emit_alerts` accepts any cluster mapping, and a
        # required new key would break every caller that builds one by hand —
        # which is a real contract, not just a test convenience. Absent the key,
        # fall back to `headline` and behave exactly as before this change.
        legacy_key = cluster.get("legacy_headline") or cluster["headline"]
        if legacy_alert_exists(conn, stored_ticker, legacy_key, days):
            continue

        direction = cluster["net_direction"]
        member_ids = sorted(cluster.get("members") or [])
        cur_set = set(member_ids)
        # Derive identity when the caller did not supply it, rather than requiring
        # it. `find_clusters` always does, but a hand-built cluster dict should not
        # crash the emitter — and a memberless dict then degrades to
        # (ticker, direction) identity, which is all it can honestly assert.
        fingerprint = cluster.get("fingerprint") or _fingerprint(
            member_ids, cluster["ticker"], direction
        )
        prior = _prior_alerts(conn, cluster["ticker"], days)

        # RULE_CLUSTER's identity semantics, in its order.
        if any(p["members"] == cur_set and p["direction"] == direction for p in prior):
            continue          # same members, same direction — already alerted
        if any(p["members"] > cur_set for p in prior):
            # A superset is already on record. This is a shrunk view of the same
            # signal, not a second one.
            #
            # ⚠️ Deliberately not direction-aware, matching rule_cluster.py. A
            # 2-member NET_LONG subset of a 4-member NET_SHORT window IS suppressed
            # (real case: MSFT id 44 within id 46). Overlapping windows on one
            # ticker are slices of one cluster, so the fuller read wins.
            continue
        superseded = [p for p in prior if p["members"] and p["members"] < cur_set]

        # Identity rides in `why_matters`, appended after any unresolved flag.
        why_matters = " ".join(
            x for x in ((why_matters or ""), f"{IDENTITY_MARKER}{fingerprint}") if x
        ).strip()

        conn.execute(
            """
            INSERT INTO alerts (rule, headline, severity, tags, ticker,
                                lifecycle_stage, why_matters)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                RULE,
                cluster["headline"],
                cluster["severity"],
                cluster["tags"],
                stored_ticker,
                lifecycle,
                why_matters,
            ),
        )
        for p in superseded:
            # SUPERSEDED, not deleted — an expanded member set replaces the smaller
            # alert on record rather than silently dropping it.
            conn.execute(
                "UPDATE alerts SET lifecycle_stage='superseded' WHERE id=?", (p["id"],)
            )
        emitted += 1
    conn.commit()
    return emitted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect congressional trading clusters (RULE_02)."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Lookback window in days. Default: 90.",
    )
    parser.add_argument(
        "--min-members",
        type=int,
        default=3,
        help="Minimum distinct members to form a cluster. Default: 3.",
    )
    # Accepted (and ignored) for scheduler-runner uniformity — the scheduler
    # invokes every job with --emit-alerts; without this, argparse would reject it.
    parser.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> None:
    import time as _time
    parser = build_parser()
    args = parser.parse_args()

    _t0 = _time.time()
    with db_connection() as conn:
        transactions = fetch_transactions(conn, args.days)
        clusters = find_clusters(transactions, args.min_members)
        emitted = emit_alerts(conn, clusters, args.days)

    print(f"{len(clusters)} clusters found, {emitted} alerts emitted")
    from jpt_common import record_activity
    record_activity("RULE_02", scanned=len(transactions), flagged=len(clusters),
                    emitted=emitted, duration_seconds=round(_time.time() - _t0, 2))


if __name__ == "__main__":
    main()
