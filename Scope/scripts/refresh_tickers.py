#!/usr/bin/env python3
"""Scheduled refresh of the `tickers` reference table — THE SAFE HALF ONLY.

`resolve_tickers.main()` does two things. This runs one of them.

  upsert_sec_tickers      reference refresh, conflict key `symbol`. Safe. <- THIS
  resolve_transactions    UPDATEs congressional `transactions.ticker_id`. NOT safe to
                          run unattended, and deliberately unreachable from here.

WHY THIS FILE EXISTS AS A SEPARATE SCRIPT. The scheduler invokes jobs as
`[sys.executable, <script>, "--emit-alerts"]`, so it cannot pass a "safe mode" flag —
scheduling `resolve_tickers.py` itself would run BOTH halves, including the congressional
write. The separation therefore has to be structural: this module never calls
`resolve_transactions`, which `tests/test_ticker_refresh_job.py` asserts by walking the
call graph rather than trusting this comment.

WHY IT IS SCHEDULED. `tickers` had exactly one writer (`resolve_tickers.upsert_sec_tickers`)
and no schedule at all, so it only ever refreshed when someone ran it by hand. RULE_16's CINS fallback
resolves institutional holdings against it, so staleness quietly costs coverage with no
error anywhere — the failure mode is silence, which is why it needs a cadence rather than
a reminder.

WEEKLY IS AMPLE. Company -> ticker mappings change on the order of IPOs, delistings and
renames, so a daily poll would add load for no signal. Measured on the local DB: 10,619
rows with updated_at spanning 2026-06-07..2026-07-09 — 18 days stale as of 2026-07-27,
and with no schedule that only grows. (An earlier draft of this comment said "seven
weeks". No measurement I have supports that figure; prod is UNVERIFIED — settle it with
`SELECT COUNT(*), MIN(updated_at), MAX(updated_at) FROM tickers;`.)

Failure is loud: the scheduler's `_run_rule` guarantees a SCHEDULER_JOB_FAILURE
activity_log row for any non-zero exit, import-time crash or timeout, and a successful
run writes its own REFRESH_TICKERS row.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from resolve_tickers import refresh_tickers_only  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # The scheduler passes --emit-alerts to EVERY job. This one emits no alerts, but it
    # must still accept the flag: an unrecognised argument makes argparse exit 2, which
    # is how the morning brief silently failed 100% of its runs for months.
    p.add_argument("--emit-alerts", action="store_true",
                   help="accepted for scheduler compatibility; this job emits no alerts")
    return p


def main() -> int:
    build_parser().parse_args()
    refresh_tickers_only()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
