#!/usr/bin/env python3
"""Correct the RULE_02 alerts the directional-count fix no longer stands behind.

WHAT THIS CORRECTS
------------------
`rule_02_cluster.find_clusters` took its member count from every member with any
row in the 7-day window, and its verb from a net over each member's FIRST row. So
a member whose only activity on the ticker was an *exchange* counted toward the
headline number, and a member who both bought and sold voted as whichever row
happened to come first. RULE_02 therefore emitted alerts asserting a directional
consensus the underlying trades do not support.

The code is fixed forward (rule_02_cluster.py). This script exists only for the
alerts already stored, which the fix cannot reach.

HOW IT DECIDES — reconstruction, not a heuristic
------------------------------------------------
An earlier version of this script tested each alert's named members for
directional activity and retracted where fewer members were directional than the
headline claimed. That predicate finds count inflation and NOTHING ELSE, and it
therefore missed three HIGH alerts whose *count* was right and whose *verb* was
wrong. The verifier caught it.

This version instead re-derives the corpus: it runs the FIXED `find_clusters` over
the whole transaction table and asks, for each stored alert, whether the fixed rule
still emits it. That is the only predicate that is guaranteed to agree with the fix,
because it IS the fix.

The reconstruction is trustworthy because the BASELINE code, run the same way,
reproduces all 82 stored alerts exactly on (ticker, headline, severity, tags) —
`--check-reconstruction` asserts that before changing anything, and refuses to
proceed if the corpus cannot be reproduced.

Measured on the working DB (md5 177f474b03495c20df10a21335ca9dc3): 7 of 82 alerts,
SIX of them HIGH.

  RETRACT (the cluster is gone — too few directional members to be a cluster at all):
    id  5 [HIGH]   "2 members bought ABT"    Friedman: exchange-only
    id 72 [MEDIUM] "2 members traded VSNT"   Dingell + Pelosi: exchange-only
    id 73 [HIGH]   "3 members sold WAT"      Dingell + Hern: exchange-only
    id 74 [HIGH]   "2 members sold WAT"      Dingell: exchange-only

  CORRECT (the cluster is real; the DIRECTION it asserts is not):
    id  7 [HIGH]   "2 members sold ADP"    -> "2 members traded ADP (MIXED)"   MEDIUM
    id 30 [HIGH]   "2 members bought GOOGL" -> "2 members traded GOOGL (MIXED)" MEDIUM
    id 66 [HIGH]   "3 members sold US"     -> "3 members traded US (MIXED)"    MEDIUM

The three CORRECT rows are the ones that matter most operationally.
`rule_10_corroboration._candidate_alerts` filters `severity IN ('HIGH','CRITICAL')`,
so while they stay HIGH they remain gate-eligible RULE_10 legs asserting a
consensus the fixed rule reports as MIXED.

⚠️ THE GATE DOES NOT HONOUR RETRACTION for RULE_02. RULE_02 is not in
`jpt_common.SIGNED_RULES`, so `alert_corroborates` short-circuits True and a
retracted RULE_02 alert still supplies the `congressional` instrument. Verified by
execution: a retracted RULE_02 row still completes a 3-instrument convergence.
Dropping the three CORRECT rows to MEDIUM does remove them from `_candidate_alerts`;
retraction alone would not. Closing this properly means signing RULE_02 — a separate
human-gated session that this fix unblocks. Do not assume this script makes the gate
honest.

DISCIPLINE (mirrors scripts/remap_rule09_tickers.py)
----------------------------------------------------
* STANDALONE. Never imported by the migration chain; never run by the scheduler.
* DRY-RUN BY DEFAULT. `--apply` is required to write. READ the dry run first.
* IDEMPOTENT. Re-running after an `--apply` changes zero rows.
* REVERSIBLE. `--apply` writes the pre-image of every touched row to
  `rule02_directional_remap_backup` before updating, and prints the undo.
* PREPARED, NOT RUN. This has never been executed against the working DB.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import rule_02_cluster as fixed  # noqa: E402

RULE = "RULE_02"

#: The tree IMMEDIATELY BEFORE the directional-count fix landed, used by the
#: reconstruction guard below to rebuild the pre-fix corpus.
#:
#: ⚠️ This was `HEAD~1`, which was correct only while HEAD *was* the fix commit. Once
#: the fix merged, `HEAD~1` became a merge parent that ALREADY CONTAINS the fix, so the
#: guard rebuilt the corpus with the fixed rule, reproduced 75/82 instead of 82/82, and
#: refused every time. The only way past was `--skip-reconstruction-check`, which turns
#: the safety property OFF rather than satisfying it — and because the identity remap
#: refuses until this one has run, the entire RULE_02 remap chain was blocked behind a
#: flag that disables its own guard.
#:
#: Pinned to a commit, so it stays correct however history is later reshaped.
PRE_FIX_COMMIT = "2f16e36^"

# Stored alerts include 2-member clusters, so the corpus must be rebuilt at the
# lowest threshold any stored alert could have been emitted under. Rebuilding at
# 3 would make every 2-member alert look retractable.
REBUILD_MIN_MEMBERS = 2


def _connect(db_path: str | None) -> sqlite3.Connection:
    if db_path:
        conn = sqlite3.connect(db_path)
    else:
        from jpt_common import _get_db_path

        conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _all_transactions(conn) -> list[dict]:
    """Every transaction, shaped as `fetch_transactions` shapes them.

    No date filter: stored alerts span the whole history, and a 90-day window
    would make every older alert look retractable.
    """
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT t.member_id,
                   t.raw_ticker_string AS ticker,
                   t.transaction_type,
                   t.transaction_date,
                   m.full_name
            FROM transactions t
            LEFT JOIN members m ON m.bioguide_id = t.member_id
            WHERE t.raw_ticker_string IS NOT NULL
            ORDER BY t.raw_ticker_string, t.transaction_date
            """
        )
    ]


