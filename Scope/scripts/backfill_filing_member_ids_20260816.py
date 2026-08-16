#!/usr/bin/env python3
"""One-time backfill of `filings.member_id` from the Stage 3 re-match review.

    python scripts/backfill_filing_member_ids_20260816.py --db <path>
    python scripts/backfill_filing_member_ids_20260816.py --db <path> --apply
    python scripts/backfill_filing_member_ids_20260816.py --db <path> --apply --sync-transactions

🔴 WRITES TO SCOPE'S PRODUCTION DATABASE. `filings` is DATA-LOSS class, so this
applies an EXPLICIT, HUMAN-REVIEWED LIST and nothing else. It does not re-derive
anything at write time: the 20 rows below are the exact output of the Stage 3
full-corpus dry run, reviewed and approved on 2026-08-16.

⚠️ DEPLOY THE MATCHER FIRST. Three of these filings are 2026-dated, and
`ingest_house_index` re-derives `member_id` for the current year's index every 6
hours. Running this against the OLD deployed matcher would put the correct values
in and have them overwritten within one cron cycle — which is precisely how the
2026-08-15 identity fix was silently undone.

── WHY EACH ROW CHANGES ────────────────────────────────────────────────────
  3 rows  wrong id -> right id : the namesake defect. `Collins, Mac` (left 2005)
          and `Begich, Nicholas` Sr. (left 1973) held filings made in 2026.
 17 rows  NULL -> id           : Delaney / Dunn / Carter. These resolve now
          because the date filter removes ~2,150 out-of-office competitors from
          the difflib pool, so the right person stops being outscored. They are
          the members `CLAUDE.md` documents as the correct answers for these
          filers, and they are currently NULL, so nothing correct is overwritten.

⚠️ Every row is guarded on its EXPECTED CURRENT VALUE. If any filing does not
still hold the value the review saw, the whole run refuses — so a concurrent
ingest cannot cause this to write over a value nobody reviewed.
"""
from __future__ import annotations

import argparse
import sqlite3

STAMP = "20260816"
BACKUP = "_preimage_filings_%s" % STAMP
BACKUP_TX = "_preimage_transactions_%s" % STAMP

