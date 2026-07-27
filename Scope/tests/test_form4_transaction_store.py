"""Per-transaction Form 4 store. INGESTION ONLY — no clustering here.

Two things will quietly wreck the cluster feature if they are wrong, so they are the
two things these tests attack hardest:

  1. PER-TRANSACTION DIRECTION. A filing with both buys and sells must NOT collapse to
     one label. RULE_06's `majority_action` does exactly that, which for a rule keyed on
     BUYING is the difference between seeing an insider and not.
  2. INSIDER IDENTITY. Three Manulife subsidiaries must not read as three insiders —
     that was the audit's one and only false cluster in 33 days of real history.

The fixture is a REAL Apple Form 4 (0001140361-26-025622), kept under tests/fixtures/,
not a hand-written approximation. Its element names are why this module exists in the
shape it does.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection  # noqa: E402
from scripts import ingest_form4_transactions as f4  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
REAL = os.path.join(FIXTURES, "form4_aapl_0001140361-26-025622.xml")


def _xml(owners_xml: str, txns_xml: str, aff: str | None = "false",
         symbol: str = "TEST") -> str:
    aff_el = f"<aff10b5One>{aff}</aff10b5One>" if aff is not None else ""
    return f"""<?xml version="1.0"?><ownershipDocument>
      <periodOfReport>2026-06-15</periodOfReport>
      <issuer><issuerCik>0000000001</issuerCik><issuerName>Test Co</issuerName>
        <issuerTradingSymbol>{symbol}</issuerTradingSymbol></issuer>
      {owners_xml}{aff_el}
      <nonDerivativeTable>{txns_xml}</nonDerivativeTable></ownershipDocument>"""


def _owner(name="Doe Jane", cik="0000000009", officer="true", title="CFO",
           director="false", tenpct="false") -> str:
    return f"""<reportingOwner><reportingOwnerId>
      <rptOwnerCik>{cik}</rptOwnerCik><rptOwnerName>{name}</rptOwnerName>
      </reportingOwnerId><reportingOwnerRelationship>
      <isDirector>{director}</isDirector><isOfficer>{officer}</isOfficer>
      <isTenPercentOwner>{tenpct}</isTenPercentOwner>
      <officerTitle>{title}</officerTitle></reportingOwnerRelationship></reportingOwner>"""


def _txn(code="P", ad="A", shares="100", price="10.00") -> str:
    price_el = (f"<transactionPricePerShare><value>{price}</value>"
                f"</transactionPricePerShare>" if price else
                "<transactionPricePerShare><footnoteId id='F1'/></transactionPricePerShare>")
    return f"""<nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-06-15</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>{shares}</value></transactionShares>
        {price_el}
        <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
      </transactionAmounts></nonDerivativeTransaction>"""


# ── the real filing: the reason this store exists ────────────────────────────

def test_the_real_filing_rule06_throws_away_entirely():
    """RULE_06 keeps 0 of these 3 transactions — option exercise + tax withholding.

    Not a signal, but it IS insider activity, and a store that drops it cannot answer
    "who transacted in this name". Also note the M rows carry an EMPTY, footnoted price,
    so RULE_06's `price > 0` filter is a real filter, not a sanity check.
    """
    xml = open(REAL, encoding="utf-8").read()
    parsed = f4.parse_transactions(xml)
    from rule_06_form4 import parse_form4

    assert len(parse_form4(xml).transactions) == 0, "fixture no longer demonstrates the gap"
    assert len(parsed["transactions"]) == 3
    codes = {(t.code, t.acquired_disposed) for t in parsed["transactions"]}
    assert codes == {("M", "A"), ("F", "D"), ("M", "D")}
    assert parsed["ticker"] == "AAPL" and parsed["issuer_cik"] == "0000320193"
    assert any(t.price == 0.0 for t in parsed["transactions"]), \
        "the footnoted empty price must be preserved as 0.0, not dropped"


def test_the_real_filing_captures_the_10b5_1_flag_and_the_owner():
    parsed = f4.parse_transactions(open(REAL, encoding="utf-8").read())
    assert parsed["is_10b5_1"] == 0, "aff10b5One='false' must read as 0, not None"
    assert len(parsed["owners"]) == 1
    o = parsed["owners"][0]
    assert o.name == "Newstead Jennifer" and o.is_officer and o.cik == "0001780525"


# ── 1. per-transaction direction — the collapse that must not happen ─────────

def test_a_MIXED_filing_keeps_buys_and_sells_SEPARATE():
    """THE test. RULE_06's majority_action would label this whole filing one way."""
    xml = _xml(_owner(), _txn("P", "A", "1000", "10") + _txn("S", "D", "5000", "10"))
    parsed = f4.parse_transactions(xml)

    dirs = [(t.code, t.acquired_disposed, t.shares) for t in parsed["transactions"]]
    assert ("P", "A", 1000.0) in dirs and ("S", "D", 5000.0) in dirs, dirs
    assert len({d[1] for d in dirs}) == 2, "the mixed filing collapsed to one direction"

    # and RULE_06 really would collapse it — the control that makes this meaningful
    from rule_06_form4 import parse_form4
    assert parse_form4(xml).majority_action == "sold", \
        "control failed: RULE_06 no longer collapses, so this test proves nothing"


