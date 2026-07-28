#!/usr/bin/env python3
"""INSIDER CLUSTERS — a discovery surface, never a gate leg.

Two or more DISTINCT insiders making open-market buys in the same small-cap inside a
short window. It emits no alert, carries no confidence, and adds no instrument to the
convergence gate. A human reads it, so it FAILS CLOSED: anything it cannot identify with
confidence is dropped, not guessed.

════════════════════════════════════════════════════════════════════════════════════
WHY THIS MODULE WAS REWRITTEN, AND WHAT THE REWRITE ACTUALLY CHANGES
════════════════════════════════════════════════════════════════════════════════════

Six certification passes on the predecessor found FOURTEEN "keys". They were not
fourteen bugs. They were ONE bug fourteen times:

    Several keys each independently reconstructed "who is one insider" from a
    DIFFERENT PROJECTION of a row, and each projection was blind to a different column.

Each patch fixed the projection in front of it and left (or created) another. One round's
fix was itself a new instance and silently deleted a real $300,000 cluster. Patching the
keys was never going to converge, because the keys were not the problem — HAVING SEVERAL
OF THEM WAS.

So there is now exactly ONE definition of identity, `person_partition`, and nothing else
may invent its own. The traps below are not "fixed". They are UNREPRESENTABLE: there is
no way to express them, because there is nothing left to express them WITH.

    K2   an owner with an empty CIK escaping the union
    K9   `0001234567` and `1234567` counted as two people
    K10  'ABCD' and 'abcd' forming no cluster
    K12  the published name flipping between processes (set iteration + PYTHONHASHSEED)
    K13  the union's input set being window- and ticker-scoped, while the anchor loop
         MAXIMISES member count — so the search actively selected the window in which a
         household splits
    K14  the trade key's insider being `MIN(insider_cik)`, an aggregate over a filing's
         owner list, which MOVED when that list moved

THE STRUCTURAL CONSEQUENCE THAT DOES THE REAL WORK. Because every accession's owner CIKs
are unioned together, EVERY ACCESSION MAPS TO EXACTLY ONE `person_id`. "A joint filing
counts as one insider" stops being a rule that can be got wrong — there is no way to
express anything else. K13 and K14 fall out of the same fact: the id cannot depend on
which filings are in the window, nor on which owners a filing happens to list.

⚠️ THIS MODULE READS `form4_transactions` READ-ONLY. It does not write to the store.

⚠️ "READ-ONLY" MEANS SCOPE'S DATA, NOT THE DATABASE. A scan warms two caches —
`insider_meta` here, and `ticker_meta` transitively through `classify_cap`. It writes no
alert, theme, or transaction. That distinction was got wrong before and is stated plainly
rather than left to be rediscovered.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import normalize_ticker  # noqa: E402
from scripts.rule_reddit_collector import classify_cap  # noqa: E402

# ── tunables — none of these are measured, they are judgement ────────────────
WINDOW_DAYS = 7               # on the TRADE date, not the filing date
MIN_INSIDERS = 2
SCAN_HORIZON_DAYS = 45        # PTR/Form 4 disclosure lag; the window slides inside this
LARGE_CAP_MIN = 10_000_000_000
CIK_TTL_DAYS = 90

# A PRESENT BUT MEANINGLESS SYMBOL. Funds without a ticker file the literal string "NA",
# and three unrelated filers once formed a pseudo-cluster on it worth $19.98. The store
# correctly drops an ABSENT symbol; a present-but-meaningless one got through.
_SENTINEL_TICKERS = ("NA", "N/A", "NONE", "NULL", "-")

# Forms a natural person CANNOT file. NOT a general "institutional" list — `SC 13D`/`SC
# 13G` are deliberately ABSENT: founders, activists and 10% owners file them constantly,
# and including them produced +1 correct institution against ~36 FALSE ones on a
# 330-owner day, stripping 68% of the dollars from the one real cluster by reclassifying
# a DIRECTOR as an institution. The `sic` heuristic is gone for the same reason — EDGAR
# assigns some individuals their issuer's SIC.
_INSTITUTIONAL_FORMS = {
    "13F-HR", "13F-HR/A", "13F-NT", "13F-NT/A", "N-PX", "N-CSR", "N-CSRS", "N-Q",
    "10-K", "10-Q", "20-F", "40-F", "ADV", "ADV-E", "X-17A-5",
}
_SEC_HEADERS = {"User-Agent": "Scope research sloppysecondstbb@gmail.com"}

# A usable CIK: optional padding, then 1-10 significant digits. NOT all-zero, NOT blank,
# NOT alphabetic. See `_norm_cik` for why the "or 0" fallback is a corpus-merging bug.
_CIK_RE = re.compile(r"0*[1-9]\d{0,9}")


def ensure_tables(conn) -> None:
    """The identity cache. Additive only.

    Called from `find_clusters`, NOT only from tests. The predecessor created this table
    only in its test file, so on any real database the first identity lookup raised
    `no such table`, the router flattened it to `count: 0`, and a SCHEMA FAILURE became
    indistinguishable from an honest quiet day — on a surface whose entire claim is that
    its zeros are honest.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS insider_meta (
            cik          TEXT PRIMARY KEY,
            kind         TEXT,      -- person | institution | unknown
            entity_type  TEXT,
            sic          TEXT,
            evidence     TEXT,
            resolved_at  TEXT
        )""")
    conn.commit()


# ════════════════════════════════════════════════════════════════════════════
#  THE SINGLE IDENTITY AUTHORITY
#
#  Everything below this banner keys on `person_id`. Nothing else in this module
#  may derive identity from a raw CIK, a name, or an aggregate.
# ════════════════════════════════════════════════════════════════════════════

def _norm_cik(cik: str | None) -> str:
    """The ONE place a CIK becomes comparable (K9). Returns "" for anything UNUSABLE.

    EDGAR pads inconsistently, so `0001234567` and `1234567` are one filer. The
    predecessor normalised at some sites and not others, so the same human counted twice.
    Here there is one site to get right, because there is one key.

    ⚠️ AN UNUSABLE CIK IS ABSENT, NOT A NAMESPACE. The first draft of this rewrite ended
    `... .lstrip("0") or "0"` — the idiom used elsewhere in this codebase — which turns a
    PLACEHOLDER all-zero CIK into the truthy node "0". Under a GLOBAL union-find that is
    catastrophic rather than local: every accession carrying a zero-CIK owner unions into
    one component, so two unrelated filers become one person and a real cluster is
    deleted. That is K2's exact shape — a sentinel value in an identity column treated as
    a real identity — and this rewrite reintroduced it on day one. Caught by an
    adversarial review, and pinned by `test_an_UNUSABLE_cik_is_absent_not_a_namespace`.

    Usable is `0*[1-9]digits`, at most 10 significant digits. Everything else — empty,
    all-zero, non-numeric, over-long — is absent.
    """
    if not cik:
        return ""
    raw = str(cik).strip()
    return raw.lstrip("0") if _CIK_RE.fullmatch(raw) else ""


def person_partition(conn) -> dict[str, tuple[str, ...]]:
    """cik -> person_id, over the WHOLE corpus. THE definition of "one insider".

    Union-find over every co-filing relationship in `form4_transactions`: each accession
    unions all of its owner CIKs. A connected component is one economic actor — a
    household, or an asset manager's chain of entities.

    ⚠️ NO FILTERS OF ANY KIND. Not by `txn_code`, `ticker`, `is_derivative`, `is_10b5_1`,
    date, or window. THIS IS THE ENTIRE POINT, and it is what kills K13: whether two
    people are one household is a property OF THE PEOPLE, not of the rows that happen to
    fall inside the window being scored. The predecessor scoped this input set to
    open-market buys on one ticker in one window — and because the anchor loop maximises
    member count, the search ACTIVELY SELECTED the window in which a household splits.
    A bridging filing that was earlier, or a non-buy, or on another ticker, was invisible.

    ⚠️ `person_id` IS THE IDENTITY CLASS ITSELF — `tuple(sorted(component))` — not a
    label computed from it. K14 was `MIN(insider_cik)`, an aggregate over A FILING'S
    OWNER LIST, so it MOVED when that list moved: a 4/A that added a family trust changed
    the representative and broke dedupe. A component is a property of the whole corpus,
    and using the component AS the key means there is no aggregate left to move.

    An owner with an EMPTY CIK is not a node (K2). It must not be one: every CIK-less
    owner in the corpus would otherwise union into a single component and merge every
    filer who ever filed without a CIK. Such an owner contributes nothing and is dropped
    by the fail-closed identity gate, which is where that case belongs.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:          # path compression, iterative
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # Deterministic merge direction: the lexicographically smaller root wins.
            # Union-by-size would depend on insertion order, which is exactly the class
            # of nondeterminism K12 was about.
            lo, hi = (ra, rb) if ra < rb else (rb, ra)
            parent[hi] = lo

    # ORDER BY makes the traversal reproducible. Combined with the deterministic merge
    # direction above, the resulting partition is byte-identical run to run and process
    # to process — never dependent on set iteration or PYTHONHASHSEED (K12).
    by_accession: dict[str, list[str]] = {}
    for acc, cik in conn.execute(
            "SELECT DISTINCT accession, insider_cik FROM form4_transactions "
            "ORDER BY accession, insider_cik"):
        norm = _norm_cik(cik)
        if not norm:
            continue
        parent.setdefault(norm, norm)
        by_accession.setdefault(acc, []).append(norm)

    for acc in sorted(by_accession):
        owners = sorted(set(by_accession[acc]))
        for other in owners[1:]:
            union(owners[0], other)

    components: dict[str, list[str]] = {}
    for cik in sorted(parent):
        components.setdefault(find(cik), []).append(cik)

    return {cik: tuple(sorted(members))
            for members in components.values() for cik in members}