# (filing_id, expected_current_member_id, new_member_id)
REVIEWED = [
    (1657, "C000640", "C001129"),   # 2026-01-20  Michael A. Collins
    (1901, None,      "C001103"),   # 2025-03-06  Earl Leroy Carter
    (1902, None,      "C001103"),   # 2025-04-23  Earl Leroy Carter
    (1903, None,      "C001103"),   # 2025-07-08  Earl Leroy Carter
    (1940, None,      "M001232"),   # 2025-03-20  April McClain Delaney
    (1941, None,      "M001232"),   # 2025-04-02  April McClain Delaney
    (1942, None,      "M001232"),   # 2025-05-02  April McClain Delaney
    (1943, None,      "M001232"),   # 2025-06-02  April McClain Delaney
    (1944, None,      "M001232"),   # 2025-07-02  April McClain Delaney
    (1945, None,      "M001232"),   # 2025-08-05  April McClain Delaney
    (1946, None,      "M001232"),   # 2025-09-02  April McClain Delaney
    (1947, None,      "M001232"),   # 2025-10-06  April McClain Delaney
    (1948, None,      "M001232"),   # 2025-11-04  April McClain Delaney
    (1949, None,      "M001232"),   # 2025-12-08  April McClain Delaney
    (1983, None,      "D000628"),   # 2025-03-10  Neal Patrick MD, Facs Dunn
    (1984, None,      "D000628"),   # 2025-04-03  Neal Patrick MD, Facs Dunn
    (1985, None,      "D000628"),   # 2025-08-13  Neal Patrick MD, Facs Dunn
    (1986, None,      "D000628"),   # 2025-10-01  Neal Patrick MD, Facs Dunn
    (2373, "B000315", "B001323"),   # 2026-06-12  Nicholas Begich
    (2374, "B000315", "B001323"),   # 2026-07-07  Nicholas Begich
]
IDS = tuple(r[0] for r in REVIEWED)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sync-transactions", action="store_true",
                    help="also set transactions.member_id from their parent filing")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    print("  db   : %s" % args.db)
    print("  mode : %s%s" % ("APPLY" if args.apply else "DRY RUN (rolled back)",
                             " +transactions" if args.sync_transactions else ""))

    # ── refuse unless every row still holds the reviewed value ──
    drift = []
    for fid, old, _new in REVIEWED:
        row = conn.execute("SELECT member_id FROM filings WHERE id=?", (fid,)).fetchone()
        if row is None or row["member_id"] != old:
            drift.append((fid, row["member_id"] if row else "<missing>", old))
    if drift:
        print("   🔴 REFUSING — %d row(s) no longer hold the reviewed value:" % len(drift))
        for fid, got, want in drift:
            print("      id=%s now %r, review saw %r" % (fid, got, want))
        return 1
    print("  guard: all %d filings still hold the reviewed value" % len(REVIEWED))

    conn.execute("BEGIN")
    for name, src in ((BACKUP, "filings"), (BACKUP_TX, "transactions")):
        if src == "transactions" and not args.sync_transactions:
            continue
        conn.execute("DROP TABLE IF EXISTS %s" % name)
        conn.execute("CREATE TABLE %s AS SELECT * FROM %s" % (name, src))
        n_s = conn.execute("SELECT COUNT(*) FROM %s" % src).fetchone()[0]
        n_b = conn.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0]
        print("  pre-image %-34s %d rows (source %d) %s"
              % (name, n_b, n_s, "OK" if n_b == n_s else "🔴"))
        if n_b != n_s:
            conn.execute("ROLLBACK"); print("   🔴 backup incomplete — REFUSING"); return 1

    print()
    print("  ── FILINGS ──")
    for fid, old, new in REVIEWED:
        conn.execute("UPDATE filings SET member_id=? WHERE id=?", (new, fid))
        print("     id=%-6s %-9s -> %s" % (fid, old or "NULL", new))

    tx_changed = 0
    if args.sync_transactions:
        print()
        print("  ── TRANSACTIONS (synced from their parent filing) ──")
        cur = conn.execute(
            "UPDATE transactions SET member_id = (SELECT member_id FROM filings f"
            "  WHERE f.id = transactions.filing_id)"
            " WHERE filing_id IN (%s)" % ",".join("?" * len(IDS)), IDS)
        tx_changed = cur.rowcount
        print("     %d transaction rows synced" % tx_changed)

    # ── nothing outside the reviewed set may move ──
    stray = conn.execute(
        "SELECT COUNT(*) FROM filings a JOIN %s b ON b.id=a.id"
        " WHERE a.id NOT IN (%s) AND IFNULL(a.member_id,'') != IFNULL(b.member_id,'')"
        % (BACKUP, ",".join("?" * len(IDS))), IDS).fetchone()[0]
    stray_tx = 0
    if args.sync_transactions:
        stray_tx = conn.execute(
            "SELECT COUNT(*) FROM transactions a JOIN %s b ON b.id=a.id"
            " WHERE a.filing_id NOT IN (%s) AND IFNULL(a.member_id,'') != IFNULL(b.member_id,'')"
            % (BACKUP_TX, ",".join("?" * len(IDS))), IDS).fetchone()[0]

    print()
    print("     filings changed                    : %d (expect %d)" % (len(REVIEWED), len(REVIEWED)))
    print("     filings outside the reviewed set   : %d %s" % (stray, "OK" if stray == 0 else "🔴"))
    if args.sync_transactions:
        print("     transactions synced                : %d" % tx_changed)
        print("     transactions outside those filings : %d %s" % (stray_tx, "OK" if stray_tx == 0 else "🔴"))

    if stray or stray_tx:
        conn.execute("ROLLBACK"); print("\n   🔴 ROLLED BACK — collateral change."); return 1
    if args.apply:
        conn.execute("COMMIT"); print("\n   APPLIED.")
    else:
        conn.execute("ROLLBACK"); print("\n   DRY RUN — rolled back, nothing written.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
