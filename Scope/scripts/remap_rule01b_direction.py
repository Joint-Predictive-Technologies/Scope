#!/usr/bin/env python3
"""Correct the hardcoded direction on stored RULE_01B alerts. DRY-RUN BY DEFAULT.

════════════════════════════════════════════════════════════════════════════════════
WHAT AND WHY
════════════════════════════════════════════════════════════════════════════════════

RULE_01B's headline was the hardcoded string "opens new position" on EVERY alert, while the
stored corpus is purchase 104 / sale 52 / sale_partial 31 / exchange 5. So **88 of 192 (45.8%)
describe a DISPOSAL as opening a position**, 24 of them HIGH. ABT #706 headlines "opens new
position in ABT" over a detail reading "Transaction: sale partial" — one alert contradicting
itself.

Worse, the claim is amplified at render time: `api/routers/evidence.py:172` renders
`alert.get("why_matters") or _WHY[rule]`, and `_WHY["RULE_01B"]` says *"A member opened a
brand-new disclosed position in this security."* **Zero of the 192 set `why_matters`**, so that
by-rule text applied to every sale. Setting `why_matters` per row is what switches it off.

The rule is fixed FORWARD-ONLY. This script repairs rows already written.

════════════════════════════════════════════════════════════════════════════════════
⚠️ WHY THIS IS A SCRIPT AND NOT A MIGRATION — READ BEFORE "TIDYING" IT
════════════════════════════════════════════════════════════════════════════════════

The RULE_11 repair (m003/m004) lives inside `jpt_common` and runs automatically on the next
`db_connection()` — on prod, at deploy, unattended. Prod writes here are the human's by standing
instruction. Standalone, dry-run by default, explicit `--apply`. Same discipline as
`remap_rule09_tickers.py`, `remap_rule01b_first_touch.py` and `remap_rule01b_ticker_validation.py`.

════════════════════════════════════════════════════════════════════════════════════
⚠️ RUN ORDER — THIS SCRIPT IS THIRD AND LAST
════════════════════════════════════════════════════════════════════════════════════

    1. remap_rule01b_first_touch.py        (chronology — reads alerts.ticker)
    2. remap_rule01b_ticker_validation.py  (ticker key — blanks alerts.ticker, adds the marker)
    3. remap_rule01b_direction.py          (this)

It must be last for two reasons, both load-bearing:

  * It SKIPS rows the chronology remap retracted. A retracted alert's claim has already been
    withdrawn; rewriting its headline into a fresh directional assertion would RESURRECT it as a
    live-looking signal. 25 of the 88 are in that state.
  * It must PRESERVE the `(UNVERIFIED SYMBOL)` marker the ticker remap adds, which means that
    marker has to exist before this runs. 16 of the 88 are in that state; 3 are in all three sets.

Detection of "not last": an unvalidated non-blank `alerts.ticker` still present means the ticker
remap is outstanding. For chronology the condition is no longer COMPUTABLE once the ticker remap
has blanked `alerts.ticker` (it joins on that column), so the signal is the existence of
`rule01b_first_touch_retraction_backup` — which is itself the reason the order is fixed rather
than merely recommended. Refuses with exit 2; `--force` overrides.

════════════════════════════════════════════════════════════════════════════════════
⚠️ THIS DOES NOT SIGN RULE_01B
════════════════════════════════════════════════════════════════════════════════════

`corroborates` / `corroboration_note` are written, and they are INERT: `alert_corroborates`
returns True for any rule outside `SIGNED_RULES` (`{"RULE_06"}`) before it reads them. Recording
an honest direction is the PRECONDITION for signing RULE_01B, not signing it. That is a separate,
human-gated session. This script does not touch `SIGNED_RULES`.

An unrecognised transaction type writes `corroborates = NULL` — the UNKNOWN verdict, which fails
closed on the day the rule is signed. Never a guess.

════════════════════════════════════════════════════════════════════════════════════
GUARANTEES
════════════════════════════════════════════════════════════════════════════════════

* CLAIM TEXT AND VERDICT ONLY. Writes `headline`, `why_matters`, `corroborates` and
  `corroboration_note`. No `severity`, no `ticker`, no `lifecycle_stage`, and NO score column —
  detection-time scores are immutable and nothing here rescores anything.
* IDEMPOTENT. A row whose headline already matches its transaction is skipped, so a second
  `--apply` changes zero rows.
* FORWARD-ONLY and REVERSIBLE. Pre-images to `rule01b_direction_backup`; undo printed.
* READ-ONLY COUNTS FIRST. The pre-flight prints before any write path is reachable.
* ⚠️ The guarantees describe THIS SCRIPT'S statements, not the process footprint: it connects via
  `db_connection()`, which runs the idempotent migration chain (the working DB is at m012, so a
  local run also applies m013/m014 — additive column adds, and m014 is what creates the two
  verdict columns this script writes).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection, normalize_ticker      # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# The single source of the direction mapping. Imported, never re-stated: a second copy here
# would be free to drift from what the rule emits, and then stored and fresh alerts would
# disagree about what a `sale_partial` means.
from rule_01b_first_touch import _direction_for              # noqa: E402

RULE = "RULE_01B"
BACKUP_TABLE = "rule01b_direction_backup"
CHRONO_BACKUP = "rule01b_first_touch_retraction_backup"
UNVERIFIED_MARKER = " (UNVERIFIED SYMBOL)"


def _fields(tags: str | None) -> tuple:
    """(name, raw_tx_type, raw_ticker) from the RULE_01B tags string.

    `full_name|tx_type|delay_days|transaction_date|filing_date[|raw_ticker]`. Field 1 holds the
    DISPLAY form the rule wrote (`sale partial`), so it is mapped back to the raw column value
    (`sale_partial`) before the direction lookup. Field 5 is the raw ticker, appended by the
    ticker-validation session and absent on rows written before it.
    """
    parts = (tags or "").split("|")
    name = parts[0].strip() if parts else ""
    display_tx = parts[1].strip() if len(parts) > 1 else ""
    raw_ticker = parts[5].strip() if len(parts) > 5 else ""
    return name, display_tx.replace(" ", "_").lower(), raw_ticker


def plan(conn) -> tuple[list, list, list]:
    """Return (correct, skipped_retracted, already_right) — NO WRITES."""
    correct, skipped, already = [], [], []
    rows = conn.execute(
        "SELECT id, ticker, member_id, tags, headline, why_matters, corroborates, "
        "       corroboration_note, severity, lifecycle_stage, created_at "
        "FROM alerts WHERE rule=? ORDER BY id", (RULE,)
    ).fetchall()

    for row in rows:
        if (row["lifecycle_stage"] or "") == "retracted":
            # Its claim is already withdrawn. Re-asserting a direction would resurrect it.
            skipped.append(row)
            continue

        name, raw_tx, raw_ticker = _fields(row["tags"])
        ticker = raw_ticker or (row["ticker"] or "")
        verb, corr, note, why = _direction_for(raw_tx)

        headline = f"First Touch — {name} {verb} {ticker}"
        # ⚠️ THIS SCRIPT SUPPLIES THE UNVERIFIED MARKER ON STORED ROWS; IT DOES NOT MERELY
        # PRESERVE IT. `remap_rule01b_ticker_validation.py` writes `ticker`, `tags` and
        # `lifecycle_stage` — deliberately NOT `headline` — so a stored row barred from
        # corroboration carries no marker in its headline at all. Only the RULE adds one, at
        # emission. Since this script rewrites the headline and puts the raw symbol back into
        # it, staying silent would leave "First Touch — X opens new position in THE" reading
        # as a confident claim about a symbol the system has explicitly refused to validate.
        #   blank `alerts.ticker` + a raw symbol in tags == the ticker remap's output state.
        already_marked = UNVERIFIED_MARKER.strip() in (row["headline"] or "")
        barred = not (row["ticker"] or "").strip() and bool(raw_ticker)
        if already_marked or barred:
            headline += UNVERIFIED_MARKER

        if (row["headline"] == headline and row["why_matters"] == why
                and row["corroborates"] == corr and row["corroboration_note"] == note):
            already.append(row)
        else:
            correct.append((row, headline, why, corr, note, raw_tx))
    return correct, skipped, already


def _order_check(conn) -> list[str]:
    """Reasons this script must not run yet. Empty means it is safely last."""
    problems = []

    valid = {normalize_ticker(r["symbol"])
             for r in conn.execute("SELECT symbol FROM tickers") if r["symbol"]}
    if valid:
        unvalidated = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE rule=? AND ticker IS NOT NULL AND ticker!=''",
            (RULE,),
        ).fetchone()[0] and sum(
            1 for r in conn.execute(
                "SELECT ticker FROM alerts WHERE rule=? AND ticker IS NOT NULL AND ticker!=''",
                (RULE,))
            if normalize_ticker(r["ticker"]) not in valid
        )
        if unvalidated:
            problems.append(
                f"{unvalidated} alert(s) still carry an UNVALIDATED ticker — "
                "remap_rule01b_ticker_validation.py has not been applied. Its "
                "`(UNVERIFIED SYMBOL)` marker must exist before this script preserves it."
            )

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (CHRONO_BACKUP,)
    ).fetchone()
    if not exists:
        problems.append(
            f"`{CHRONO_BACKUP}` does not exist — remap_rule01b_first_touch.py has not been "
            "applied. Rows it would retract must be retracted BEFORE this script runs, or "
            "this script rewrites their headlines into fresh directional claims."
        )
    return problems


def _preflight(conn, correct, skipped, already) -> None:
    """READ-ONLY counts, printed before any write path is reachable."""
    from collections import Counter
    total = len(correct) + len(skipped) + len(already)
    row = conn.execute(
        "SELECT COUNT(*) n, MIN(created_at) lo, MAX(created_at) hi FROM alerts WHERE rule=?",
        (RULE,),
    ).fetchone()

    print("\n  ── PRE-FLIGHT (read-only) ─────────────────────────────────────────")
    print(f"  window                 : ALL stored {RULE} alerts, no date filter")
    print(f"  {RULE} alerts         : {row['n']}")
    print(f"  created_at range       : {row['lo']}  ->  {row['hi']}")

    wrong = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE rule=? AND headline LIKE '%opens new position%'",
        (RULE,),
    ).fetchone()[0]
    print(f"\n  headlined 'opens new position' : {wrong}")

    disposals = [e for e in correct if e[5] != "purchase"]
    print(f"\n  ── CLAIM AUDIT ────────────────────────────────────────────────────")
    print(f"  CORRECT   (direction rewritten)  : {len(correct)}   <-- the decisive count")
    print(f"    of those, a DISPOSAL mislabelled as opening : {len(disposals)}")
    print(f"    by tx_type : {dict(Counter(e[5] for e in correct))}")
    hi = [e for e in correct if e[0]['severity'] in ('HIGH', 'CRITICAL')]
    print(f"    gate-reaching HIGH/CRITICAL                 : {len(hi)}")
    print(f"  SKIPPED   (already retracted)    : {len(skipped)}"
          f"   <-- never re-assert a direction on a retracted row")
    print(f"  UNCHANGED (already correct)      : {len(already)}")
    print(f"  total examined                   : {total}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the corrections. Without it, nothing is written.")
    parser.add_argument("--force", action="store_true",
                        help="apply even if the two prior RULE_01B remaps are outstanding. "
                             "Doing so resurrects retracted rows as fresh directional claims.")
    args = parser.parse_args()

    conn = db_connection()
    correct, skipped, already = plan(conn)
    _preflight(conn, correct, skipped, already)

    problems = _order_check(conn)
    print(f"\n  run-order problems : {len(problems)}   <-- MUST be 0 before applying")
    for p in problems:
        print(f"    - {p}")

    if not args.apply:
        print(f"\n  DRY RUN — nothing written. {len(correct)} row(s) would change.")
        print("  Re-run with --apply to write.")
        conn.close()
        return 0

    if problems and not args.force:
        print("\n  ⛔ REFUSING TO APPLY — this script must run THIRD.")
        print("     1. remap_rule01b_first_touch.py --apply")
        print("     2. remap_rule01b_ticker_validation.py --apply")
        print("     3. this script")
        print("     (`--force` overrides, and you should not need it.)")
        conn.close()
        return 2

    if not correct:
        print("\n  Nothing to do — already corrected (idempotent).")
        conn.close()
        return 0

    conn.execute(f"""CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} (
                        alert_id               INTEGER PRIMARY KEY,
                        old_headline           TEXT,
                        old_why_matters        TEXT,
                        old_corroborates       INTEGER,
                        old_corroboration_note TEXT,
                        corrected_at           TEXT DEFAULT (datetime('now')))""")
    for row, headline, why, corr, note, _raw in correct:
        conn.execute(
            f"INSERT OR IGNORE INTO {BACKUP_TABLE}(alert_id, old_headline, old_why_matters,"
            " old_corroborates, old_corroboration_note) VALUES (?,?,?,?,?)",
            (row["id"], row["headline"], row["why_matters"], row["corroborates"],
             row["corroboration_note"]),
        )
        # Claim text and verdict ONLY. No severity, no ticker, no lifecycle_stage and no
        # score column appears in this statement, deliberately — see GUARANTEES.
        conn.execute(
            "UPDATE alerts SET headline=?, why_matters=?, corroborates=?, "
            "corroboration_note=? WHERE id=?",
            (headline, why, corr, note, row["id"]),
        )
    conn.commit()
    print(f"\n  APPLIED — {len(correct)} row(s) corrected; pre-images in {BACKUP_TABLE}.")
    print(f"  Undo: UPDATE alerts SET"
          f" headline=(SELECT old_headline FROM {BACKUP_TABLE} b WHERE b.alert_id=alerts.id),"
          f" why_matters=(SELECT old_why_matters FROM {BACKUP_TABLE} b WHERE b.alert_id=alerts.id),"
          f" corroborates=(SELECT old_corroborates FROM {BACKUP_TABLE} b WHERE b.alert_id=alerts.id),"
          f" corroboration_note=(SELECT old_corroboration_note FROM {BACKUP_TABLE} b"
          f" WHERE b.alert_id=alerts.id)"
          f" WHERE id IN (SELECT alert_id FROM {BACKUP_TABLE});")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