def accession_persons(conn, partition: dict[str, tuple[str, ...]]
                      ) -> dict[str, tuple[tuple[str, ...], ...]]:
    """accession -> its canonical owner-set id: the sorted set of owner `person_id`s.

    ⚠️ THIS IS ALWAYS A ONE-TUPLE, by construction — `person_partition` unions every
    accession's owners, so they are necessarily in one component. It is built and
    asserted as a SET anyway, because that is what makes the property checkable rather
    than assumed: `test_every_accession_maps_to_exactly_one_person_id` fails loudly if
    the partition ever stops guaranteeing it.

    An accession whose owners ALL lack a CIK maps to the empty tuple — unidentifiable,
    and dropped downstream. Fail closed.
    """
    out: dict[str, set] = {}
    for acc, cik in conn.execute(
            "SELECT DISTINCT accession, insider_cik FROM form4_transactions "
            "ORDER BY accession, insider_cik"):
        norm = _norm_cik(cik)
        out.setdefault(acc, set())
        if norm and norm in partition:
            out[acc].add(partition[norm])
    return {acc: tuple(sorted(pids)) for acc, pids in out.items()}


def _classify_from_submissions(j: dict) -> tuple[str, str]:
    """(kind, evidence) from an SEC submissions document. Pure; no I/O.

    ⚠️ `entityType` CANNOT DISCRIMINATE, and a brief that specified it was wrong.
    Measured over a 330-owner census: `other` covers 263 persons AND 61 institutions —
    Jennifer Newstead (a natural person) and Citadel Advisors LLC are both `other`, while
    BlackRock Finance is `operating`. Building on it would have shipped a lookup that
    called every institution a person, carrying an SEC field's authority.

    What survives is the forms a natural person cannot file.
    """
    recent = (j.get("filings") or {}).get("recent") or {}
    forms = set(recent.get("form") or [])
    hits = sorted(forms & _INSTITUTIONAL_FORMS)
    if hits:
        return "institution", f"files {','.join(hits[:4])}"
    if (j.get("entityType") or "").lower() == "operating":
        return "institution", "entityType=operating"
    if forms:
        return "person", f"ownership-only forms ({','.join(sorted(forms)[:4])})"
    return "unknown", "no filing history"


