#!/usr/bin/env python3
"""Mutation harness for the two fixes PORTED onto the ledger page on 2026-09-03.

🔴 WHY THIS EXISTS.  `test_map_ledger_model.py` slices its functions verbatim out
of `osint_map.html`, so a test there can only fail if the PAGE changes.  Three
times in this campaign a test that read the thing it pinned was found unable to
fail, so the two ported behaviours are not treated as proven until a deliberate
break of each is shown to turn a test red.

⚠️ THE WORKING TREE IS NEVER MUTATED.  Each mutant is applied to a temp copy that
reproduces the test's own `../../map-service/static/osint_map.html` relative
path, and pytest is pointed at the copy.  A previous harness in this campaign
corrupted a tree with `.bak` restores; this one cannot, because it never writes
inside the repo.

Both controls are mandatory and are reported first:
  CONTROL A  the unmutated copy               must be GREEN
  CONTROL B  a deliberate sabotage            must be RED
A number without both controls is not a number — a harness in this campaign once
scored every run as "caught" because of a bad pytest flag.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
PAGE_REL = os.path.join("map-service", "static", "osint_map.html")
TEST_REL = os.path.join("Scope", "tests", "test_map_ledger_model.py")
PY = os.path.expanduser("~/dev/Scope/.venv/bin/python")

# (name, needle, replacement, tests that MUST turn red)
MUTANTS = [
    ("the third differing name is dropped again — the pre-port behaviour",
     "} else if(!seen[id].alts.some(function(a){ return a.name===name; })){\n"
     "          seen[id].alts.push({name:name, src:src});\n        }",
     "}",
     ["test_A_THIRD_DIFFERING_NAME_IS_KEPT_AND_NOT_SILENTLY_DROPPED"]),

    ("alts stops de-duplicating, so one name is listed twice",
     "!seen[id].alts.some(function(a){ return a.name===name; })",
     "true",
     ["test_A_THIRD_DIFFERING_NAME_IS_KEPT_AND_NOT_SILENTLY_DROPPED",
      "test_A_REPEATED_ALT_NAME_IS_LISTED_ONCE"]),

    ("the contract loser is pushed to alts instead of awardedAs",
     "seen[id].awardedAs=seen[id].name;",
     "seen[id].alts.push({name:seen[id].name, src:seen[id].nameSrc});",
     ["test_THE_ORIGINAL_CONTRACT_NAME_RULE_IS_UNTOUCHED_BY_THE_PORT"]),

    ("the contract name wins the label again — the subsidiary asserted for the parent",
     "if(seen[id].nameSrc==='contract' && src!=='contract'){",
     "if(false){",
     ["test_THE_ORIGINAL_CONTRACT_NAME_RULE_IS_UNTOUCHED_BY_THE_PORT"]),

    ("`sited` is never carried from the site row — the datum is dropped",
     "            d.precision==='coordinate');",
     "            false);",
     ["test_THE_COORDINATE_FOOTER_READS_THE_DATUM_NOT_THE_RENDERED_NOTE"]),

    ("`sited` is not merged onto an entity a second source already created",
     "if(sited) seen[id].sited=true;",
     "",
     ["test_THE_COORDINATE_FOOTER_READS_THE_DATUM_NOT_THE_RENDERED_NOTE"]),

    ("the constructor drops `sited`, so only the merge path can set it",
     "alts:[], sited:!!sited};",
     "alts:[], sited:false};",
     ["test_THE_COORDINATE_FOOTER_READS_THE_DATUM_NOT_THE_RENDERED_NOTE",
      "test_A_SITE_THAT_NAMES_ITSELF_FIRST_IS_STILL_SITED"]),
]

SABOTAGE = ("CONTROL B — the note is no longer kept, so every row loses its figure",
            "if(!seen[id].note && note) seen[id].note=note;", "")


def build_tree(tmp: str) -> str:
    os.makedirs(os.path.join(tmp, os.path.dirname(PAGE_REL)), exist_ok=True)
    os.makedirs(os.path.join(tmp, os.path.dirname(TEST_REL)), exist_ok=True)
    shutil.copy2(os.path.join(ROOT, PAGE_REL), os.path.join(tmp, PAGE_REL))
    shutil.copy2(os.path.join(ROOT, TEST_REL), os.path.join(tmp, TEST_REL))
    return os.path.join(tmp, TEST_REL)


def run(tmp: str) -> tuple[int, set[str]]:
    """Return (exit code, set of test names that FAILED)."""
    r = subprocess.run([PY, "-m", "pytest", os.path.join(tmp, TEST_REL), "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, cwd=tmp)
    out = r.stdout + r.stderr
    failed = set(re.findall(r"(test_[A-Za-z0-9_]+)\s+-\s", out)) | \
             set(re.findall(r"_{5,}\s+(test_[A-Za-z0-9_]+)\s+_{5,}", out))
    return r.returncode, failed


def apply(tmp: str, needle: str, repl: str) -> bool:
    p = os.path.join(tmp, PAGE_REL)
    s = open(p, encoding="utf-8").read()
    if s.count(needle) != 1:
        return False                      # ambiguous or absent: reported, never scored
    open(p, "w", encoding="utf-8").write(s.replace(needle, repl))
    return True


def main() -> int:
    print(f"page  : {os.path.join(ROOT, PAGE_REL)}")
    print(f"tests : {os.path.join(ROOT, TEST_REL)}\n")

    with tempfile.TemporaryDirectory() as tmp:
        build_tree(tmp)
        code, failed = run(tmp)
        a_ok = code == 0 and not failed
        print(f"CONTROL A  unmutated copy         {'GREEN ✅' if a_ok else 'RED 🔴 ' + str(sorted(failed))}")
        if not a_ok:
            print("\n🔴 the control is red; no mutant score can mean anything. stopping.")
            return 2

    with tempfile.TemporaryDirectory() as tmp:
        build_tree(tmp)
        name, needle, repl = SABOTAGE
        if not apply(tmp, needle, repl):
            print(f"CONTROL B  ANCHOR NOT FOUND 🔴 — {name}")
            return 2
        code, failed = run(tmp)
        print(f"CONTROL B  {name[:46]}  {'RED ✅ ' + str(len(failed)) + ' failed' if failed else 'GREEN 🔴 sabotage undetected'}")
        if not failed:
            print("\n🔴 a deliberate break was not caught; the suite cannot be trusted. stopping.")
            return 2

    print()
    ok = bad = missing = 0
    for i, (name, needle, repl, expect) in enumerate(MUTANTS, 1):
        with tempfile.TemporaryDirectory() as tmp:
            build_tree(tmp)
            if not apply(tmp, needle, repl):
                print(f"  {i:>2}. ANCHOR NOT FOUND 🔴  {name}")
                missing += 1
                continue
            code, failed = run(tmp)
            hit = set(expect) & failed
            if hit:
                print(f"  {i:>2}. caught  ✅  {name}")
                ok += 1
            else:
                print(f"  {i:>2}. SURVIVED 🔴  {name}")
                print(f"        expected red: {expect}; actually red: {sorted(failed) or 'nothing'}")
                bad += 1
    print(f"\n{ok} of {len(MUTANTS)} caught · {bad} survived · {missing} anchors not found")
    return 0 if bad == 0 and missing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
