"""THE BASKET-RULE CLASS, closed by SHAPE rather than by a list of names.

A basket rule publishes an alert whose TICKER came out of a hardcoded lookup table
instead of out of the event. Such a ticker must never complete a convergence.

The predecessor guard asserted that and did not deliver it: it identified a rule by the
first quoted `RULE_*` literal in the file, walked one directory, and knew two table
names. Three of five planted members passed it, and a real member — a rule at the repo
ROOT, with its own basket and its own alert writer — was firing the gate the whole time.

So the detector (`tests/basket_shape.py`) asks only two structural questions: is there a
module-level dict of string lists, and does an ELEMENT of one of those lists become an
alert's ticker. It names no table and no file, which is asserted below.
"""
from __future__ import annotations

import os
import shutil
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from basket_shape import repo_root, scan_repo  # noqa: E402
from jpt_common import (COUNTRY_REGION_MAP, RULE_10_EXCLUDED,  # noqa: E402
                        RULE_10_INSTRUMENTS, rule10_instruments)

# ── the class, as the sweep finds it ────────────────────────────────────────
#
# Derived FROM THE SWEEP, not from memory: `test_the_basket_class_has_no_UNKNOWN_members`
# asserts these are exactly what a structural walk of the repo turns up. Each entry
# records how that rule's basket is prevented from reaching the gate.
KNOWN = {
    "RULE_OSINT":          "emission retired — the alert path is switched off at source",
    "RULE_ADSB":           "excluded from RULE_10_EXCLUDED",
    "RULE_TELEGRAM_OSINT": "excluded from RULE_10_EXCLUDED",
    "RULE_12":             "retired and excluded",
    "RULE_08":             "LIVE AND UNRESOLVED — awaiting a human gate/scoring decision",
}


def _found():
    return scan_repo()


# ── 1. the guarantee ────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason=(
    "RULE_08's SECTOR_MAP is a LIVE basket reaching the gate as the 'fed-register' "
    "instrument: ['RULE_01B','RULE_06','RULE_08'] fires a convergence on a ticker that "
    "came from a keyword lookup. Resolving it is a human gate/scoring decision — exclude "
    "it (removing a counted instrument from live convergences) or replace SECTOR_MAP with "
    "real issuer attribution — and CLAUDE.md makes that human-gated. See "
    "vault/Scope/02_Sessions/SESSION-2026-07-29-basket-rule-gate-class.md. "
    "WHEN IT IS RESOLVED THIS XPASSES AND THE SUITE GOES RED: delete this marker."))
def test_no_basket_shaped_rule_reaches_the_gate():
    """The whole point, repo-wide. A lookup-table ticker cannot be a gate leg."""
    violators = [(r["rule"], r["path"], r["baskets"]) for r in _found()
                 if r["rule"] and rule10_instruments([r["rule"]])]
    assert violators == [], (
        "a basket-shaped rule contributes a gate instrument: " + "; ".join(
            f"{rule} ({path}, {baskets})" for rule, path, baskets in violators))


def test_the_basket_class_has_no_UNKNOWN_members():
    """THE FORWARD GUARANTEE, and it is green today.

    The instant a sixth basket rule appears anywhere in the repo — a new table, a new
    directory, a new filename — this fails and names it. That is the property the
    predecessor guard claimed and did not have.
    """
    found = {r["rule"] for r in _found() if r["rule"]}
    unknown = found - set(KNOWN)
    assert not unknown, (
        f"a basket-shaped rule nobody has ruled on: {sorted(unknown)} — decide whether it "
        f"is excluded, retired, or gets real attribution, then record it in KNOWN")
    missing = set(KNOWN) - found
    assert not missing, (
        f"{sorted(missing)} is recorded as a basket rule but the sweep no longer finds it "
        f"— if its basket was genuinely removed, delete it from KNOWN")