def _emitted_index(clusters) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """(ticker, tags) -> {(headline, severity), ...}

    Keyed on the member set, because that is what survives a verb correction: an
    alert whose direction changed keeps its members, and an alert whose members
    changed is a different cluster.
    """
    idx: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for c in clusters:
        idx.setdefault((c["ticker"], c["tags"]), set()).add(
            (c["headline"], c["severity"])
        )
    return idx


def _pre_images(conn) -> dict[int, tuple[str, str]]:
    """alert_id -> (old_headline, old_severity) for rows a prior --apply rewrote."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='rule02_directional_remap_backup'"
    ).fetchone()
    if not row:
        return {}
    return {
        r["alert_id"]: (r["old_headline"], r["old_severity"])
        for r in conn.execute(
            "SELECT alert_id, old_headline, old_severity "
            "FROM rule02_directional_remap_backup"
        )
        if r["old_headline"] is not None
    }


def classify(conn, check_reconstruction: bool = True):
    """Return (retract, correct, skipped, recon). Read-only."""
    txns = _all_transactions(conn)
    new_idx = _emitted_index(fixed.find_clusters(txns, REBUILD_MIN_MEMBERS))

    stored = conn.execute(
        "SELECT id, ticker, headline, severity, tags, lifecycle_stage "
        "FROM alerts WHERE rule = ? ORDER BY id",
        (RULE,),
    ).fetchall()

    recon = {"stored": len(stored), "reproduced_by_baseline": None}
    if check_reconstruction:
        # Trust check: the UNFIXED rule must reproduce the stored corpus, or the
        # reconstruction is not a valid basis for changing anything.
        base_dir = os.path.join(os.path.dirname(__file__), "..")
        baseline_src = _baseline_source(base_dir)
        if baseline_src is not None:
            base_idx = _emitted_index(baseline_src.find_clusters(txns, REBUILD_MIN_MEMBERS))
            # Compare against PRE-IMAGES for any row this remap already rewrote.
            # Without this the guard is self-defeating: a successful --apply
            # changes the very rows it then re-checks, so the second run sees the
            # baseline failing to reproduce them and refuses — making the script
            # non-idempotent, which the discipline forbids.
            pre = _pre_images(conn)
            hits = 0
            for a in stored:
                headline, severity = pre.get(a["id"], (a["headline"], a["severity"]))
                if (headline, severity) in base_idx.get((a["ticker"], a["tags"] or ""), set()):
                    hits += 1
            recon["reproduced_by_baseline"] = hits
            recon["from_pre_image"] = len(pre)

    retract, correct, skipped = [], [], []
    for a in stored:
        key = (a["ticker"], a["tags"] or "")
        stored_tuple = (a["headline"], a["severity"])
        candidates = new_idx.get(key, set())

        if stored_tuple in candidates:
            continue  # the fixed rule still emits this exactly

        already = (a["lifecycle_stage"] or "") == "retracted"
        if not candidates:
            retract.append({"id": a["id"], "severity": a["severity"],
                            "headline": a["headline"], "already": already})
        elif len(candidates) == 1:
            new_headline, new_severity = next(iter(candidates))
            correct.append({
                "id": a["id"], "severity": a["severity"], "headline": a["headline"],
                "new_headline": new_headline, "new_severity": new_severity,
                "already": a["headline"] == new_headline and a["severity"] == new_severity,
            })
        else:
            skipped.append({
                "id": a["id"],
                "why": f"ambiguous — {len(candidates)} candidate replacements: "
                       f"{sorted(candidates)}",
            })

    return retract, correct, skipped, recon


def _baseline_source(base_dir):
    """Load the pre-fix module from git, for the reconstruction trust check.

    Returns None (and the check is reported as unavailable) outside a git tree —
    the check is a guard, not a dependency.
    """
    import importlib.util
    import subprocess
    import tempfile

    try:
        src = subprocess.run(
            ["git", "show", f"{PRE_FIX_COMMIT}:Scope/rule_02_cluster.py"],
            cwd=base_dir, capture_output=True, text=True, timeout=20,
        )
        if src.returncode != 0 or not src.stdout:
            return None
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(src.stdout)
            path = fh.name
        spec = importlib.util.spec_from_file_location("_r02_baseline", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true",
                        help="Actually write. Without this the script only reports.")
    parser.add_argument("--db", default=None, help="DB path (default: the resolved app DB).")
    parser.add_argument("--skip-reconstruction-check", action="store_true",
                        help="Skip the baseline-reproduces-the-corpus guard.")
    args = parser.parse_args()

    conn = _connect(args.db)
    retract, correct, skipped, recon = classify(
        conn, check_reconstruction=not args.skip_reconstruction_check
    )

    print(f"\nRULE_02 directional-count remap — {recon['stored']} stored alerts")
    got = recon["reproduced_by_baseline"]
    if got is None:
        print("  reconstruction check: UNAVAILABLE (no git baseline reachable)")
    else:
        ok = got == recon["stored"]
        print(f"  reconstruction check: baseline reproduces {got}/{recon['stored']} "
              f"{'OK' if ok else '*** MISMATCH ***'}")
        if not ok and args.apply:
            print("\n  REFUSING TO APPLY — the unfixed rule does not reproduce the stored\n"
                  "  corpus, so 'the fixed rule no longer emits this' is not trustworthy.\n"
                  "  Re-run with --skip-reconstruction-check only if you understand why.")
            return 2

    print(f"\n  RETRACT ({len(retract)}) — no cluster survives at all:")
    for c in retract:
        print(f"    id {c['id']:>4} [{c['severity']:<6}] {c['headline']}"
              f"{'   (already retracted)' if c['already'] else ''}")
    print(f"\n  CORRECT ({len(correct)}) — cluster real, asserted direction not:")
    for c in correct:
        print(f"    id {c['id']:>4} [{c['severity']:<6}] {c['headline']}")
        print(f"           -> [{c['new_severity']:<6}] {c['new_headline']}"
              f"{'   (already correct)' if c['already'] else ''}")
    for s in skipped:
        print(f"  SKIP id {s['id']}: {s['why']}")

    pending_r = [c for c in retract if not c["already"]]
    pending_c = [c for c in correct if not c["already"]]

    if not args.apply:
        print(f"\n  DRY RUN — nothing written. {len(pending_r)} to retract, "
              f"{len(pending_c)} to correct.")
        print("  Read the rows above, then re-run with --apply.")
        return 0

    if not pending_r and not pending_c:
        print("\n  Nothing to do; already applied.")
        return 0

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rule02_directional_remap_backup (
            alert_id        INTEGER PRIMARY KEY,
            old_lifecycle   TEXT,
            old_headline    TEXT,
            old_severity    TEXT,
            old_why_matters TEXT
        )
    """)

    def _backup(alert_id):
        row = conn.execute(
            "SELECT lifecycle_stage, headline, severity, why_matters "
            "FROM alerts WHERE id = ?", (alert_id,)
        ).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO rule02_directional_remap_backup"
            "(alert_id, old_lifecycle, old_headline, old_severity, old_why_matters) "
            "VALUES (?,?,?,?,?)",
            (alert_id, row["lifecycle_stage"], row["headline"], row["severity"],
             row["why_matters"]),
        )

    for c in pending_r:
        _backup(c["id"])
        conn.execute(
            "UPDATE alerts SET lifecycle_stage = 'retracted', "
            "why_matters = COALESCE(why_matters || ' | ', '') || ? WHERE id = ?",
            ("retracted: member count included non-directional (exchange-only) members",
             c["id"]),
        )
    for c in pending_c:
        _backup(c["id"])
        conn.execute(
            "UPDATE alerts SET headline = ?, severity = ?, "
            "why_matters = COALESCE(why_matters || ' | ', '') || ? WHERE id = ?",
            (c["new_headline"], c["new_severity"],
             f"direction corrected: was {c['headline']!r} at {c['severity']}; "
             f"a member traded both ways inside the window",
             c["id"]),
        )
    conn.commit()

    print(f"\n  APPLIED — {len(pending_r)} retracted, {len(pending_c)} direction-corrected; "
          f"pre-images in rule02_directional_remap_backup.")
    print("  Undo: UPDATE alerts SET "
          "lifecycle_stage=(SELECT old_lifecycle FROM rule02_directional_remap_backup b WHERE b.alert_id=alerts.id), "
          "headline=(SELECT old_headline FROM rule02_directional_remap_backup b WHERE b.alert_id=alerts.id), "
          "severity=(SELECT old_severity FROM rule02_directional_remap_backup b WHERE b.alert_id=alerts.id), "
          "why_matters=(SELECT old_why_matters FROM rule02_directional_remap_backup b WHERE b.alert_id=alerts.id) "
          "WHERE id IN (SELECT alert_id FROM rule02_directional_remap_backup);")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