def test_the_mixed_filing_lands_as_separate_ROWS():
    conn = db_connection(); f4.ensure_tables(conn)
    xml = _xml(_owner(), _txn("P", "A", "1000", "10") + _txn("S", "D", "5000", "10"))
    ref = f4.FilingRef(adsh="0000000000-26-000001", owner_cik="1", filename="form4.xml", file_date="2026-06-17")
    n = f4.store_filing(conn, ref, f4.parse_transactions(xml)); conn.commit()
    rows = conn.execute("SELECT txn_code, acquired_disposed, shares FROM "
                        "form4_transactions ORDER BY txn_index").fetchall()
    conn.close()
    assert n == 2 and len(rows) == 2
    assert [(r["txn_code"], r["acquired_disposed"]) for r in rows] == [("P", "A"), ("S", "D")]


def test_every_transaction_code_is_kept_not_just_P_and_S():
    """A store that keeps only P/S cannot answer 'who transacted', which is the
    question clustering asks before it asks 'who bought'."""
    xml = _xml(_owner(), "".join(_txn(c, "A") for c in ("P", "S", "A", "M", "F", "G")))
    codes = {t.code for t in f4.parse_transactions(xml)["transactions"]}
    assert codes == {"P", "S", "A", "M", "F", "G"}


# ── 2. the 10b5-1 flag ──────────────────────────────────────────────────────

def test_a_10b5_1_filing_is_flagged_and_a_plain_buy_is_not():
    assert f4.parse_transactions(_xml(_owner(), _txn(), aff="true"))["is_10b5_1"] == 1
    assert f4.parse_transactions(_xml(_owner(), _txn(), aff="1"))["is_10b5_1"] == 1
    assert f4.parse_transactions(_xml(_owner(), _txn(), aff="false"))["is_10b5_1"] == 0


def test_an_absent_10b5_1_element_is_NULL_not_zero():
    """Pre-2022 filings predate the checkbox. 'Not disclosed' and 'not a plan' are
    different facts, and storing 0 for both would let a later rule silently treat old
    filings as confirmed non-plan trades."""
    assert f4.parse_transactions(_xml(_owner(), _txn(), aff=None))["is_10b5_1"] is None


# ── 3. attribution: the filer's own symbol, never a guess ───────────────────

def test_a_filing_without_a_trading_symbol_is_DROPPED_never_guessed():
    """Inferring a ticker from the issuer NAME is what scored Boeing from
    `BA Credit Card Trust`. There is no fallback here by design."""
    xml = _xml(_owner(), _txn(), symbol="")
    assert f4.parse_transactions(xml) is None
    assert f4.parse_transactions(_xml(_owner(), _txn(), symbol="MOBX"))["ticker"] == "MOBX"


# ── 4. insider identity — the Manulife case ─────────────────────────────────

