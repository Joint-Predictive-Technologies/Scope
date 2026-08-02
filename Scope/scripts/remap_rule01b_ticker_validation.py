#!/usr/bin/env python3
"""Bar stored RULE_01B alerts with an unvalidated ticker from corroboration. DRY-RUN BY DEFAULT.

════════════════════════════════════════════════════════════════════════════════════
WHAT AND WHY
════════════════════════════════════════════════════════════════════════════════════

RULE_01B wrote the RAW parsed ticker string from the PTR into `alerts.ticker` with no
validation, and the gate groups corroboration candidates BY THAT COLUMN. RULE_01B is a live
gate leg (`RULE_10_INSTRUMENTS["RULE_01B"] = "congressional"`), so an unvalidated string is a
live convergence KEY.

On the 2026-07-28 snapshot **41 of 192** stored alerts (21.4%) carry a ticker absent from
`tickers`, **22 of them HIGH**, and `NY` spans TWO members (C001123, P000608) — a cross-member
"convergence" on a string that is not a company.

The rule is fixed FORWARD-ONLY. This script repairs rows already written.

════════════════════════════════════════════════════════════════════════════════════
⚠️ WHY THIS IS A SCRIPT AND NOT A MIGRATION — READ BEFORE "TIDYING" IT
════════════════════════════════════════════════════════════════════════════════════

The RULE_11 repair (m003/m004) lives INSIDE `jpt_common` and runs automatically on the next
`db_connection()` — on prod, at deploy, unattended. Prod writes here are the human's by standing
instruction, so this is a standalone script, dry-run by default, requiring an explicit `--apply`.
Moving it into the migration chain would silently convert a human-gated repair into an automatic
one. Don't. Same discipline as `remap_rule09_tickers.py` and `remap_rule01b_first_touch.py`.

════════════════════════════════════════════════════════════════════════════════════
⚠️ ORDERING IS LOAD-BEARING — RUN THE CHRONOLOGY REMAP FIRST
════════════════════════════════════════════════════════════════════════════════════

`remap_rule01b_first_touch.py` derives its ground truth by joining `alerts.ticker` to
`transactions.raw_ticker_string`. THIS script blanks `alerts.ticker`. Run this one first and
every unvalidated row becomes "no matching transaction" for the chronology remap — its
correction could then never be applied, permanently.

    1. remap_rule01b_first_touch.py --apply     (chronology; reads alerts.ticker)
    2. remap_rule01b_ticker_validation.py --apply   (this; blanks alerts.ticker)

The pre-flight REFUSES to apply while unretracted false-chronology rows remain, unless forced.
Measured cost of getting it wrong: validation-first-with-`--force` then chronology retracts
**31** rows where the correct order retracts **39** — the 8 in the intersection become
permanently underivable.

**8** rows are in BOTH sets on the snapshot: #710 TPH, #714 CHASE, #715 CORP, #716 SIMON,
#723 FIN, #725 THE, #752 PA, #785 LLC. So this script also never downgrades an existing
`lifecycle_stage='retracted'` to `'review'` — a retracted alert stays retracted.

⚠️ An earlier version of this docstring said **9** and included **#739 BRK.B**. That was the
NAIVE 41-row intersection, never recomputed after this same docstring's own normalisation
point: BRK.B *validates* (against the stored `BRK-B`), so it is retracted by the chronology
remap and correctly left alone by this one. Found by a verification pass.

════════════════════════════════════════════════════════════════════════════════════
⚠️ ABSENT FROM `tickers` IS NOT "FAKE" — NOTHING IS DELETED, NOTHING IS RESOLVED
════════════════════════════════════════════════════════════════════════════════════

`tickers` is measurably incomplete — VOO, RYCEY, TPH, CTRA and FAS are all missing — so roughly
half the unvalidated strings are REAL securities it does not carry and half are PDF parse
artifacts. THE TWO ARE NOT SEPARABLE AUTOMATICALLY: 40 of the 41 match `^[A-Z]{1,5}$`, and the
single one that does NOT — `BRK.B` — is the real one. A shape rule gets it exactly backwards.

So every unvalidated row is treated identically: barred from corroboration, kept, flagged for a
human to triage into `tickers`. No row is deleted and NO string is resolved to a near symbol —
that is the difflib fabrication removed from RULE_09 and RULE_11.

Both sides of the comparison are normalised via `jpt_common.normalize_ticker`. `tickers` stores
551 class shares in the DASH form and zero in the dot form, while alerts carry the canonical
dot form, so a naive membership test would reject all 551 real symbols.

════════════════════════════════════════════════════════════════════════════════════
GUARANTEES
════════════════════════════════════════════════════════════════════════════════════

* ATTRIBUTION ONLY. Writes `ticker`, `tags` and `lifecycle_stage`. No `severity`, and NO score
  column — not `opportunity_score`, not `novelty_score`, not `evidence_confidence`.
  Detection-time scores are immutable by standing decision; nothing here rescores anything.
* IDEMPOTENT. A processed row has a blank `ticker`, so it is no longer a candidate. A second
  `--apply` changes zero rows.
* FORWARD-ONLY and REVERSIBLE. `--apply` first writes the pre-image of every row it will touch
  to `rule01b_ticker_validation_backup`; the undo is printed on completion.
* READ-ONLY COUNTS FIRST. The pre-flight prints before any write path is reachable.
* ⚠️ The guarantees above describe THIS SCRIPT'S statements, not the process footprint: it
  connects via `db_connection()`, which runs the idempotent migration chain (the working DB is
  at m012, so a local run also applies m013/m014 — additive column adds).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection, normalize_ticker      # noqa: E402

RULE = "RULE_01B"
BACKUP_TABLE = "rule01b_ticker_validation_backup"


def _validity_set(conn) -> set:
    """`tickers.symbol`, canonicalised — the same set `rule_01b_first_touch._validity_set` uses."""
    return {normalize_ticker(r["symbol"])
            for r in conn.execute("SELECT symbol FROM tickers") if r["symbol"]}


def _with_raw_ticker(tags: str | None, raw: str) -> str:
    """Append the raw string as tags field 5, idempotently.

    APPEND, NEVER PREPEND. `remap_rule01b_first_touch.py` reads `tags.split("|")[3]` for the
    transaction date, so fields 0-4 must keep their positions.
    """
    parts = (tags or "").split("|")
    while len(parts) < 5:
        parts.append("")
    if len(parts) >= 6 and parts[5]:
        return tags          # already carries it
    return "|".join(parts[:5] + [raw])


def plan(conn) -> tuple[list, list]:
    """Return (valid, unvalidated) — NO WRITES."""
    valid_symbols = _validity_set(conn)
    valid, unvalidated = [], []
    rows = conn.execute(
        "SELECT id, ticker, member_id, tags, severity, lifecycle_stage, created_at "
        "FROM alerts WHERE rule=? ORDER BY id", (RULE,)
    ).fetchall()
    for row in rows:
        raw = (row["ticker"] or "").strip()
        if not raw:
            continue                                  # already processed, or never keyed
        if normalize_ticker(raw) in valid_symbols:
            valid.append(row)
        else:
            unvalidated.append(row)
    return valid, unvalidated


def _false_chronology_outstanding(conn) -> int:
    """Rows the CHRONOLOGY remap still owes, computed its way — the ordering guard.

    Must be zero before this script blanks the tickers that remap needs to join on.
    """
    n = 0
    for row in conn.execute(
        "SELECT id, ticker, member_id, tags, lifecycle_stage FROM alerts WHERE rule=?", (RULE,)
    ):
        if (row["lifecycle_stage"] or "") == "retracted":
            continue
        parts = (row["tags"] or "").split("|")
        claimed = parts[3].strip() if len(parts) > 3 else ""
        if not claimed or not row["ticker"] or not row["member_id"]:
            continue
        gt = conn.execute(
            "SELECT MIN(transaction_date) e, "
            "       SUM(CASE WHEN transaction_date IS NULL OR date(transaction_date) IS NULL "
            "                THEN 1 ELSE 0 END) u, COUNT(*) c "
            "FROM transactions WHERE member_id=? AND raw_ticker_string=?",
            (row["member_id"], row["ticker"]),
        ).fetchone()
        if gt and gt["c"] and not gt["u"] and gt["e"] and claimed > gt["e"]:
            n += 1
    return n


def _preflight(conn, valid, unvalidated) -> int:
    """READ-ONLY counts, printed before any write path is reachable. Returns rows owed."""
    total = len(valid) + len(unvalidated)
    row = conn.execute(
        "SELECT COUNT(*) n, MIN(created_at) lo, MAX(created_at) hi FROM alerts WHERE rule=?",
        (RULE,),
    ).fetchone()
    done = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE rule=? AND (ticker IS NULL OR ticker='')", (RULE,)
    ).fetchone()[0]

    print("\n  ── PRE-FLIGHT (read-only) ─────────────────────────────────────────")
    print(f"  window                : ALL stored {RULE} alerts, no date filter")
    print(f"  {RULE} alerts        : {row['n']}")
    print(f"  created_at range      : {row['lo']}  ->  {row['hi']}")
    print(f"  already gate-invisible: {done}")
    print(f"  still keyed           : {total}")

    # How much of the "unvalidated" population is really just a normalisation mismatch?
    raw_syms = {r["symbol"] for r in conn.execute("SELECT symbol FROM tickers") if r["symbol"]}
    naive = [r for r in (valid + unvalidated) if (r["ticker"] or "") not in raw_syms]
    print(f"\n  unvalidated, NAIVE string match      : {len(naive)}")
    print(f"  unvalidated, NORMALISED both sides   : {len(unvalidated)}"
          f"   <-- the honest count")
    print(f"  recovered by matching normalisation  : {len(naive) - len(unvalidated)}"
          f"   (class shares: `tickers` stores BRK-B, alerts carry BRK.B)")

    gate = [r for r in unvalidated if r["severity"] in ("HIGH", "CRITICAL")]
    print(f"\n  VALID       (untouched)              : {len(valid)}")
    print(f"  UNVALIDATED (barred from the gate)   : {len(unvalidated)}"
          f"   <-- the decisive count "
          f"({100.0 * len(unvalidated) / total if total else 0:.1f}%)")
    print(f"    of those, gate-reaching HIGH/CRIT  : {len(gate)}")

    outstanding = _false_chronology_outstanding(conn)
    print(f"\n  chronology corrections still owed    : {outstanding}"
          f"   <-- MUST be 0 before applying")
    return outstanding


def _report(unvalidated) -> None:
    """The triage list — the human decides which of these belong in `tickers`."""
    from collections import defaultdict
    by_sym = defaultdict(list)
    for r in unvalidated:
        by_sym[r["ticker"]].append(r)

    print(f"\n  ── UNVALIDATED SYMBOLS ({len(by_sym)} distinct) — the human's triage list ──")
    print("  Absence from `tickers` does NOT mean fake. Neither this script nor any rule")
    print("  can tell a parse artifact from a real security the table lacks, so both are")
    print("  barred from corroboration and BOTH are kept. Add the real ones to `tickers`.")
    print(f"\n     {'symbol':<10}{'alerts':>7}{'members':>9}{'HIGH':>6}   cross-member?")
    for sym, rows in sorted(by_sym.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        members = {r["member_id"] for r in rows}
        hi = sum(1 for r in rows if r["severity"] in ("HIGH", "CRITICAL"))
        flag = "  <-- FALSE CONVERGENCE KEY" if len(members) > 1 else ""
        print(f"     {sym:<10}{len(rows):>7}{len(members):>9}{hi:>6}{flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes. Without it, nothing is written.")
    parser.add_argument("--force", action="store_true",
                        help="apply even with chronology corrections outstanding. "
                             "Doing so makes those corrections permanently underivable.")
    args = parser.parse_args()

    conn = db_connection()
    valid, unvalidated = plan(conn)
    outstanding = _preflight(conn, valid, unvalidated)
    _report(unvalidated)

    changes = unvalidated

    if not args.apply:
        print(f"\n  DRY RUN — nothing written. {len(changes)} row(s) would change.")
        print("  Re-run with --apply to write.")
        conn.close()
        return 0

    if outstanding and not args.force:
        print(f"\n  ⛔ REFUSING TO APPLY — {outstanding} chronology correction(s) outstanding.")
        print("     This script blanks `alerts.ticker`, which `remap_rule01b_first_touch.py`")
        print("     joins on to find its ground truth. Applying now would make those")
        print("     corrections permanently underivable.")
        print("     Run `remap_rule01b_first_touch.py --apply` FIRST, then re-run this.")
        print("     (`--force` overrides, and you should not need it.)")
        conn.close()
        return 2

    if not changes:
        print("\n  Nothing to do — already applied (idempotent).")
        conn.close()
        return 0

    conn.execute(f"""CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} (
                        alert_id            INTEGER PRIMARY KEY,
                        old_ticker          TEXT,
                        old_tags            TEXT,
                        old_lifecycle_stage TEXT,
                        remapped_at         TEXT DEFAULT (datetime('now')))""")
    for row in changes:
        conn.execute(
            f"INSERT OR IGNORE INTO {BACKUP_TABLE}"
            "(alert_id, old_ticker, old_tags, old_lifecycle_stage) VALUES (?,?,?,?)",
            (row["id"], row["ticker"], row["tags"], row["lifecycle_stage"]),
        )
        # A retracted alert STAYS retracted — never downgrade it to 'review'. 9 rows are
        # in both this set and the chronology remap's.
        stage = ("retracted" if (row["lifecycle_stage"] or "") == "retracted" else "review")
        # `ticker`, `tags`, `lifecycle_stage`. No severity and no score column appears in
        # this statement, deliberately — see GUARANTEES in the module docstring.
        conn.execute(
            "UPDATE alerts SET ticker='', tags=?, lifecycle_stage=? WHERE id=?",
            (_with_raw_ticker(row["tags"], row["ticker"]), stage, row["id"]),
        )
    conn.commit()
    print(f"\n  APPLIED — {len(changes)} row(s) barred from corroboration; "
          f"pre-images in {BACKUP_TABLE}.")
    print(f"  Undo: UPDATE alerts SET"
          f" ticker=(SELECT old_ticker FROM {BACKUP_TABLE} b WHERE b.alert_id=alerts.id),"
          f" tags=(SELECT old_tags FROM {BACKUP_TABLE} b WHERE b.alert_id=alerts.id),"
          f" lifecycle_stage=(SELECT old_lifecycle_stage FROM {BACKUP_TABLE} b"
          f" WHERE b.alert_id=alerts.id)"
          f" WHERE id IN (SELECT alert_id FROM {BACKUP_TABLE});")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
