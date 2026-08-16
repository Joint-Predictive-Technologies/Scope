#!/usr/bin/env python3
"""Populate `member_terms` from unitedstates/congress-legislators.

    python scripts/load_member_terms.py                 # DRY RUN
    python scripts/load_member_terms.py --apply         # writes member_terms only

`match_member_id` needs to know WHEN a member served in order to tell two people
with the same name apart. `members` carries no term dates at all — only
`is_current`, which is 1 on 2,689 of 2,692 rows and therefore disambiguates
nothing. This fills the gap m017 created.

⚠️ THIS SCRIPT WRITES ONLY `member_terms`. It never touches `members`,
`filings`, `transactions` or `alerts`. The re-matching of existing filings is a
separate, separately-gated step (`scripts/rematch_filings_dryrun.py`).

── WHY THE FEED AND NOT congress.gov ───────────────────────────────────────
`bootstrap_members.py` reads congress.gov's *list* endpoint, which returns a
`terms` summary carrying only `startYear` — year granularity cannot decide a
filing dated 2026-01-20 against a term that ended 2026-01-05. The
unitedstates/congress-legislators feed carries full ISO start/end dates for
every term, and covers 2,692 of 2,692 members in the roster (verified).

── CACHING ─────────────────────────────────────────────────────────────────
Both feed files are cached on disk and reused unless --refresh is passed. They
are ~15MB combined and change at most daily; re-downloading them on every run
would be rude to a free community-run host.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from jpt_common import db_connection  # noqa: E402

BASE = "https://unitedstates.github.io/congress-legislators/%s.json"
FILES = ("legislators-current", "legislators-historical")
SOURCE = "unitedstates/congress-legislators"
UA = "Scope Congressional Trade Tracker (sloppysecondstbb@gmail.com)"
DEFAULT_CACHE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache", "legislators"
)


def load_feed(cache_dir: str, refresh: bool) -> list[dict]:
    os.makedirs(cache_dir, exist_ok=True)
    people: list[dict] = []
    for name in FILES:
        path = os.path.join(cache_dir, name + ".json")
        if refresh or not os.path.exists(path) or os.path.getsize(path) == 0:
            req = urllib.request.Request(BASE % name, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as resp:
                blob = resp.read()
            with open(path, "wb") as fh:
                fh.write(blob)
            print("   downloaded %-26s %d bytes" % (name, len(blob)))
        else:
            print("   cached     %-26s %d bytes" % (name, os.path.getsize(path)))
        people.extend(json.load(open(path)))
    return people


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-download the feed")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE)
    args = ap.parse_args()

    print("  mode : %s" % ("APPLY" if args.apply else "DRY RUN"))
    people = load_feed(args.cache_dir, args.refresh)
    print("   feed people: %d" % len(people))

    by_bioguide: dict[str, list[dict]] = {}
    for p in people:
        b = (p.get("id") or {}).get("bioguide")
        if b:
            by_bioguide.setdefault(b, []).extend(p.get("terms") or [])

    conn = db_connection()
    roster = [r["bioguide_id"] for r in conn.execute("SELECT bioguide_id FROM members")]
    print("   roster members: %d" % len(roster))

    rows, missing = [], []
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    for b in roster:
        terms = by_bioguide.get(b)
        if not terms:
            missing.append(b)
            continue
        for t in terms:
            start, end = t.get("start"), t.get("end")
            if not start or not end:
                # A term with no dates cannot constrain anything. Skipped and
                # counted rather than stored with an invented boundary.
                continue
            rows.append((b, start, end, t.get("type"), t.get("state"),
                         str(t.get("district")) if t.get("district") is not None else None,
                         SOURCE, now))

    print()
    print("   members with terms : %d" % (len(roster) - len(missing)))
    print("   members MISSING    : %d %s" % (len(missing), missing[:5]))
    print("   term rows to write : %d" % len(rows))
    if rows:
        spans = [(r[1], r[2]) for r in rows]
        print("   earliest term start: %s   latest term end: %s"
              % (min(s for s, _ in spans), max(e for _, e in spans)))

    if args.apply:
        conn.execute("DELETE FROM member_terms")
        conn.executemany(
            """INSERT OR REPLACE INTO member_terms
               (bioguide_id, term_start, term_end, chamber, state, district, source, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM member_terms").fetchone()[0]
        d = conn.execute("SELECT COUNT(DISTINCT bioguide_id) FROM member_terms").fetchone()[0]
        print("\n   APPLIED. member_terms holds %d rows for %d members." % (n, d))
    else:
        print("\n   DRY RUN — nothing written.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
