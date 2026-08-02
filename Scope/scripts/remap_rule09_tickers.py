#!/usr/bin/env python3
"""Re-derive stored RULE_09 alert tickers from the authoritative mapping. DRY-RUN BY DEFAULT.

════════════════════════════════════════════════════════════════════════════════════
WHAT AND WHY
════════════════════════════════════════════════════════════════════════════════════

RULE_09 attributed lobbying clients to tickers with `difflib.get_close_matches(cutoff=0.7)`.
Of the 216 stored alerts carrying a ticker, **86 (39.8%) share ZERO distinctive tokens with
the company they name** — IBM CORPORATION -> $VIRC, HEXCEL -> $HBIA, RELX -> $ARDX. RULE_09 is
corroboration-eligible, so those can build a false convergence on the wrong company.

`rule_09_lobbying.match_ticker` is now authoritative (curated overrides + unique exact
normalized name, no fallback). This script applies the same resolver to rows already written.

════════════════════════════════════════════════════════════════════════════════════
⚠️ WHY THIS IS A SCRIPT AND NOT A MIGRATION — READ BEFORE "TIDYING" IT
════════════════════════════════════════════════════════════════════════════════════

The equivalent RULE_11 repair (m003/m004) lives INSIDE `jpt_common` and therefore runs
automatically on the next `db_connection()` — i.e. on prod, at deploy, unattended. Prod writes
here are the human's by standing instruction, so this deliberately does NOT follow that
pattern. It is a standalone script, dry-run by default, requiring an explicit `--apply`.

Moving this into the migration chain would silently convert a human-gated repair into an
automatic one. Don't.

════════════════════════════════════════════════════════════════════════════════════
⚠️ CLIENT RECOVERY IS IMPERFECT, AND THAT IS SAFE BY CONSTRUCTION
════════════════════════════════════════════════════════════════════════════════════

RULE_09 builds `tags` as `", ".join([client, registrant, issues…, spend])` and 382 of 2113
known client names contain a comma, so the client's right edge is genuinely ambiguous. (The
RULE_11 repair had it easier: those tags are `|`-separated.) `_client_of` recovers field 0 and
re-joins any immediately following BARE legal-form fragment ("INC.", "LLC") — bounded and
deterministic, no similarity.

⚠️ THE SAFETY ARGUMENT, STATED CORRECTLY — AN EARLIER VERSION OF THIS COMMENT OVERCLAIMED IT.
It said a mis-recovered client "can never cause a fabrication". That is true of the KEEP branch
(a row is kept only when the resolver returns EXACTLY the stored symbol) but **false of the
CORRECT branch**, which writes `resolved` — a ticker that by definition was never stored. A
truncation landing exactly on a curated key would write it:

    tags = "JACOBS, MEDINGER & FINNEGAN, LLP, …"  ->  _client_of -> "JACOBS"  ->  CORRECT -> $J

So the honest statement is: mis-recovery is **safe on KEEP and CLEAR, and is a live risk on
CORRECT**. Two things bound it. First, CORRECT is small and inspectable — 5 rows locally, 0 on
prod — so the dry-run must be READ before `--apply`, and every CORRECT row should be eyeballed.
Second, measured on the real corpus, **all 216 rows recover cleanly with zero truncations**, and
the 5 CORRECT rows are genuine improvements. The risk is real but currently unrealised; it is
disclosed here rather than assumed away. Found by a verification pass.

════════════════════════════════════════════════════════════════════════════════════
GUARANTEES
════════════════════════════════════════════════════════════════════════════════════

* ATTRIBUTION ONLY. It writes `alerts.ticker` and nothing else — no `opportunity_score`, no
  `novelty_score`, no headline. Detection-time scores are immutable by standing decision.
* IDEMPOTENT. Re-running after an `--apply` changes zero rows.
* FORWARD-ONLY and REVERSIBLE. `--apply` first writes the pre-image of every row it will
  touch to `rule09_ticker_remap_backup`, so the change can be undone with one UPDATE…FROM.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jpt_common import db_connection                      # noqa: E402
from rule_09_lobbying import match_ticker                 # noqa: E402

RULE = "RULE_09"

# Bare legal-form fragments that belong to the PRECEDING comma-separated field.
_LEGAL_FRAGMENT = {
    "INC", "INC.", "LLC", "LLC.", "L.L.C.", "LLP", "LP", "L.P.", "LTD", "LTD.",
    "CORP", "CORP.", "CO", "CO.", "S.A.", "N.V.", "PLC", "PBC", "P.C.",
    "INCORPORATED", "LIMITED", "THE",
}


def _client_of(tags: str | None) -> str | None:
    """The client name from a RULE_09 tags string. Deterministic; see the module docstring."""
    parts = [p.strip() for p in (tags or "").split(",")]
    if not parts or not parts[0]:
        return None
    client = parts[0]
    index = 1
    while index < len(parts) and parts[index].upper() in _LEGAL_FRAGMENT:
        client = f"{client}, {parts[index]}"
        index += 1
    return client


def plan(conn) -> tuple[list, list, list]:
    """Return (keep, correct, clear) — no writes. `correct`/`clear` carry the new value."""
    tickers = [(r["symbol"], r["company_name"] or "")
               for r in conn.execute("SELECT symbol, company_name FROM tickers")]
    keep, correct, clear = [], [], []
    rows = conn.execute(
        "SELECT id, ticker, tags FROM alerts "
        "WHERE rule=? AND ticker IS NOT NULL AND ticker != ''", (RULE,)
    ).fetchall()
    for row in rows:
        client = _client_of(row["tags"])
        resolved = match_ticker(client, tickers) if client else None
        entry = (row["id"], client, row["ticker"], resolved)
        if resolved is None:
            clear.append(entry)
        elif resolved == row["ticker"]:
            keep.append(entry)
        else:
            correct.append(entry)
    return keep, correct, clear


def _report(keep, correct, clear) -> None:
    total = len(keep) + len(correct) + len(clear)
    print(f"\n  RULE_09 alerts carrying a ticker : {total}")
    print(f"  KEEP    (authority confirms)     : {len(keep)}")
    print(f"  CORRECT (authority says another) : {len(correct)}")
    print(f"  CLEAR   (no confident mapping)   : {len(clear)}   <-- the decisive count")
    for label, rows in (("CORRECT", correct), ("CLEAR", clear), ("KEEP", keep)):
        if not rows:
            continue
        print(f"\n  -- {label} sample --")
        for alert_id, client, old, new in rows[:8]:
            print(f"     #{alert_id:<6} {str(client)[:44]:<46} ${old:<6} -> {new or '(blank)'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes. Without it, nothing is written.")
    args = parser.parse_args()

    conn = db_connection()
    keep, correct, clear = plan(conn)
    _report(keep, correct, clear)

    changes = correct + clear
    if not args.apply:
        print(f"\n  DRY RUN — nothing written. {len(changes)} row(s) would change.")
        print("  Re-run with --apply to write.")
        conn.close()
        return 0

    if not changes:
        print("\n  Nothing to do — already remapped (idempotent).")
        conn.close()
        return 0

    conn.execute("""CREATE TABLE IF NOT EXISTS rule09_ticker_remap_backup (
                        alert_id INTEGER PRIMARY KEY,
                        old_ticker TEXT,
                        remapped_at TEXT DEFAULT (datetime('now')))""")
    for alert_id, _client, old, new in changes:
        conn.execute(
            "INSERT OR IGNORE INTO rule09_ticker_remap_backup(alert_id, old_ticker) VALUES (?,?)",
            (alert_id, old),
        )
        # `ticker` ONLY. No score column appears in this statement, deliberately.
        conn.execute("UPDATE alerts SET ticker=? WHERE id=?", (new, alert_id))
    conn.commit()
    print(f"\n  APPLIED — {len(changes)} row(s) updated; pre-images in rule09_ticker_remap_backup.")
    print("  Undo: UPDATE alerts SET ticker=(SELECT old_ticker FROM rule09_ticker_remap_backup b"
          " WHERE b.alert_id=alerts.id) WHERE id IN (SELECT alert_id FROM rule09_ticker_remap_backup);")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