def test_casing_variants_normalize_to_one_identity():
    """`DUGGAN ROBERT W` and `Duggan Robert W` are one person filing twice; a rule
    counting distinct strings would call them two."""
    assert f4.normalize_insider("DUGGAN ROBERT W") == f4.normalize_insider("Duggan Robert W")
    assert f4.normalize_insider("  duggan  robert   w  ") == "DUGGAN ROBERT W"
    assert f4.normalize_insider("O'Brien, Sean P.") == "O BRIEN SEAN P"


@pytest.mark.parametrize("name", [
    "Manulife (International) Ltd",
    "Manulife (Singapore) Pte. Ltd.",
    "Manufacturers Life Insurance Co (Bermuda Branch)",
])
def test_the_MANULIFE_entities_are_flagged_institutional(name):
    """The audit's one false cluster in 33 days: three affiliated entities reading as
    three insiders. They are 10% owners, not officers — exactly the shape that fooled it."""
    assert f4.classify_insider(name, is_director=False, is_officer=False,
                               is_ten_pct=True) == "institution", name


def test_natural_persons_are_flagged_person():
    for n, d, o in [("DUGGAN ROBERT W", False, True), ("Zanganeh Mahkam", True, False),
                    ("PECK MICHAEL D", False, True), ("Newstead Jennifer", False, True)]:
        assert f4.classify_insider(n, d, o, False) == "person", n


def test_an_officer_title_beats_an_entity_token():
    """An entity cannot be an officer, so a director/officer flag is decisive. Without
    this precedence a person surnamed e.g. `Bank` would be classed institutional."""
    assert f4.classify_insider("Bank Jonathan", is_director=False, is_officer=True,
                               is_ten_pct=False) == "person"


def test_the_kind_is_stored_so_a_cluster_rule_can_use_it():
    conn = db_connection(); f4.ensure_tables(conn)
    owners = (_owner("Manulife (Singapore) Pte. Ltd.", "0000000011", "false", "",
                     tenpct="true")
              + _owner("DUGGAN ROBERT W", "0000000012", "true", "CEO"))
    ref = f4.FilingRef(adsh="0000000000-26-000002", owner_cik="1", filename="form4.xml", file_date="2026-06-17")
    f4.store_filing(conn, ref, f4.parse_transactions(_xml(owners, _txn()))); conn.commit()
    kinds = dict(conn.execute("SELECT insider_name, insider_kind FROM form4_transactions"))
    conn.close()
    assert kinds["Manulife (Singapore) Pte. Ltd."] == "institution"
    assert kinds["DUGGAN ROBERT W"] == "person"


def test_a_joint_filing_yields_one_row_PER_INSIDER_per_transaction():
    """Multiple reportingOwners on one filing are separate insiders, and a cluster rule
    counting rows-per-filing would miss the second."""
    conn = db_connection(); f4.ensure_tables(conn)
    owners = _owner("Doe Jane", "0000000021") + _owner("Roe Richard", "0000000022")
    ref = f4.FilingRef(adsh="0000000000-26-000003", owner_cik="1", filename="form4.xml", file_date="2026-06-17")
    n = f4.store_filing(conn, ref, f4.parse_transactions(
        _xml(owners, _txn("P") + _txn("S", "D")))); conn.commit()
    rows = conn.execute("SELECT COUNT(*) FROM form4_transactions").fetchone()[0]
    conn.close()
    assert n == rows == 4, "2 owners x 2 transactions must be 4 rows"


# ── 5. reliability, and no regression to RULE_06 ────────────────────────────

def test_re_storing_the_same_filing_does_not_duplicate():
    conn = db_connection(); f4.ensure_tables(conn)
    ref = f4.FilingRef(adsh="0000000000-26-000004", owner_cik="1", filename="form4.xml", file_date="2026-06-17")
    parsed = f4.parse_transactions(_xml(_owner(), _txn("P") + _txn("S", "D")))
    for _ in range(3):
        f4.store_filing(conn, ref, parsed)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM form4_transactions").fetchone()[0]
    conn.close()
    assert n == 2, f"three stores produced {n} rows — the natural key does not dedup"


