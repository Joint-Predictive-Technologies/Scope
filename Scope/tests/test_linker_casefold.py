"""The linker's fuzzy candidate generation must be case-consistent with itself.

`resolve_by_company_name` compared the RAW-CASE description against RAW-CASE company
names, while the ambiguity guard and the final lookup both `casefold()`. That single
asymmetry discarded exact matches:

    ratio('Marsh & McLennan Companies, Inc.', 'MARSH & MCLENNAN COMPANIES, INC.') = 0.375  REJECTED
    ratio('Marsh & McLennan Companies, Inc.', 'Bausch Health Companies Inc.')     = 0.700  ACCEPTED
    casefolded:                                correct 1.000, wrong 0.667

Marsh & McLennan is in `tickers`. The linker had the perfect match and threw it away.

⚠️ REPAIR, NOT REMOVAL. `FB` -> `META` is a legitimate rename that only this fuzzy
fallback recovers; deleting difflib (the RULE_09/RULE_11 pattern) would destroy it.
That ban is for the RULES, not this human-gated ingestion step. `FUZZY_CUTOFF`,
`resolve_by_symbol` and the ambiguity guard are all untouched, and pinned below.

⚠️ MEASURED COST, recorded rather than buried. Over the full 1,422-row fuzzy-path
population this is NOT "every change an improvement" — that was true only of rows
that already resolved. It is:

    resolved        76 -> 152
    lost  (X->None)   8   every one a WRONG link correctly dropped
    new   (None->X)  84

Of those 84, the verifier scored **12 distinct descriptions / 27 rows as WRONG** — I
had said 9, and six were missing from my list (`SP OAKTREE`->Angel Oak x4,
`UNITED STATES BILLS`->USO x6, Berkshire Hills->Parke, American Smallcap->American
Well, Nuveen->Neuberger, Matthews Mutual Fund->MATW). On the rows a real run actually
touches the ledger is **~57 correct new links against 27 wrong ones, plus 1 wrong link
avoided** — roughly 2:1, not the 9:1 I first reported.

⚠️ AND THE FUNCTION BEING FIXED IS NOT THE SAME AS THE DATA BEING FIXED.
`resolve_transactions` selects `WHERE ticker_id IS NULL`, so **12 of the 13 corrected
rows are never re-examined** — including the Marsh & McLennan rows this fix is named
for, which stay written as `BHC`. Only `Regions Financial` (ticker_id NULL) is touched
by a run. Repairing the stored rows is a separate backfill.

⚠️ A SECOND, UNRECORDED LIMIT: folding makes the 2,514 duplicate company names
*reachable*, so the untouched guard abstains more often — descriptions whose top-3 are
one company repeated go **16 -> 29** (104 rows). Those were None before too, so it is
not a regression, but the right company is now being found at a high score and thrown
away. See `test_duplicate_company_names_force_an_abstention_KNOWN_LIMIT`.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import resolve_tickers as rt  # noqa: E402


# A miniature `tickers` table carrying exactly the collisions that matter.
TICKERS = [
    (1, "MMC", "MARSH & MCLENNAN COMPANIES, INC."),
    (2, "BHC", "Bausch Health Companies Inc."),
    (3, "META", "Meta Platforms, Inc."),
    (4, "A", "AGILENT TECHNOLOGIES, INC."),
    (5, "SINT", "SiNtx Technologies, Inc."),
    (6, "TANH", "TANTECH HOLDINGS LTD"),
    (7, "DLB", "Dolby Laboratories, Inc."),
    (8, "NVDA", "NVIDIA CORP"),
    # The near-ties below are load-bearing, not padding. The two corrections this
    # fix produces do NOT come from folding rejecting the wrong match — Dolby still
    # scores 0.741 either way. They come from folding lifting OTHER candidates over
    # the cutoff, which trips the (untouched) ambiguity guard into abstaining.
    # Without these rows the mechanism cannot be reproduced.
    (9, "MLAB", "Mesa Laboratories Inc /CO/"),
    (10, "BIO", "Bio-Rad Laboratories, Inc."),
    (11, "KEN", "Kenon Holdings Ltd."),
    (12, "ATH", "Athene Holding Ltd."),
]


@pytest.fixture
def maps(tmp_path):
    """Build the real maps through `load_ticker_maps`, not a hand-made dict."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tickers (id INTEGER PRIMARY KEY, symbol TEXT, company_name TEXT)")
    conn.executemany("INSERT INTO tickers VALUES (?,?,?)", TICKERS)
    sym_to_id, comp_to_id, names = rt.load_ticker_maps(conn)
    conn.close()
    return sym_to_id, comp_to_id, names