def resolve_insider_kind(conn, cik: str, cache: bool = True) -> str:
    """'person' | 'institution' | 'unknown'. FAILS CLOSED in code, not only in docs."""
    if not cik:
        return "unknown"
    key = _norm_cik(cik)
    if not key:
        return "unknown"

    row = conn.execute("SELECT kind, resolved_at FROM insider_meta WHERE cik = ?",
                       (key,)).fetchone()
    if row and row[1]:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row[1]).replace(
                tzinfo=timezone.utc)
            if age < timedelta(days=CIK_TTL_DAYS):
                return row[0]
        except (TypeError, ValueError):
            pass

    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{key.zfill(10)}.json",
                         headers=_SEC_HEADERS, timeout=15)
        if not r.ok:
            # NOT cached. A 429 is a rate limit, not a verdict — caching it would freeze
            # a real person as `unknown` for the whole TTL.
            return "unknown"
        kind, evidence = _classify_from_submissions(r.json())
        entity_type, sic = r.json().get("entityType", ""), r.json().get("sic", "")
    except Exception:
        return "unknown"

    if cache:
        conn.execute(
            "INSERT INTO insider_meta (cik, kind, entity_type, sic, evidence, "
            "resolved_at) VALUES (?,?,?,?,?,?) ON CONFLICT(cik) DO UPDATE SET "
            "kind=excluded.kind, entity_type=excluded.entity_type, sic=excluded.sic, "
            "evidence=excluded.evidence, resolved_at=excluded.resolved_at",
            (key, kind, entity_type, sic, evidence,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    return kind


def person_kind(conn, person_id: tuple[str, ...], store_kinds: dict[str, set],
                resolve) -> str:
    """Is this identity class a natural-person insider? FAIL CLOSED.

    A component legitimately MIXES kinds — a director and their family trust are one
    actor, and the trust's name carries an entity token. So:

        ANY member resolves 'person'   -> person       (a real insider)
        else all members institution   -> institution  (not an insider)
        else                           -> unknown      -> NOT COUNTED

    The store's own name-token verdict is consulted FIRST, so an obvious institution
    costs no network call. It is a HARD signal for `institution` and only a DEFAULT for
    `person` (its own docstring measures it missing ~31 of 33 suffix-less managers), so
    a store 'person' is still confirmed against SEC rather than trusted.
    """
    saw_institution = False
    for cik in person_id:                      # already sorted; deterministic
        if store_kinds.get(cik) == {"institution"}:
            saw_institution = True
            continue
        kind = resolve(conn, cik)
        if kind == "person":
            return "person"
        if kind == "institution":
            saw_institution = True
        else:
            return "unknown"                   # fail closed on any unresolved member
    return "institution" if saw_institution else "unknown"


# ════════════════════════════════════════════════════════════════════════════
#  Everything below consumes person_id. None of it derives identity.
# ════════════════════════════════════════════════════════════════════════════

def _buy_rows(conn, horizon_days: int) -> list[dict]:
    """One row per (accession, txn_index) — a TRANSACTION, not an owner-row.

    The store writes a full cartesian product: a filing with N owners and M transactions
    is N*M rows, with the transaction fields duplicated VERBATIM across owners. So
    summing `value` without collapsing multiplies a joint filing's dollars by N.

    ⚠️ NO OWNER-SCOPED COLUMN MAY APPEAR IN THIS PROJECTION. Not `insider_cik`, not
    `insider_name`, and NOT `insider_kind` either. The predecessor carried
    `MIN(insider_cik)` here — an aggregate over the filing's owner list, which moved when
    that list moved (K14). Any owner-scoped column has the same defect: `MIN(insider_kind)`
    would read 'institution' whenever ANY owner is one, so a 4/A adding a family trust
    would silently drop the leg. K14 with a different column substituted in.

    ⚠️ AND THERE IS NO AGGREGATE AT ALL. `SELECT DISTINCT` rather than `GROUP BY` with
    MIN/MAX: every selected column is filing- or transaction-level, written VERBATIM
    IDENTICAL across the owner rows, so DISTINCT collapses them to exactly one row. If
    that assumption were ever false, DISTINCT yields TWO rows and the anomaly is loud —
    where MIN/MAX would silently pick one and look correct. The property is checked by
    `test_the_projection_yields_exactly_one_row_per_transaction`, not assumed.
    """
    sql = f"""
        SELECT DISTINCT accession, txn_index, issuer_cik, ticker, txn_date, filing_date,
               shares, value, COALESCE(is_10b5_1, -1) is_10b5_1
        FROM form4_transactions
        WHERE txn_code = 'P' AND acquired_disposed = 'A'
          AND is_derivative = 0
          AND UPPER(TRIM(ticker)) NOT IN ({','.join('?' * len(_SENTINEL_TICKERS))})
          AND COALESCE(is_10b5_1, 0) != 1
          AND date(txn_date) >= date('now', '-{int(horizon_days)} days')
        ORDER BY accession, txn_index
    """
    return [dict(zip(("accession", "txn_index", "issuer_cik", "ticker", "txn_date",
                      "filing_date", "shares", "value", "is_10b5_1"), r))
            for r in conn.execute(sql, _SENTINEL_TICKERS)]


def _display_names(conn) -> dict[str, dict]:
    """cik -> a display record. Presentation only; NEVER an identity key.

    CORPUS-SCOPED and ordered BY CIK, deliberately. One CIK carries several name
    spellings over time, so a window-scoped choice would render the same person
    differently in two clusters and a human could not tell they were one insider — K13
    and K12 on the label axis. Ordering by `insider_cik, insider_name` makes the pick a
    function of the corpus alone; an earlier draft ordered by `owner_seq` first, which
    made the name depend on where the person happened to sit in a filing.
    """
    out: dict[str, dict] = {}
    for cik, name, norm, title in conn.execute(
            "SELECT DISTINCT insider_cik, insider_name, insider_name_norm, "
            "officer_title FROM form4_transactions "
            "ORDER BY insider_cik, insider_name, officer_title"):
        key = _norm_cik(cik)
        if key and key not in out:
            out[key] = {"cik": key, "name": name or "", "norm": norm or "",
                        "title": title or ""}
    return out


def issuer_labels(conn) -> dict[str, str]:
    """issuer_cik -> the ticker to DISPLAY and to price. Deterministic.

    THE FIFTEENTH IDENTITY SITE, found by an adversarial review of this rewrite. The
    module fixed "who is one insider" and left "which company is this" answered by a
    LABEL — the free-text `issuerTradingSymbol` a filing agent types — while
    `issuer_cik`, the issuer's actual identity, sat unused in the same table.

    Two live failures from grouping on the string: a symbol written `BRK-B` on one filing
    and `BRK.B` on another splits one company into two groups (K10, surviving its own
    fix); and a ticker RENAME mid-horizon splits a cluster while a ticker REUSE merges two
    unrelated small caps into a false one. `issuer_cik` answers both exactly.

    The label is the ticker from the most recent filing, tie-broken by sort — never set
    order.
    """
    out: dict[str, str] = {}
    for issuer, ticker, _fd in conn.execute(
            "SELECT DISTINCT issuer_cik, ticker, filing_date FROM form4_transactions "
            "WHERE TRIM(COALESCE(issuer_cik,'')) != '' "
            "ORDER BY issuer_cik, filing_date DESC, ticker"):
        # First row per issuer is its most recent filing_date; the ORDER BY's trailing
        # `ticker` makes a same-day tie deterministic rather than row-order.
        out.setdefault(str(issuer).strip(), (ticker or "").strip())
    return out


def _store_kinds(conn) -> dict[str, set]:
    out: dict[str, set] = {}
    for cik, kind in conn.execute(
            "SELECT DISTINCT insider_cik, insider_kind FROM form4_transactions "
            "ORDER BY insider_cik, insider_kind"):
        key = _norm_cik(cik)
        if key:
            out.setdefault(key, set()).add(kind)
    return out


def corpus_meta(conn, horizon_days: int = SCAN_HORIZON_DAYS) -> dict:
    """What the counts on this surface actually MEAN. Published, not implied.

    - `corpus_start` — K13's fix is "global over the INGESTED corpus", not global. A
      bridging joint filing that predates ingestion is invisible, so a household can
      still over-split. The reader cannot judge a count without knowing this date.
    - `horizon_start` — the scan uses `date('now')`, so two runs either side of midnight
      see different data. Resolving it here makes what a human read re-derivable.
    - `rows_without_issuer_cik` — how often the issuer fell back to its ticker STRING.
    """
    row = conn.execute(
        "SELECT MIN(filing_date), MAX(filing_date), COUNT(DISTINCT accession) "
        "FROM form4_transactions").fetchone()
    missing = conn.execute(
        "SELECT COUNT(*) FROM form4_transactions "
        "WHERE TRIM(COALESCE(issuer_cik,'')) = ''").fetchone()[0]
    return {
        "corpus_start": row[0], "corpus_end": row[1], "accessions": row[2] or 0,
        "rows_without_issuer_cik": missing,
        "horizon_start": (date.today() - timedelta(days=horizon_days)).isoformat(),
        "note": ("Identity is global over the INGESTED corpus only. A co-filing that "
                 "predates corpus_start cannot link two filers, so a household may "
                 "still appear as two insiders."),
    }


def find_clusters(conn, window_days: int = WINDOW_DAYS,
                  min_insiders: int = MIN_INSIDERS,
                  horizon_days: int = SCAN_HORIZON_DAYS,
                  resolve=None, cap_fn=None) -> list[dict]:
    """2+ distinct `person_id`s buying the same small-cap inside `window_days`."""
    ensure_tables(conn)
    resolve = resolve or resolve_insider_kind
    cap_fn = cap_fn or classify_cap

    partition = person_partition(conn)              # ONE identity, computed ONCE
    persons_of = accession_persons(conn, partition)
    names = _display_names(conn)
    store_kinds = _store_kinds(conn)
    kind_cache: dict[tuple, str] = {}

    def kind_of(pid: tuple) -> str:
        if pid not in kind_cache:
            kind_cache[pid] = person_kind(conn, pid, store_kinds, resolve)
        return kind_cache[pid]

    rows = _buy_rows(conn, horizon_days)

    # GROUPED ON `issuer_cik` — the issuer's IDENTITY — not on the ticker string, which
    # is free text a filing agent types. See `issuer_labels`. A filing with no issuer CIK
    # falls back to the normalised ticker rather than being silently dropped; that
    # fallback is counted and published as `issuer_cik_missing`.
    labels = issuer_labels(conn)
    by_issuer: dict[str, list[dict]] = {}
    for r in rows:
        issuer = str(r["issuer_cik"] or "").strip()
        if not issuer:
            issuer = "ticker:" + (normalize_ticker(r["ticker"])
                                  or (r["ticker"] or "").strip().upper())
        by_issuer.setdefault(issuer, []).append(r)

    out: list[dict] = []
    for issuer in sorted(by_issuer):
        buys = by_issuer[issuer]
        ticker = labels.get(issuer) or issuer.removeprefix("ticker:")
        ticker = normalize_ticker(ticker) or ticker
        # Cheap reject on DISTINCT PERSONS, not on accession count: two filings by one
        # household are not two insiders, and counting accessions here would let the
        # expensive path run on shapes that can never qualify.
        if len({persons_of.get(b["accession"], ()) for b in buys
                if persons_of.get(b["accession"])}) < min_insiders:
            continue

        best = None
        for anchor_row in sorted(buys, key=lambda b: (b["txn_date"], b["accession"])):
            start = date.fromisoformat(anchor_row["txn_date"])
            end = start + timedelta(days=window_days)   # half-open: exactly window_days
            window = [b for b in buys
                      if start <= date.fromisoformat(b["txn_date"]) < end]

            members: dict[tuple, dict] = {}
            counted: set = set()
            total = 0.0
            dropped_unidentified = dropped_institution = dropped_unresolved = 0
            undisclosed_trades = zero_value_trades = 0

            for b in sorted(window, key=lambda b: (b["accession"], b["txn_index"])):
                pids = persons_of.get(b["accession"], ())
                if not pids:
                    dropped_unidentified += 1      # no usable owner CIK -> fail closed
                    continue
                kinds = {p: kind_of(p) for p in pids}
                live = [p for p in pids if kinds[p] == "person"]
                if not live:
                    # SPLIT ON PURPOSE. "Resolved, and not a person" and "could not
                    # resolve" are different facts, and collapsing them hid a real
                    # nondeterminism: `resolve_insider_kind` returns `unknown` on a 429
                    # and deliberately does not cache it, so a rate-limited worker
                    # silently publishes FEWER insiders than an unthrottled one. A
                    # cluster whose membership turned on an unresolved lookup is
                    # provisional, and now says so.
                    if any(k == "unknown" for k in kinds.values()):
                        dropped_unresolved += 1
                    else:
                        dropped_institution += 1
                    continue

                for pid in live:
                    if pid not in members:
                        members[pid] = _member_record(pid, names)

                # THE TRADE KEY. Keyed on person_id, so it cannot move when a filing's
                # owner list changes (K14). `accession` is deliberately EXCLUDED so a 4/A
                # re-filing the same trade collapses. `txn_index` is REQUIRED: one filing
                # can hold two legitimately distinct identical legs, and omitting it lost
                # $19,995 of SCTX's $84,990 — the store's own schema comment warns of it.
                key = (tuple(live), b["txn_index"], b["txn_date"],
                       b["shares"], round(b["value"] or 0.0, 2))
                if key in counted:
                    continue
                counted.add(key)
                total += b["value"] or 0.0
                # COUNTED IN TRADES, matching the dollar sum. The predecessor counted
                # this in accessions while summing in trades, so the number moved with
                # how a filing agent happened to package the legs rather than with the
                # data.
                if b["is_10b5_1"] == -1:
                    undisclosed_trades += 1
                if not (b["value"] or 0.0):
                    # A footnoted (empty) price is legitimate and the store keeps it as
                    # 0.0. Without this a cluster can honestly publish "2 insiders, $0".
                    zero_value_trades += 1

            if len(members) < min_insiders:
                continue

            # Deterministic: most insiders, then most dollars, then EARLIEST window.
            cand = (len(members), round(total, 2), -start.toordinal())
            if best is None or cand > best[0]:
                best = (cand, {
                    "ticker": ticker,
                    "issuer_cik": issuer,
                    "insiders": [members[p] for p in sorted(members)],
                    "total_value": round(total, 2),
                    "window_start": start.isoformat(),
                    "window_end": (end - timedelta(days=1)).isoformat(),
                    "accessions": len({b["accession"] for b in window
                                       if persons_of.get(b["accession"])}),
                    "undisclosed_10b5_1_trades": undisclosed_trades,
                    "zero_value_trades": zero_value_trades,
                    "dropped_unidentified": dropped_unidentified,
                    "dropped_institution": dropped_institution,
                    "dropped_unresolved": dropped_unresolved,
                    "provisional": dropped_unresolved > 0,
                })
        if not best:
            continue

        # The cap gate LAST — it is the only step that can hit a second network service,
        # and there is no point pricing a ticker that formed no cluster. FAIL CLOSED: an
        # unknown cap keeps the ticker OFF a surface a human reads. This is deliberately
        # the OPPOSITE of the reddit collector, which fails open because a missing name
        # in a lookup table is the expensive failure there.
        status, cap = cap_fn(conn, ticker)
        if status != "small" or cap is None or cap >= LARGE_CAP_MIN:
            continue
        best[1]["market_cap"] = cap
        out.append(best[1])

    # Terminal key is `issuer_cik`, so a tie can never fall through to dict insertion
    # order — i.e. to SQL row order — and decide what a human reads first.
    return sorted(out, key=lambda c: (-len(c["insiders"]), -c["total_value"],
                                      c["ticker"], c["issuer_cik"]))


def _member_record(pid: tuple[str, ...], names: dict[str, dict]) -> dict:
    """Presentation for one identity class. Deterministic; carries no identity weight.

    The published name is the first member CIK in sorted order that has one — NOT a set
    iteration (K12), where CPython's per-process hash randomisation made two uvicorn
    workers answer with different names for the same cluster.
    """
    for cik in pid:                               # already sorted
        if cik in names:
            rec = dict(names[cik])
            rec["person_id"] = list(pid)
            return rec
    return {"cik": pid[0], "name": "", "norm": "", "title": "", "person_id": list(pid)}


if __name__ == "__main__":
    from jpt_common import db_connection

    conn = db_connection()
    for c in find_clusters(conn):
        print(f"{c['ticker']:8} {len(c['insiders'])} insiders  "
              f"${c['total_value']:,.2f}  cap ${c['market_cap']:,}")
    conn.close()