def test_two_identical_transactions_in_one_filing_stay_TWO_rows():
    """Deduping on values instead of position would silently merge them."""
    conn = db_connection(); f4.ensure_tables(conn)
    ref = f4.FilingRef(adsh="0000000000-26-000005", owner_cik="1", filename="form4.xml", file_date="2026-06-17")
    f4.store_filing(conn, ref, f4.parse_transactions(
        _xml(_owner(), _txn("P", "A", "100", "10") + _txn("P", "A", "100", "10"))))
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM form4_transactions").fetchone()[0]
    conn.close()
    assert n == 2


def test_the_store_uses_its_OWN_watermark_source():
    """Sharing RULE_06's `sec_form4` source would make either rule's progress silently
    skip filings for the other."""
    import rule_06_form4 as r6
    assert f4.FILING_SOURCE == "sec_form4_txn" != r6.FILING_SOURCE


def test_RULE_06_is_byte_unchanged():
    """This store is additive. RULE_06's alerting, dedup and watermark must not move."""
    import ast, subprocess
    head = open(os.path.join(os.path.dirname(__file__), "..",
                             "rule_06_form4.py"), encoding="utf-8").read()
    base = subprocess.run(["git", "show", "main:Scope/rule_06_form4.py"],
                          capture_output=True, text=True,
                          cwd=os.path.join(os.path.dirname(__file__), "..", "..")).stdout
    if not base:
        pytest.skip("no git baseline available")
    assert ast.dump(ast.parse(head)) == ast.dump(ast.parse(base)), \
        "rule_06_form4.py changed — the store must be purely additive"


def test_the_ingest_survives_the_scheduler_flag():
    """--emit-alerts is passed to every job; an unrecognised arg exits 2."""
    assert f4.build_parser().parse_args(["--emit-alerts"]).emit_alerts is True