def resolve(desc, maps):
    _sym, comp_to_id, names = maps
    tid = rt.resolve_by_company_name(desc, comp_to_id, names)
    return {i: s for i, s, _ in TICKERS}.get(tid)


# --------------------------------------------------------------------------
# The control must pass, before and after
# --------------------------------------------------------------------------

def test_CONTROL_the_FB_to_META_rescue_survives(maps):
    """The reason difflib is REPAIRED and not deleted.

    `FB` -> `META` is a real rename that the exact-symbol pass cannot catch; only the
    fuzzy fallback recovers it. If this ever fails, the fallback has been removed and
    real signal is being lost.
    """
    assert resolve("Meta Platforms, Inc. - Class A", maps) == "META"


# --------------------------------------------------------------------------
# The asymmetry itself
# --------------------------------------------------------------------------

def test_an_exact_name_match_is_no_longer_discarded_on_CASE(maps):
    """The headline case: a ratio-1.0 match beaten by a 0.7 wrong one."""
    assert resolve("Marsh & McLennan Companies, Inc.", maps) == "MMC", \
        "the perfect match must win; raw-case comparison gave Bausch Health"


def test_the_ratios_themselves_show_why(maps):
    """Names the mechanism so the test above cannot be read as a coincidence."""
    import difflib
    d = "Marsh & McLennan Companies, Inc."
    right, wrong = "MARSH & MCLENNAN COMPANIES, INC.", "Bausch Health Companies Inc."
    raw_right = difflib.SequenceMatcher(None, d, right).ratio()
    raw_wrong = difflib.SequenceMatcher(None, d, wrong).ratio()
    assert raw_right < rt.FUZZY_CUTOFF <= raw_wrong, "raw-case: correct rejected, wrong accepted"
    fold_right = difflib.SequenceMatcher(None, d.casefold(), right.casefold()).ratio()
    fold_wrong = difflib.SequenceMatcher(None, d.casefold(), wrong.casefold()).ratio()
    assert fold_right > fold_wrong, "casefolded: correct wins"


@pytest.mark.parametrize("desc,expected", [
    ("MARSH & MCLENNAN COMPANIES, INC.", "MMC"),
    ("marsh & mclennan companies, inc.", "MMC"),
    ("Marsh & McLennan Companies, Inc.", "MMC"),
])
def test_resolution_is_now_case_invariant(desc, expected, maps):
    assert resolve(desc, maps) == expected


# --------------------------------------------------------------------------
# The corrections: wrong links become None
# --------------------------------------------------------------------------

@pytest.mark.parametrize("desc,was", [
    ("TENCENT HLDIGS LTD", "TANH"),                  # Tencent is not Tantech
    ("DC Laboratories, Inc. - Common", "DLB"),       # IDEXX-ish text is not Dolby
])
def test_a_wrong_fuzzy_link_now_resolves_to_nothing(desc, was, maps):
    """8 such rows are corrected on the real corpus; these are two of them.

    ⭐ THE MECHANISM IS NOT WHAT IT LOOKS LIKE, and the first version of this test got
    it wrong by using a fixture too small to show it. Folding does not reject the bad
    match on score — Dolby still scores 0.741 against "DC Laboratories" either way.
    What changes is that folding lifts OTHER names over the cutoff:

        raw   : Dolby 0.741, Core Labs 0.702            gap 0.039 -> accepts Dolby
        folded: Dolby 0.741, Mesa 0.714, Bio-Rad 0.714  gap 0.027 -> AMBIGUOUS -> None

        raw   : Tantech 0.737 alone                     -> accepts Tantech
        folded: Kenon/Itonic/Athene all 0.757           gap 0.000 -> AMBIGUOUS -> None

    So the correction is produced by the UNTOUCHED ambiguity guard finally seeing the
    competition it was written for. Returning None is the right answer: the row stays
    unlinked and keys nothing.

    ⚠️ That explanation does NOT cover the third correction. `Regions Financial
    Corporation` was BBT (Beacon Financial) and is now None — but folded, the CORRECT
    company scores 0.863 and is discarded because `RF`, `RF-PC`, `RF-PE` and `RF-PF`
    share one `company_name`, so the guard reads three copies of the right answer as
    ambiguity. That is the guard MISFIRING, not working. Pinned below.
    """
    assert resolve(desc, maps) is None, f"should no longer mis-link to {was}"