def test_the_ambiguous_ones_are_never_treated_as_clean():
    """Fail closed. A module whose rule identity cannot be established from exactly one
    declaration is reported, never quietly skipped."""
    ambiguous = [(r["path"], r["ambiguous"]) for r in _found() if not r["rule"]]
    assert ambiguous == [], f"basket rules with undeterminable identity: {ambiguous}"


# ── 2. the two already-decided members ──────────────────────────────────────

@pytest.mark.parametrize("rule,instrument", [("RULE_ADSB", "flight"),
                                             ("RULE_TELEGRAM_OSINT", "telegram")])
def test_the_excluded_basket_rules_contribute_nothing(rule, instrument):
    assert rule in RULE_10_EXCLUDED
    assert rule10_instruments([rule]) == []
    assert RULE_10_INSTRUMENTS.get(rule) == instrument, (
        "the mapping was deleted — an eligible-but-UNMAPPED rule becomes its own "
        "instrument, which is the phantom trap that let three retired rules count as "
        "three legs")
    assert rule10_instruments(["RULE_01B", "RULE_06", rule]) == \
        rule10_instruments(["RULE_01B", "RULE_06"])


# ── 3. the detector must not become a list again ────────────────────────────

def test_the_detector_names_no_table_and_no_file():
    """If any basket's NAME or any rule FILE appears in the detector, the guarantee has
    quietly reverted to an enumeration — which is the defect being fixed.

    ⚠️ STRIPS DOCSTRINGS VIA THE AST, NOT BY LINE PREFIX. The first version filtered out
    every line starting with a quote, so a live `_KNOWN_BASKET_TABLES = ["REGION_TICKERS",
    …]` list — one name per line, each line starting with a quote — passed it. A
    verification pass planted exactly that and the guard stayed green. The names are also
    HARVESTED from the repo rather than typed here, so a sixth table is covered the day it
    is written.
    """
    import ast as _ast

    detector = os.path.join(os.path.dirname(__file__), "basket_shape.py")
    tree = _ast.parse(open(detector).read())
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef,
                             _ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, _ast.Expr) and isinstance(first.value, _ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body.pop(0)
    code = _ast.unparse(tree)

    # Every basket name in the repo, and every rule module's stem — derived, not typed.
    names, stems = set(), set()
    for root, dirs, files in os.walk(repo_root()):
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "__pycache__", "data"}
                   and not d.startswith(".")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            stems.add(fn[:-3])
            try:
                t = _ast.parse(open(os.path.join(root, fn), encoding="utf-8").read())
            except (SyntaxError, OSError, UnicodeDecodeError):
                continue
            for n in t.body:
                tgt = None
                if isinstance(n, _ast.AnnAssign) and isinstance(n.target, _ast.Name):
                    tgt = n.target.id
                elif (isinstance(n, _ast.Assign) and len(n.targets) == 1
                      and isinstance(n.targets[0], _ast.Name)):
                    tgt = n.targets[0].id
                if tgt and isinstance(n.value, _ast.Dict) and n.value.values:
                    if all(isinstance(v, (_ast.List, _ast.Tuple, _ast.Set)) and v.elts
                           for v in n.value.values):
                        names.add(tgt)
    assert names, "the harvest found no baskets at all — this test is not testing anything"
    for name in names:
        assert name not in code, f"the detector hardcodes the table name {name}"
    for stem in stems:
        if stem.startswith("rule_"):
            assert stem not in code, f"the detector hardcodes the file {stem}"