def test_this_session_built_NO_clustering():
    """Scope guard. Clustering is the NEXT session, built on this store.

    Checks the AST, not the text — the module docstring legitimately explains that
    clustering comes next, and a substring scan flags its own scope statement.
    """
    import ast, inspect
    tree = ast.parse(inspect.getsource(f4))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
            {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef,))} | \
            {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    for banned in ("insert_alert", "cluster", "find_clusters", "emit"):
        assert not any(banned in nm.lower() for nm in names), \
            f"a name containing {banned!r} exists in code — this session is ingestion only"

    strings = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    sql = " ".join(x.upper() for x in strings if "INSERT" in x.upper())
    assert "INTO ALERTS" not in sql, "this module writes an alert"


# ── run(): the ingestion loop, which had NO coverage at all ─────────────────
#
# Both bugs found by the first live invocation lived here: a wrong
# `search_form4_filings` signature, and an error path that wrote an activity_log row
# during a --dry-run. The parse and store were well covered; the thing that drives them
# was not — the same "tested the pieces, not the caller" gap that has recurred.

def _stub_search(monkeypatch, refs, complete=True):
    monkeypatch.setattr(f4, "search_form4_filings", lambda *a, **k: (refs, complete))


def test_run_calls_search_with_the_signature_it_actually_has():
    """The first live invocation died on `days=`. The real signature is
    (since, today, deadline) -> (refs, complete)."""
    import inspect
    from rule_06_form4 import search_form4_filings
    params = list(inspect.signature(search_form4_filings).parameters)
    assert params[:2] == ["since", "today"], params
    src = inspect.getsource(f4.run)
    assert "search_form4_filings(since, today" in src, \
        "run() is not calling the real signature"


def test_run_stores_rows_end_to_end(monkeypatch):
    ref = f4.FilingRef(adsh="0000000000-26-000090", owner_cik="1",
                       filename="form4.xml", file_date="2026-06-17")
    _stub_search(monkeypatch, [ref])
    monkeypatch.setattr(f4, "fetch_filing_xml",
                        lambda r: _xml(_owner(), _txn("P") + _txn("S", "D")))
    out = f4.run()
    assert out["parsed"] == 1 and out["rows"] == 2
    conn = db_connection()
    n = conn.execute("SELECT COUNT(*) FROM form4_transactions").fetchone()[0]
    logged = conn.execute("SELECT COUNT(*) FROM activity_log WHERE source=?",
                          (f4.RULE,)).fetchone()[0]
    conn.close()
    assert n == 2 and logged == 1, "the run must log on the success path too"


def test_a_rerun_skips_already_processed_filings(monkeypatch):
    """Incremental: the watermark plus processed_adsh must prevent re-work."""
    ref = f4.FilingRef(adsh="0000000000-26-000091", owner_cik="1",
                       filename="form4.xml", file_date="2026-06-17")
    _stub_search(monkeypatch, [ref])
    monkeypatch.setattr(f4, "fetch_filing_xml", lambda r: _xml(_owner(), _txn("P")))
    f4.run()
    second = f4.run()
    conn = db_connection()
    n = conn.execute("SELECT COUNT(*) FROM form4_transactions").fetchone()[0]
    conn.close()
    assert second["parsed"] == 0, "the second run re-parsed an already-processed filing"
    assert n == 1, f"re-run duplicated rows ({n})"


def test_a_fetch_failure_is_NOT_recorded_so_it_retries(monkeypatch):
    """A transient 429 must not permanently mark a filing processed."""
    ref = f4.FilingRef(adsh="0000000000-26-000092", owner_cik="1",
                       filename="form4.xml", file_date="2026-06-17")
    _stub_search(monkeypatch, [ref])
    monkeypatch.setattr(f4, "fetch_filing_xml", lambda r: None)
    out = f4.run()
    conn = db_connection()
    marked = conn.execute("SELECT COUNT(*) FROM filings WHERE source=? AND doc_id=?",
                          (f4.FILING_SOURCE, ref.adsh)).fetchone()[0]
    conn.close()
    assert out["fetch_failed"] == 1 and marked == 0, \
        "a fetch failure was recorded as processed and will never be retried"


def test_a_search_failure_is_LOUD_not_a_quiet_day(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("EDGAR down")
    monkeypatch.setattr(f4, "search_form4_filings", _boom)
    with pytest.raises(RuntimeError):
        f4.run()
    conn = db_connection()
    row = conn.execute("SELECT notes FROM activity_log WHERE source=? "
                       "ORDER BY id DESC LIMIT 1", (f4.RULE,)).fetchone()
    conn.close()
    assert row and "FAILED" in row["notes"], "a search failure logged nothing"


def test_a_DRY_RUN_writes_nothing_even_when_the_search_FAILS(monkeypatch):
    """The bug the first live invocation exposed: the error path called
    record_activity regardless of dry_run, so a crashed dry run still mutated the DB."""
    def _boom(*a, **k):
        raise RuntimeError("EDGAR down")
    monkeypatch.setattr(f4, "search_form4_filings", _boom)
    with pytest.raises(RuntimeError):
        f4.run(dry_run=True)
    conn = db_connection()
    logged = conn.execute("SELECT COUNT(*) FROM activity_log WHERE source=?",
                          (f4.RULE,)).fetchone()[0]
    conn.close()
    assert logged == 0, "a failed DRY RUN wrote an activity_log row"


def test_a_dry_run_stores_no_rows_and_creates_no_table(monkeypatch):
    ref = f4.FilingRef(adsh="0000000000-26-000093", owner_cik="1",
                       filename="form4.xml", file_date="2026-06-17")
    _stub_search(monkeypatch, [ref])
    monkeypatch.setattr(f4, "fetch_filing_xml", lambda r: _xml(_owner(), _txn("P")))
    out = f4.run(dry_run=True)
    conn = db_connection()
    exists = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                          "AND name='form4_transactions'").fetchone()[0]
    logged = conn.execute("SELECT COUNT(*) FROM activity_log WHERE source=?",
                          (f4.RULE,)).fetchone()[0]
    conn.close()
    assert out["rows"] == 1, "a dry run should still REPORT what it would store"
    assert exists == 0 and logged == 0
