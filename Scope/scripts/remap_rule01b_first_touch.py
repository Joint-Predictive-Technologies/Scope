#!/usr/bin/env python3
"""Retract stored RULE_01B alerts whose "no prior disclosed trade" claim is false. DRY-RUN BY DEFAULT.

════════════════════════════════════════════════════════════════════════════════════
WHAT AND WHY
════════════════════════════════════════════════════════════════════════════════════

RULE_01B decided "first touch" by INSERTION ORDER (`t2.id < t.id`, pre-fix
`scripts/rule_01b_first_touch.py:52`, now `:68-82`). Ingestion batches do not arrive in trade-date order —
a later batch routinely carries older trades — so the lowest-id row for a member+ticker is
often not the earliest. On the 2026-07-28 snapshot **780** member+ticker pairs had that
inversion and **39 of 192** stored RULE_01B alerts (20.3%) assert "no prior disclosed trade"
about a member who demonstrably has one:

    Gottheimer / ABT — alert #706 cites 2026-05-27 (txn id 40206)
                       against a true earliest of 2025-01-28 (txn id 43602)

RULE_01B is a live gate leg (`RULE_10_INSTRUMENTS["RULE_01B"] = "congressional"`), so this is
a false corroborating leg, not a cosmetic headline error.

The rule itself is fixed FORWARD-ONLY. This script repairs rows already written.

════════════════════════════════════════════════════════════════════════════════════
⚠️ WHY THIS IS A SCRIPT AND NOT A MIGRATION — READ BEFORE "TIDYING" IT
════════════════════════════════════════════════════════════════════════════════════

The RULE_11 repair (m003/m004) lives INSIDE `jpt_common` and therefore runs automatically on
the next `db_connection()` — i.e. on prod, at deploy, unattended. Prod writes here are the
human's by standing instruction, so this deliberately does NOT follow that pattern. It is a
standalone script, dry-run by default, requiring an explicit `--apply`. Moving it into the
migration chain would silently convert a human-gated repair into an automatic one. Don't.

Same discipline as `scripts/remap_rule09_tickers.py`.

════════════════════════════════════════════════════════════════════════════════════
RETRACT, NEVER "CORRECT" — AND WHY THERE IS NO CORRECT BRANCH
════════════════════════════════════════════════════════════════════════════════════

A false first-touch alert cannot be rewritten into a TRUE one. The member's real first touch in
that ticker is a DIFFERENT transaction, and for 38 of the 39 it predates the rule's 90-day
window — there is no true first-touch event to re-point the alert at. So the only honest
operation is retraction: strip the claim, state what is actually true, and say so.

The dedup slot is normally LEFT OCCUPIED. `rule_01b_first_touch.py:103-108` dedups on
`(rule, ticker, member_id)`, so a retracted row still blocks re-emission for that pair. For
those 38 that is correct: the true first touch is outside the window and nothing should fire.

⚠️ FOR THE REMAINING ONE IT IS NOT CORRECT, AND THE SCRIPT SAYS SO OUT LOUD.
An earlier version of this docstring claimed the true first touch was outside the window for
ALL 39. That was false, and a verification pass caught it. **GIL / K000398** (alert #8525,
claims 2026-06-17) has a true earliest of **2026-06-02, INSIDE the window** — the fixed rule
genuinely wants to emit it, and does against a cleared DB, but the retained dedup slot
suppresses it permanently. Retracting that row converts a false claim into a silenced TRUE
one. That is still a net improvement, but it is a real cost and it must not be invisible.

So `plan()` returns a fourth bucket, SUPPRESSED, and `--apply` prints it. Freeing the slot is
NOT done here: deleting the row would destroy the audit trail, and teaching the rule's dedup to
skip retracted rows is a change to the dedup, not to first-touch determination — out of scope
for this session. The human decides, with the row named.

RULE_01B's dedup key is `(rule, ticker, member_id)`, NOT the headline. Rewriting headline text
is therefore safe here. It would NOT be safe for RULE_06, whose dedup key IS its headline.

════════════════════════════════════════════════════════════════════════════════════
⚠️ DISCLOSED RESIDUAL — RETRACTION IS NOT YET HONOURED BY THE GATE
════════════════════════════════════════════════════════════════════════════════════

`lifecycle_stage` is a DISPLAY label. RULE_10's candidate query
(`scripts/rule_10_corroboration.py:176-187`) selects on ticker / rule / severity / created_at
and does NOT read it. So a row retracted here REMAINS a corroboration candidate until it ages
out of the 14-day window. On the local snapshot 11 of the 39 are HIGH (the gate's floor) and
all 192 were created 2026-07-08..07-20, so the exposure ages out within days.

This is stated rather than papered over. The two ways to close it — blanking a stored
`ticker`/`severity` to dodge the gate (a lie in the other direction), or teaching the gate to
skip retracted rows — are both out of scope for a first-touch chronology fix, and the second
must move FIVE co-expressed predicates together (`rule_10_corroboration.py`,
`api/routers/forming.py`, `scripts/morning_brief.py`, `api/routers/tickers.py`,
`scripts/check_convergence.py`). Queued as its own session.

════════════════════════════════════════════════════════════════════════════════════
GUARANTEES
════════════════════════════════════════════════════════════════════════════════════

* CLAIM TEXT ONLY. It writes `headline`, `detail` and `lifecycle_stage`. No `ticker`, no
  `severity`, and NO score column — not `opportunity_score`, not `novelty_score`, not
  `evidence_confidence`. Detection-time scores are immutable by standing decision, and
  nothing here rescores anything.
* IDEMPOTENT. Rows already `lifecycle_stage='retracted'` are skipped, so a second `--apply`
  changes zero rows.
* ⚠️ THE GUARANTEE ABOVE IS ABOUT THIS SCRIPT'S OWN STATEMENTS, NOT THE PROCESS FOOTPRINT.
  It connects via `jpt_common.db_connection()`, which runs the whole idempotent migration
  chain. The working DB is at m012, so a local run also applies m013/m014 (additive column
  adds). Harmless, but it is a write the guarantees would otherwise seem to exclude.
* FORWARD-ONLY and REVERSIBLE. `--apply` first writes the pre-image of every row it will
  touch to `rule01b_first_touch_retraction_backup`, so the change can be undone with one
  UPDATE…FROM (printed on completion).
* READ-ONLY COUNTS FIRST. The pre-flight prints before any write path is reachable.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection                      # noqa: E402

RULE = "RULE_01B"
BACKUP_TABLE = "rule01b_first_touch_retraction_backup"

# Imported, never redefined. This is only used to REPORT how much of the false-claim
# corpus is still a live corroboration candidate; a local copy would quietly disagree
# with the gate the moment the window moves.
from rule_10_corroboration import CONVERGENCE_WINDOW_DAYS as GATE_WINDOW_DAYS  # noqa: E402


def _claimed_date(tags: str | None) -> str | None:
    """The transaction_date a RULE_01B alert claims, from its own tags.

    Tags are `full_name|tx_type|delay_days|transaction_date|filing_date`
    (`rule_01b_first_touch.py:117`). Split on the pipe — the 2026-07-28 audit had to
    retract a figure because it read this with a fixed substr offset, which mis-parses
    the moment an earlier field changes width. A pipe split cannot drift that way; the
    separator was chosen precisely because member names contain commas.
    """
    parts = (tags or "").split("|")
    if len(parts) < 4:
        return None
    return parts[3].strip() or None


def plan(conn) -> tuple[list, list, list, list]:
    """Return (sound, retract, undecidable, suppressed) — NO WRITES.

    sound        — the claimed date IS the earliest for that member+ticker. Leave alone.
    retract      — a strictly earlier trade exists. The claim is false.
    undecidable  — the claim cannot be judged (tags unparseable, no matching transaction,
                   or an undated sibling). Reported, never written: this script only
                   retracts what it can PROVE false.
    suppressed   — a SUBSET of `retract`, not a separate action. These are the rows whose
                   TRUE first touch is itself inside the rule's 90-day window, so retracting
                   in place trades a false claim for a permanently silenced true one (the
                   dedup slot stays occupied). Surfaced so the cost is visible; see the
                   module docstring.
    """
    sound, retract, undecidable, suppressed = [], [], [], []
    rows = conn.execute(
        "SELECT id, ticker, member_id, tags, headline, detail, severity, lifecycle_stage, "
        "       created_at "
        "FROM alerts WHERE rule=? ORDER BY id", (RULE,)
    ).fetchall()

    for row in rows:
        claimed = _claimed_date(row["tags"])
        if not claimed or not row["member_id"] or not row["ticker"]:
            undecidable.append((row, claimed, None, "tags/key unusable"))
            continue

        # Ground truth on the SAME key the rule grouped by: the raw ticker string.
        # (That key is itself unvalidated — audit defect #4, artifact tickers such as
        # CHASE / CORP / SIMON — but re-keying is a separate session. This script
        # corrects chronology on whatever key the alert was actually written with.)
        gt = conn.execute(
            "SELECT MIN(transaction_date) AS earliest, "
            "       SUM(CASE WHEN transaction_date IS NULL OR date(transaction_date) IS NULL "
            "                THEN 1 ELSE 0 END) AS undated, "
            "       COUNT(*) AS n "
            "FROM transactions WHERE member_id=? AND raw_ticker_string=?",
            (row["member_id"], row["ticker"]),
        ).fetchone()

        if not gt or not gt["n"]:
            undecidable.append((row, claimed, None, "no matching transaction"))
        elif gt["undated"]:
            # An undated sibling means the true order is unknown. Unknown is not
            # "false", so it is NOT retracted — it is surfaced for a human.
            undecidable.append((row, claimed, gt["earliest"], "undated sibling trade"))
        elif gt["earliest"] and claimed > gt["earliest"]:
            retract.append((row, claimed, gt["earliest"], "earlier trade exists"))
            # Is the TRUE first touch itself still emittable? Same 90-day window basis
            # the rule uses, read from the same place, so the two cannot drift apart
            # silently if the window is later changed.
            in_window = conn.execute(
                "SELECT date(?) >= date('now', '-90 days')", (gt["earliest"],)
            ).fetchone()[0]
            if in_window:
                suppressed.append((row, claimed, gt["earliest"],
                                   "TRUE first touch is in-window and will stay suppressed"))
        else:
            sound.append((row, claimed, gt["earliest"], "claimed date is the earliest"))

    return sound, retract, undecidable, suppressed


def _retracted_text(row, claimed: str, earliest: str) -> tuple[str, str]:
    """The replacement headline/detail. Self-evidencing: it names the date that refutes it.

    ⚠️ THE RETRACTION LEADS. The original text is kept, but AFTER — never before.
    An earlier version appended the retraction to the end, which left the detail
    opening with the very sentence being retracted ("… has no prior disclosed trade
    in ABT …"); any surface that truncates a detail would have rendered the false
    claim and dropped the correction.
    """
    headline = f"RETRACTED: not a first touch — {row['ticker']}"
    detail = (
        f"RETRACTED — this was not a first touch. A prior disclosed trade in "
        f"{row['ticker']} exists for this member on {earliest}, before the "
        f"{claimed} transaction this alert was raised on. The original claim came from "
        f"an insertion-order bug (RULE_01B ranked by row id, not transaction_date) "
        f"and is false. Original text, retained for audit: "
        f"\"{(row['detail'] or '').strip()}\""
    )
    return headline, detail


def _preflight(conn) -> None:
    """READ-ONLY counts, printed before any write path is reachable."""
    row = conn.execute(
        "SELECT COUNT(*) n, MIN(created_at) lo, MAX(created_at) hi FROM alerts WHERE rule=?",
        (RULE,),
    ).fetchone()
    print("\n  ── PRE-FLIGHT (read-only) ─────────────────────────────────────────")
    print(f"  window            : ALL stored {RULE} alerts, no date filter")
    print(f"  {RULE} alerts    : {row['n']}")
    print(f"  created_at range  : {row['lo']}  ->  {row['hi']}")
    already = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE rule=? AND lifecycle_stage='retracted'", (RULE,)
    ).fetchone()[0]
    print(f"  already retracted : {already}")


def _report(conn, sound, retract, undecidable, suppressed) -> None:
    # RULE_10's candidate window, read from the gate's own constant so the two cannot
    # drift: CONVERGENCE_WINDOW_DAYS is what decides whether a row is still a candidate.
    gate_cutoff = conn.execute(
        "SELECT datetime('now', ?)", (f"-{GATE_WINDOW_DAYS} days",)
    ).fetchone()[0]

    total = len(sound) + len(retract) + len(undecidable)
    pct = (100.0 * len(retract) / total) if total else 0.0
    print("\n  ── CLAIM AUDIT ────────────────────────────────────────────────────")
    print(f"  {RULE} alerts examined            : {total}")
    print(f"  SOUND       (claim is true)        : {len(sound)}")
    print(f"  RETRACT     (a prior trade exists) : {len(retract)}   <-- the decisive count "
          f"({pct:.1f}%)")
    print(f"  UNDECIDABLE (never written)        : {len(undecidable)}")

    gate = [e for e in retract if e[0]["severity"] in ("HIGH", "CRITICAL")]
    still_live = [e for e in gate if (e[0]["created_at"] or "") >= gate_cutoff]
    print(f"\n  of the RETRACT rows, gate-reaching (HIGH/CRITICAL) : {len(gate)}")
    print(f"    of those, still inside RULE_10's 14-day window   : {len(still_live)}"
          f"   <-- the live exposure")
    print("  NOTE: retraction does NOT remove a row from RULE_10 candidacy — the gate's")
    print("        candidate query (rule_10_corroboration.py:176-187) reads ticker/rule/")
    print("        severity/created_at, never lifecycle_stage. Rows leave the gate by")
    print("        AGEING OUT, not by being retracted. Closing that is a separate session.")

    if suppressed:
        print(f"\n  ⚠️  SUPPRESSES A TRUE CLAIM : {len(suppressed)}")
        print("      These rows' TRUE first touch is ALSO inside the 90-day window, so the")
        print("      retained dedup slot (rule, ticker, member_id) will block the correct")
        print("      alert from ever emitting. Retracting trades a false claim for a")
        print("      silenced true one. Freeing the slot is the human's call — see the")
        print("      module docstring for why this script will not do it.")
        for row, claimed, earliest, _why in suppressed:
            print(f"      #{row['id']:<6} {row['ticker']:<8} {row['member_id']:<8} "
                  f"false={claimed}  true first touch={earliest} (would emit if unblocked)")

    for label, rows in (("RETRACT", retract), ("UNDECIDABLE", undecidable)):
        if not rows:
            continue
        print(f"\n  -- {label} sample --")
        for row, claimed, earliest, why in rows[:10]:
            print(f"     #{row['id']:<6} {row['ticker']:<8} {row['member_id']:<8} "
                  f"{row['severity']:<8} claimed={claimed}  earliest={earliest or '—'}  ({why})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the retractions. Without it, nothing is written.")
    args = parser.parse_args()

    conn = db_connection()
    _preflight(conn)
    sound, retract, undecidable, suppressed = plan(conn)
    _report(conn, sound, retract, undecidable, suppressed)

    # Idempotency: a row already retracted needs no second write.
    changes = [e for e in retract if (e[0]["lifecycle_stage"] or "") != "retracted"]

    if not args.apply:
        print(f"\n  DRY RUN — nothing written. {len(changes)} row(s) would change.")
        print("  Re-run with --apply to write.")
        conn.close()
        return 0

    if not changes:
        print("\n  Nothing to do — already retracted (idempotent).")
        conn.close()
        return 0

    conn.execute(f"""CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} (
                        alert_id            INTEGER PRIMARY KEY,
                        old_headline        TEXT,
                        old_detail          TEXT,
                        old_lifecycle_stage TEXT,
                        retracted_at        TEXT DEFAULT (datetime('now')))""")
    for row, claimed, earliest, _why in changes:
        conn.execute(
            f"INSERT OR IGNORE INTO {BACKUP_TABLE}"
            "(alert_id, old_headline, old_detail, old_lifecycle_stage) VALUES (?,?,?,?)",
            (row["id"], row["headline"], row["detail"], row["lifecycle_stage"]),
        )
        headline, detail = _retracted_text(row, claimed, earliest)
        # Claim text ONLY. No ticker, no severity, no score column appears in this
        # statement, deliberately — see GUARANTEES in the module docstring.
        conn.execute(
            "UPDATE alerts SET headline=?, detail=?, lifecycle_stage='retracted' WHERE id=?",
            (headline, detail, row["id"]),
        )
    conn.commit()
    print(f"\n  APPLIED — {len(changes)} row(s) retracted; pre-images in {BACKUP_TABLE}.")
    print(f"  Undo: UPDATE alerts SET headline=(SELECT old_headline FROM {BACKUP_TABLE} b"
          f" WHERE b.alert_id=alerts.id),"
          f" detail=(SELECT old_detail FROM {BACKUP_TABLE} b WHERE b.alert_id=alerts.id),"
          f" lifecycle_stage=(SELECT old_lifecycle_stage FROM {BACKUP_TABLE} b"
          f" WHERE b.alert_id=alerts.id)"
          f" WHERE id IN (SELECT alert_id FROM {BACKUP_TABLE});")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