@pytest.mark.parametrize("path,discriminating", [
    ("jpt_common.py", True),                  # HAS baskets; none reaches a ticker
    (os.path.join("scripts", "rule_14_patents.py"), True),   # HAS baskets; INVERTED map
    ("rule_06_form4.py", False),              # no basket at all — a weaker control
    (os.path.join("scripts", "rule_11_contracts.py"), False),
])
def test_real_attribution_is_NOT_flagged(path, discriminating):
    """The false-positive controls, all real code.

    ⚠️ ONLY TWO OF THESE DISCRIMINATE, AND THE FLAG SAYS WHICH. `rule_06` and `rule_11`
    contain no basket at all, so they short-circuit before any precision logic runs —
    they would pass against a detector that flagged every module containing a basket. A
    verification pass pointed that out; rather than drop them (they do check the detector
    is not flagging every rule) the weaker ones are now labelled as weaker.
    """
    scanned = {r["path"] for r in _found()}
    assert path not in scanned
    if discriminating:
        from basket_shape import _ModuleScan
        assert _ModuleScan(os.path.join(repo_root(), path), repo_root()).baskets, (
            "this control was supposed to CONTAIN a basket and no longer does — it has "
            "become vacuous")


def test_the_INVERTED_map_is_cleared_because_of_its_DIRECTION(tmp_path):
    """The sharpest control, made active rather than incidental.

    A real rule maps `ticker -> [company names]` and returns the KEY after matching a
    value against the document — correct attribution. Flip it to return a VALUE and the
    same file must be flagged. Without this, "we do not accuse the inverted map" could be
    true for the wrong reason.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "jpt_common.py").write_text("X = 1\n")
    tmpl = textwrap.dedent('''\
        RULE = "RULE_INV"
        ASSIGNEES = {{"LMT": ["Lockheed Martin"], "RTX": ["Raytheon"]}}


        def _ticker_for(assignee):
            for ticker, names in ASSIGNEES.items():
                for n in names:
                    if n.lower() in assignee.lower():
                        return {returned}
            return None


        def run(conn, patent):
            t = _ticker_for(patent["assignee"])
            conn.execute(
                "INSERT INTO alerts (rule, ticker, severity) VALUES (?,?,?)",
                (RULE, t, "HIGH"),
            )
    ''')
    (repo / "scripts" / "rule_inv.py").write_text(tmpl.format(returned="ticker"))
    assert scan_repo(str(repo)) == [], "the KEY direction — real attribution — was accused"
    (repo / "scripts" / "rule_inv.py").write_text(tmpl.format(returned="names[0]"))
    assert [r["rule"] for r in scan_repo(str(repo))] == ["RULE_INV"], (
        "flipping it to publish a VALUE was not flagged — the direction carve-out is an "
        "escape hatch, not a discriminator")


# ── 4. the planted members — the recheck's five, which the old sweep let through ──

_BASKET_RULE = '''\
{docstring}
from jpt_common import REGION_TICKERS

{decl}


def run(conn, region):
    tickers = REGION_TICKERS.get(region, [])
    for t in tickers[:2]:
        conn.execute(
            "INSERT INTO alerts (rule, ticker, severity, headline) "
            "VALUES ({rule_sql}, ?, ?, ?)",
            (t, "HIGH", "planted"),
        )
'''


def _plant(tmp_path, rel, *, docstring='"""A planted rule."""', decl='RULE = "RULE_PLANTED"',
           rule_sql="?", name_var="RULE"):
    """Build a throwaway repo containing a minimal basket source plus one planted rule."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "jpt_common.py").write_text(textwrap.dedent('''\
        REGION_TICKERS: dict[str, list[str]] = {
            "Middle East": ["USO", "XLE"],
            "Russia": ["LMT", "RTX"],
        }
    '''))
    body = _BASKET_RULE.format(docstring=docstring, decl=decl, rule_sql=rule_sql)
    if rule_sql == "?":
        body = body.replace('(t, "HIGH", "planted")',
                            f'({name_var}, t, "HIGH", "planted")')
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    return repo


def test_planted_A_a_plain_basket_rule_in_scripts(tmp_path):
    repo = _plant(tmp_path, os.path.join("scripts", "rule_planted_a.py"))
    found = {r["rule"] for r in scan_repo(str(repo))}
    assert found == {"RULE_PLANTED"}, found


def test_planted_B_a_runtime_assembled_name_FAILS_CLOSED(tmp_path):
    """No literal to read, so identity is undeterminable — and undeterminable must never
    read as clean."""
    repo = _plant(tmp_path, os.path.join("scripts", "rule_planted_b.py"),
                  decl='_P = "RULE"\n_S = "PLANTED_B"\nRULE = _P + "_" + _S')
    rows = scan_repo(str(repo))
    assert len(rows) == 1 and rows[0]["rule"] is None
    assert "no rule declaration" in rows[0]["ambiguous"]


def test_planted_C_a_docstring_naming_another_rule_cannot_MISIDENTIFY_it(tmp_path):
    """THE ONE THE OLD SWEEP LET THROUGH, reproduced exactly.

    ⚠️ THE SHAPE MATTERS AND MY FIRST VERSION OF THIS TEST GOT IT WRONG. It declared
    `RULE = "RULE_PLANTED_C"`, which the old regex chain matched on its FIRST pattern —
    so the test passed under the old logic too and proved nothing. The variant that
    actually slipped through held its name in a VARIABLE, so the chain fell through to
    "first quoted `RULE_*` literal anywhere in the file" and picked the excluded rule
    out of the docstring.

    Correct behaviour is therefore NOT "identifies it as RULE_PLANTED_C" — there is no
    declaration to read — it is FAIL CLOSED. What must never happen is inheriting the
    docstring's name, because that name is excluded and the rule would read as handled.
    """
    repo = _plant(tmp_path, os.path.join("scripts", "rule_planted_c.py"),
                  docstring='"""Supersedes the retired `RULE_OSINT` emission path."""',
                  decl='_NAME = "RULE_PLANTED_C"', rule_sql="?", name_var="_NAME")
    rows = scan_repo(str(repo))
    assert len(rows) == 1
    assert rows[0]["rule"] != "RULE_OSINT", (
        f"the docstring decided this live rule's identity, and that name is EXCLUDED — "
        f"the rule would read as already handled: {rows[0]}")
    assert rows[0]["rule"] is None and "no rule declaration" in rows[0]["ambiguous"], (
        f"an undeterminable identity must fail closed, not resolve: {rows[0]}")


def test_planted_D_a_rule_at_the_REPO_ROOT_is_swept(tmp_path):
    """THE OTHER ONE. Five production rules live at the repo root and the old sweep
    walked only the scripts directory."""
    repo = _plant(tmp_path, "rule_planted_d.py", decl='RULE = "RULE_PLANTED_D"')
    found = {r["rule"] for r in scan_repo(str(repo))}
    assert found == {"RULE_PLANTED_D"}, found


def test_planted_E_an_unexpected_filename_is_swept(tmp_path):
    repo = _plant(tmp_path, os.path.join("scripts", "osint_planted_e_rule.py"),
                  decl='RULE = "RULE_PLANTED_E"')
    found = {r["rule"] for r in scan_repo(str(repo))}
    assert found == {"RULE_PLANTED_E"}, found


def test_planted_F_conflicting_declarations_FAIL_CLOSED(tmp_path):
    """Two different names declared in one module: report it, do not pick one."""
    repo = _plant(tmp_path, os.path.join("scripts", "rule_planted_f.py"),
                  decl='RULE = "RULE_PLANTED_F"', rule_sql="'RULE_SOMETHING_ELSE'")
    rows = scan_repo(str(repo))
    assert len(rows) == 1 and rows[0]["rule"] is None
    assert "conflicting" in rows[0]["ambiguous"], rows[0]


def test_the_planted_control_a_NON_basket_rule_is_not_flagged(tmp_path):
    """Without this, a detector that flagged every rule would pass every test above."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "jpt_common.py").write_text("REGION_TICKERS = {'x': ['A']}\n")
    (repo / "scripts" / "rule_honest.py").write_text(textwrap.dedent('''\
        """Takes its ticker from the document, like a real attribution rule."""
        RULE = "RULE_HONEST"


        def run(conn, filing):
            conn.execute(
                "INSERT INTO alerts (rule, ticker, severity) VALUES (?, ?, ?)",
                (RULE, filing["issuer_symbol"], "HIGH"),
            )
    '''))
    assert scan_repo(str(repo)) == []


# ── 5. the coordinate invariant the globe fix rests on ──────────────────────

def test_every_mapped_region_HAS_coordinates():
    """The globe drops an event it cannot place rather than guessing — correct, and it
    rests on an invariant nothing asserted: every region a country maps to must have
    coordinates. Add one without, and real located events disappear silently.
    """
    import re
    src = open(os.path.join(repo_root(), "api", "main.py")).read()
    block = src[src.index("REGION_COORDS"):]
    block = block[:block.index("}")]
    have = set(re.findall(r'"([^"]+)"\s*:\s*\(', block))
    want = set(COUNTRY_REGION_MAP.values())
    assert not (want - have), (
        f"regions with no coordinates: {sorted(want - have)} — every located event in "
        f"them is silently dropped by /api/osint-hotspots")


# ── 6. the evasions a verification pass found, now pinned ───────────────────
#
# ⚠️ EVERY SHAPE BELOW WAS A SILENT MISS, and every one of them reached a FIRING gate —
# because `RULE_10_INSTRUMENTS.get(rule, rule)` makes an unmapped, non-excluded rule its
# own instrument. A verification pass planted 38 variants and 24 got through; these are
# the ones that were closed. The first is the worst: `insert_alert(conn, RULE, sym, ...)`
# is the writer this project's own conventions call PREFERRED, and four production rules
# call it positionally today, so the next basket rule written in house style was invisible.

_EVASIONS = {
    "positional shared writer": '''
        from jpt_common import db_connection, insert_alert
        RULE = "RULE_EV1"
        B = {"defense": ["LMT", "RTX"]}
        def run(s="defense"):
            conn = db_connection()
            for sym in B.get(s, []):
                insert_alert(conn, RULE, sym, "HIGH", "h")
    ''',
    "sorted() in the loop": '''
        RULE = "RULE_EV2"
        B = {"defense": ["LMT", "RTX"]}
        def run(conn, s="defense"):
            for sym in sorted(B.get(s, [])):
                conn.execute("INSERT INTO alerts (rule, ticker) VALUES (?,?)", (RULE, sym))
    ''',
    "a method on the element": '''
        RULE = "RULE_EV3"
        B = {"defense": ["lmt"]}
        def run(conn, s="defense"):
            for sym in B.get(s, []):
                conn.execute("INSERT INTO alerts (rule, ticker) VALUES (?,?)",
                             (RULE, sym.upper()))
    ''',
    "tuple values": '''
        RULE = "RULE_EV4"
        B = {"defense": ("LMT", "RTX")}
        def run(conn, s="defense"):
            for sym in B.get(s, ()):
                conn.execute("INSERT INTO alerts (rule, ticker) VALUES (?,?)", (RULE, sym))
    ''',
    "a basket in a class body": '''
        RULE = "RULE_EV5"
        class Cfg:
            B = {"defense": ["LMT", "RTX"]}
        def run(conn, s="defense"):
            for sym in Cfg.B.get(s, []):
                conn.execute("INSERT INTO alerts (rule, ticker) VALUES (?,?)", (RULE, sym))
    ''',
    "one element pulled out": '''
        import random
        RULE = "RULE_EV6"
        B = {"defense": ["LMT", "RTX"]}
        def run(conn, s="defense"):
            sym = random.choice(B.get(s, []))
            conn.execute("INSERT INTO alerts (rule, ticker) VALUES (?,?)", (RULE, sym))
    ''',
    "a flattening comprehension": '''
        RULE = "RULE_EV7"
        B = {"defense": ["LMT"], "energy": ["XOM"]}
        def run(conn):
            for sym in [x for lst in B.values() for x in lst]:
                conn.execute("INSERT INTO alerts (rule, ticker) VALUES (?,?)", (RULE, sym))
    ''',
}


@pytest.mark.parametrize("label", sorted(_EVASIONS))
def test_the_known_EVASIONS_are_caught(tmp_path, label):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(os.path.join(repo_root(), "jpt_common.py"), repo / "jpt_common.py")
    (repo / "scripts" / "rule_ev.py").write_text(textwrap.dedent(_EVASIONS[label]))
    found = [r["rule"] for r in scan_repo(str(repo)) if "rule_ev" in r["path"]]
    assert found, f"a basket rule using '{label}' reaches the gate unflagged"


# ── 7. the limits that REMAIN, encoded rather than described ─────────────────
#
# ⚠️ THESE SHAPES ARE NOT CAUGHT, AND EACH ONE COULD REACH THE GATE. They are recorded
# here as strict xfails for the same reason RULE_08 is: a limit written only in a note
# gets forgotten, while a limit written as a test announces itself the day someone closes
# it — the marker XPASSes, the suite goes red, and this list has to be updated.
#
# The honest scope of the guarantee, therefore: a basket rule whose table is a LITERAL
# dict of string collections, and whose ticker reaches either a literal `INSERT INTO
# alerts` statement with a column list, a `ticker=` keyword, a same-module wrapper, or an
# imported writer whose own signature names the ticker column. Baskets COMPUTED at import
# time, and SQL that cannot be read as a literal, are outside it.

_LIMITS = {
    "a basket built by a helper call": '''
        RULE = "RULE_LIM1"
        def _mk():
            return {"defense": ["LMT", "RTX"]}
        B = _mk()
        def run(conn, s="defense"):
            for sym in B.get(s, []):
                conn.execute("INSERT INTO alerts (rule, ticker) VALUES (?,?)", (RULE, sym))
    ''',
    "a basket built by dict(zip(...))": '''
        RULE = "RULE_LIM2"
        B = dict(zip(["defense"], [["LMT", "RTX"]]))
        def run(conn, s="defense"):
            for sym in B.get(s, []):
                conn.execute("INSERT INTO alerts (rule, ticker) VALUES (?,?)", (RULE, sym))
    ''',
    "SQL assembled in an f-string": '''
        RULE = "RULE_LIM3"
        B = {"defense": ["LMT", "RTX"]}
        def run(conn, s="defense"):
            t = "alerts"
            for sym in B.get(s, []):
                conn.execute(f"INSERT INTO {t} (rule, ticker) VALUES (?,?)", (RULE, sym))
    ''',
    "an INSERT with no column list": '''
        RULE = "RULE_LIM4"
        B = {"defense": ["LMT", "RTX"]}
        def run(conn, s="defense"):
            for sym in B.get(s, []):
                conn.execute("INSERT INTO alerts VALUES (NULL,?,?)", (RULE, sym))
    ''',
    "executemany over built rows": '''
        RULE = "RULE_LIM5"
        B = {"defense": ["LMT", "RTX"]}
        def run(conn, s="defense"):
            rows = [(RULE, sym) for sym in B.get(s, [])]
            conn.executemany("INSERT INTO alerts (rule, ticker) VALUES (?,?)", rows)
    ''',
}


@pytest.mark.parametrize("label", sorted(_LIMITS))
@pytest.mark.xfail(strict=True, reason=(
    "A KNOWN LIMIT of the structural detector, measured not assumed: this shape is not "
    "flagged and could reach the gate. Recorded as a strict xfail so that closing it "
    "turns the suite red and forces this list to be updated, instead of the limit living "
    "only in a session note. See SESSION-2026-07-29-basket-rule-gate-class.md."))
def test_the_KNOWN_LIMITS_are_still_limits(tmp_path, label):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "jpt_common.py").write_text("X = 1\n")
    (repo / "scripts" / "rule_lim.py").write_text(textwrap.dedent(_LIMITS[label]))
    found = [r["rule"] for r in scan_repo(str(repo)) if "rule_lim" in r["path"]]
    assert found, f"'{label}' is not caught"
