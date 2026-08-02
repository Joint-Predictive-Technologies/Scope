#!/usr/bin/env python3
"""Measure RULE_01B's stored severity residual. READ-ONLY — THIS SCRIPT CANNOT WRITE.

════════════════════════════════════════════════════════════════════════════════════
WHY THIS IS AN AUDIT AND NOT A REMAP — THE DIFFERENCE FROM THE OTHER THREE
════════════════════════════════════════════════════════════════════════════════════

The three other RULE_01B repairs today each shipped a `--apply` remap, and were right to:
chronology, the ticker key and direction all corrected **ATTRIBUTION** — what an alert SAYS.
Attribution is not a score, and correcting it rescores nothing.

**Severity is a DETECTION-TIME SCORE.** Every one of those sessions listed `severity` among the
columns it PROVED unchanged, and the RULE_15 saga treated an out-of-band severity write as a
defect in its own right. Writing stored severity here would be that exact violation, performed
by the session that is supposed to be most careful about it.

So the severity fix is **forward-only in code, with no remap**, and this file has **no `--apply`
flag, no UPDATE, no INSERT and no DELETE**. A file with no write path is a stronger guarantee
than a flag that defaults to off. `tests/test_rule01b_severity_band.py::test_no_script_writes_
stored_severity` asserts that property so it cannot quietly acquire one.

════════════════════════════════════════════════════════════════════════════════════
WHAT IT MEASURES
════════════════════════════════════════════════════════════════════════════════════

`_is_above_15k` was a three-literal denylist, so any amount it did not recognise became HIGH —
**37 of 9967** transactions on the 2026-07-28 snapshot, plus every absent amount (the call site
substitutes the string "undisclosed amount", which is also not in the list).

Severity is gate-relevant: only HIGH/CRITICAL clear RULE_10's candidate floor. So this reports
how much of that reached STORED ALERTS, and how much of THAT is still inside the gate's window.

The stored count is derived TWO INDEPENDENT WAYS and both are printed:
  * by joining the alert back to `transactions` on member + raw ticker + transaction_date;
  * by parsing the amount out of the alert's OWN detail text, which needs no join.
They should agree. **Disagreement is reported rather than reconciled** — if they diverge, the
join is unreliable on that corpus and the number should not be trusted.

════════════════════════════════════════════════════════════════════════════════════
THE DECISION THIS SCRIPT DOES NOT MAKE
════════════════════════════════════════════════════════════════════════════════════

If the stored false-HIGH count is non-zero, there are exactly two honest options and BOTH are
the human's:

  (a) LEAVE THEM. They age out of RULE_10's 14-day window on their own. No write, so the
      detection-time-immutability question never arises. This is the default.
  (b) CORRECT THEM, as a disclosed, human-gated, explicitly-recorded EXCEPTION to
      detection-time immutability — which would need its own decision note, because it is a
      deliberate breach of a standing rule and not a routine repair.

On the 2026-07-28 local snapshot the count is **0**, so the question does not arise there. Prod
may differ; that is what this script is for.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection                       # noqa: E402
from ingest_senate import amount_band_floor                # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# The live threshold and the live predicate, imported so this audit cannot disagree with the
# rule it is auditing.
from rule_01b_first_touch import RULE_01B_HIGH_THRESHOLD    # noqa: E402

RULE = "RULE_01B"
GATE_WINDOW_HOURS = 336        # RULE_10's 14-day candidate window

# The denylist as it stood, kept ONLY so this script can say what the old behaviour was.
_OLD_DENYLIST = {"$1,001 - $15,000", "$1k–15k", "$1,001-$15,000"}


def _old_verdict(band) -> str:
    """What `_is_above_15k` used to return. The call site substituted the string below."""
    effective = band or "undisclosed amount"
    return "HIGH" if (bool(effective) and effective not in _OLD_DENYLIST) else "MEDIUM"


def _correct_verdict(band) -> str:
    floor = amount_band_floor(band)
    return "HIGH" if (floor is not None and floor > RULE_01B_HIGH_THRESHOLD) else "MEDIUM"


def _amount_from_detail(detail: str | None) -> str | None:
    """The amount as the alert itself records it: 'Transaction: <type>, <amount>[, filed …]'."""
    m = re.search(r"Transaction: [^,]+, (.+?)(?:, filed|\.)", detail or "")
    return m.group(1).strip() if m else None


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    ).parse_args()          # no flags: there is nothing to opt into

    conn = db_connection()

    print("\n  ── TRANSACTIONS (the source population) ────────────────────────────")
    txns = conn.execute("SELECT amount_band, COUNT(*) n FROM transactions "
                        "GROUP BY amount_band").fetchall()
    total = sum(r["n"] for r in txns)
    mis = [(r["amount_band"], r["n"]) for r in txns
           if _old_verdict(r["amount_band"]) != _correct_verdict(r["amount_band"])]
    print(f"  transactions                          : {total}")
    print(f"  bands the denylist MISJUDGED          : {sum(n for _b, n in mis)}"
          f"   across {len(mis)} distinct spellings")
    for band, n in sorted(mis, key=lambda x: -x[1]):
        print(f"      {str(band):<26} n={n:<5} floor={amount_band_floor(band)}"
              f"  {_old_verdict(band)} -> {_correct_verdict(band)}")

    print("\n  ── STORED ALERTS (what actually reached the corpus) ────────────────")
    alerts = conn.execute(
        "SELECT id, ticker, member_id, tags, severity, detail, created_at "
        "FROM alerts WHERE rule=? ORDER BY id", (RULE,)
    ).fetchall()
    cut = conn.execute("SELECT datetime('now', ?)",
                       (f"-{GATE_WINDOW_HOURS} hours",)).fetchone()[0]

    by_join, by_detail, unjoined = [], [], 0
    for a in alerts:
        parts = (a["tags"] or "").split("|")
        tx_date = parts[3].strip() if len(parts) > 3 else None

        tx = conn.execute(
            "SELECT amount_band FROM transactions WHERE member_id=? AND raw_ticker_string=? "
            "  AND transaction_date=? LIMIT 1",
            (a["member_id"], a["ticker"], tx_date),
        ).fetchone()
        if tx is None:
            unjoined += 1
        elif a["severity"] == "HIGH" and _correct_verdict(tx["amount_band"]) == "MEDIUM":
            by_join.append((a, tx["amount_band"]))

        amt = _amount_from_detail(a["detail"])
        if amt and a["severity"] == "HIGH" and _correct_verdict(amt) == "MEDIUM":
            by_detail.append((a, amt))

    live = [x for x in by_join if (x[0]["created_at"] or "") >= cut]
    print(f"  window                                : ALL stored {RULE} alerts, no date filter")
    print(f"  {RULE} alerts                        : {len(alerts)}"
          f"  (HIGH: {sum(1 for a in alerts if a['severity'] == 'HIGH')})")
    print(f"  FALSE HIGH, derived BY JOIN           : {len(by_join)}"
          f"   ({unjoined} alert(s) did not join)")
    print(f"  FALSE HIGH, derived FROM DETAIL TEXT  : {len(by_detail)}   <-- independent check")
    if len(by_join) != len(by_detail):
        print("  ⚠️  THE TWO METHODS DISAGREE — the join is unreliable on this corpus and")
        print("      neither number should be trusted until that is understood.")
    else:
        print("  the two methods AGREE.")
    print(f"\n  of the false HIGH, inside RULE_10's {GATE_WINDOW_HOURS}h window (>= {cut})")
    print(f"                                        : {len(live)}   <-- the live gate exposure")

    if by_join:
        print(f"\n  by band: {dict(Counter(b for _a, b in by_join))}")
        for a, band in by_join[:20]:
            print(f"      #{a['id']:<6} {str(a['ticker']):<8} band={str(band):<24} "
                  f"created={str(a['created_at'])[:10]}")

    print("\n  ── THE DECISION (not this script's, and not this session's) ────────")
    if not by_join and not by_detail:
        print("  Nothing stored carries a false HIGH. The detection-time-immutability")
        print("  question does not arise on this database. No action needed.")
    else:
        print("  (a) LEAVE THEM — they age out of the gate window; no write, no immutability")
        print("      question. This is the default.")
        print("  (b) CORRECT THEM — a disclosed, human-gated, explicitly-recorded EXCEPTION to")
        print("      detection-time immutability, needing its own decision note.")
        print("  This script deliberately implements NEITHER.")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