# --------------------------------------------------------------------------
# The parser coupling: the SINT residual
# --------------------------------------------------------------------------

@pytest.mark.parametrize("desc", [
    "Agilent Technologies Inc (A) [ST]",
    "Agilent Technologies, Inc.",
    "Agilent Technologies Inc",
])
def test_the_SINT_residual_from_the_parser_fix_is_closed(desc, maps):
    """⭐ The cross-workstream check.

    The parser fix keeps `Agilent Technologies Inc (A)` with `raw_ticker_string=None`,
    and the raw-case linker resolved it to `SINT` (SiNtx Technologies) — an unrelated
    company acquiring a corroboration key. Casefolded, Agilent matches its own name and
    wins.
    """
    assert resolve(desc, maps) == "A", "must be Agilent, not SiNtx"


# --------------------------------------------------------------------------
# What must NOT have changed
# --------------------------------------------------------------------------

def test_the_fallback_the_cutoff_and_the_exact_pass_are_untouched():
    """Guards against a later "simplification" that deletes the fuzzy path.

    The RULE_09/RULE_11 difflib ban applies to the RULES. Here the fallback is the
    only thing that recovers a rename, and it is human-gated.
    """
    import difflib as _d
    assert rt.difflib is _d, "difflib must NOT be removed"
    assert rt.FUZZY_CUTOFF == 0.7, "the cutoff is deliberately unchanged"
    assert rt.resolve_by_symbol("NVDA", {"NVDA": 8}) == 8, "the exact pass still runs first"
    assert rt.resolve_by_symbol("nvda.", {"NVDA": 8}) == 8, "and still normalises"


def test_an_unconfident_match_still_returns_None(maps):
    assert resolve("Completely Unrelated Widget Manufacturing Co-operative", maps) is None


def test_the_ambiguity_guard_still_fires(maps):
    """Two near-identical candidates must still collapse to None, not pick one."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tickers (id INTEGER PRIMARY KEY, symbol TEXT, company_name TEXT)")
    conn.executemany("INSERT INTO tickers VALUES (?,?,?)", [
        (1, "AAA", "Acme Industrial Holdings Inc"),
        (2, "BBB", "Acme Industrial Holdings Ltd"),
    ])
    _s, comp_to_id, names = rt.load_ticker_maps(conn)
    conn.close()
    assert rt.resolve_by_company_name("Acme Industrial Holdings", comp_to_id, names) is None


def test_candidate_generation_is_what_changed_and_it_is_casefolded():
    """`company_names` feeds only the matcher, and is now folded at construction."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tickers (id INTEGER PRIMARY KEY, symbol TEXT, company_name TEXT)")
    conn.execute("INSERT INTO tickers VALUES (1,'MMC','MARSH & MCLENNAN COMPANIES, INC.')")
    _s, comp_to_id, names = rt.load_ticker_maps(conn)
    conn.close()
    assert names == ["marsh & mclennan companies, inc."], "candidates must be casefolded"
    assert set(comp_to_id) == {"marsh & mclennan companies, inc."}, "the map was already folded"


# --------------------------------------------------------------------------
# KNOWN LIMIT — the measured cost, pinned so it cannot be forgotten
# --------------------------------------------------------------------------

def test_casefolding_also_creates_some_NEW_wrong_links_KNOWN_LIMIT():
    """⚠️ NOT "every change an improvement" — that held only for already-resolved rows.

    Over the full 1,422-row fuzzy population: 8 wrong links dropped and 75 plausible
    new ones, but also 9 SUSPECT new links where boilerplate matches at >= 0.7 while
    the distinctive token differs:

        XIAOMI CORP          -> AIXI   (Xiao-I Corp)
        ASHTED GRP PLC S/ADR -> AIBRF  (AIB Group plc)
        DBS GROUP HLDGS LTD  -> SUGP   (SU Group Holdings)

    These rows are UNLINKED today, so each newly acquires a wrong `ticker_id`. Net is
    ~83 improvements against 9 regressions, and the gate moves by zero either way per
    the diagnosis counterfactual — but the cost is real. Closing it means the cutoff or
    a token-overlap guard, both deliberately out of scope here.

    This test documents the limit and SHOULD fail when someone fixes it.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tickers (id INTEGER PRIMARY KEY, symbol TEXT, company_name TEXT)")
    conn.executemany("INSERT INTO tickers VALUES (?,?,?)", [
        (1, "AIXI", "Xiao-I Corp"),
        (2, "NVDA", "NVIDIA CORP"),
    ])
    _s, comp_to_id, names = rt.load_ticker_maps(conn)
    conn.close()
    got = rt.resolve_by_company_name("XIAOMI CORP [ST]", comp_to_id, names)
    assert got == 1, "documents the LIMIT: Xiaomi still mis-matches Xiao-I at >= 0.7"


# --------------------------------------------------------------------------
# The mutation that passed all 16 tests while relinking 104 real rows
# --------------------------------------------------------------------------

def test_the_candidate_list_is_NOT_de_duplicated_KNOWN_LIMIT():
    """🔴 De-duplicating `company_names` looks like an obvious one-line cleanup.

    It passes every other test in this file, and silently relinks **104 congressional
    rows** to arbitrary preferred/ADR share classes — `Berkshire Hathaway Inc. New`
    -> `BRK-A` x20, `Alibaba` -> `BBAAY` x11, `Regions Financial` -> `RF-PF`. The
    verifier found it; nothing here caught it.

    2,514 of the 10,619 names are exact duplicates (one row per share class or bond
    line: `BANK OF MONTREAL /CAN/` x27). Keeping them means the ambiguity guard sees
    N copies of the same company and abstains. Abstaining is the conservative answer
    and it is the CURRENT answer — but it is a choice, and de-duplicating would make
    it silently. This pins the choice.

    Deduping is a real, measured opportunity (+104 resolutions) and belongs in its own
    session with its own adjudication of which share class is correct.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tickers (id INTEGER PRIMARY KEY, symbol TEXT, company_name TEXT)")
    conn.executemany("INSERT INTO tickers VALUES (?,?,?)", [
        (1, "RF",    "REGIONS FINANCIAL CORP"),
        (2, "RF-PC", "REGIONS FINANCIAL CORP"),
        (3, "RF-PE", "REGIONS FINANCIAL CORP"),
    ])
    _s, comp_to_id, names = rt.load_ticker_maps(conn)
    conn.close()
    assert len(names) == 3, "the candidate list must keep duplicates, not collapse them"
    assert len(set(names)) == 1, "and they are the same folded string"


def test_duplicate_company_names_force_an_abstention_KNOWN_LIMIT():
    """The cost of keeping duplicates: the RIGHT company is found and discarded.

    `Regions Financial Corporation` matches its own name at 0.863 — but `RF`, `RF-PC`,
    `RF-PE` and `RF-PF` share that name, so the guard sees three identical scores and
    returns None. Folding made this MORE common (16 -> 29 descriptions, 104 rows),
    because more duplicates now clear the cutoff.

    Abstaining beats guessing a share class, so this is deliberate. It SHOULD fail if
    someone teaches the guard to collapse identical names.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tickers (id INTEGER PRIMARY KEY, symbol TEXT, company_name TEXT)")
    conn.executemany("INSERT INTO tickers VALUES (?,?,?)", [
        (1, "RF",    "REGIONS FINANCIAL CORP"),
        (2, "RF-PC", "REGIONS FINANCIAL CORP"),
        (3, "RF-PE", "REGIONS FINANCIAL CORP"),
    ])
    _s, comp_to_id, names = rt.load_ticker_maps(conn)
    conn.close()
    assert rt.resolve_by_company_name("Regions Financial Corporation", comp_to_id, names) is None


@pytest.mark.parametrize("desc,wrong_name", [
    ("SP OAKTREE STRATEGIC CREDIT", "Angel Oak Strategic Credit Fund"),
    ("UNITED STATES BILLS", "United States Oil Fund, LP"),
    ("AMERICAN SMALLCAP", "American Well Corp"),
])
def test_more_suspect_new_links_the_verifier_found_KNOWN_LIMIT(desc, wrong_name):
    """I reported 9 suspect new links; the true figure is 12 distinct / 27 rows.

    These three were missing from my list. Same failure mode as XIAOMI->Xiao-I:
    boilerplate matches at >= 0.7 while the distinctive token differs, and there is
    often no correct target in `tickers` at all (`XIACY`, `BHLB`, `GLDW` are absent),
    so the change manufactures a link where none was available.

    Closing this needs the cutoff or a token-overlap guard — both out of scope here.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tickers (id INTEGER PRIMARY KEY, symbol TEXT, company_name TEXT)")
    conn.execute("INSERT INTO tickers VALUES (1,'WRONG',?)", (wrong_name,))
    _s, comp_to_id, names = rt.load_ticker_maps(conn)
    conn.close()
    assert rt.resolve_by_company_name(desc, comp_to_id, names) == 1, \
        "documents the LIMIT: boilerplate similarity still wins at >= 0.7"
