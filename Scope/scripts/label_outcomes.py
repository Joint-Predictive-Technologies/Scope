#!/usr/bin/env python3
"""
label_outcomes.py — LABEL_OUTCOMES

Attaches forward-return data to every alert once its +20-trading-day horizon has
elapsed, so months from now we can ask "do RULE_06 HIGH alerts outperform?" and
"does opportunity_score track realized alpha?". This is the seed data for the
calibration / learned-weight work — it does NOT interpret anything itself.

Design notes:
- Separate `alert_outcomes` table (not columns on alerts): different write
  cadence, keeps alerts immutable, lets the labeling schema evolve freely.
- Prices come from the same Yahoo Finance chart endpoint run_backtest.py uses
  (reuse, don't re-invent). Returns are decimals (0.045 = 4.5%). SPY over the
  same trading days gives the benchmark, so downstream can compute alpha.
- +20 trading days ≈ 28 calendar days; we only label alerts older than that.
- Non-equity / unpriceable alerts get status='unavailable' (never crash).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, record_activity

_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
BENCHMARK = "SPY"
HORIZON_CALENDAR_DAYS = 28  # ~20 trading days must have elapsed before labeling
_spy_cache: dict[str, list | None] = {}


def ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_outcomes (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id              INTEGER UNIQUE,
            ticker                TEXT,
            price_at_detection    REAL,
            price_1d              REAL,
            price_5d              REAL,
            price_20d             REAL,
            return_1d             REAL,
            return_5d             REAL,
            return_20d            REAL,
            benchmark_return_1d   REAL,
            benchmark_return_5d   REAL,
            benchmark_return_20d  REAL,
            status                TEXT,
            note                  TEXT,
            labeled_at            TEXT
        );
        """
    )
    conn.commit()


def _fetch_closes(ticker: str, alert_ts: str) -> list[float] | None:
    """Daily closes from the alert date forward (trading days only)."""
    try:
        dt = datetime.fromisoformat(alert_ts.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        p1 = int(dt.timestamp())
        p2 = p1 + 45 * 86400  # enough calendar days to cover 20 trading days
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?interval=1d&period1={p1}&period2={p2}"
        )
        r = requests.get(url, headers=_YF_HEADERS, timeout=10)
        data = r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return closes or None
    except Exception:
        return None


def _spy_closes(alert_ts: str) -> list[float] | None:
    key = alert_ts[:10]
    if key not in _spy_cache:
        _spy_cache[key] = _fetch_closes(BENCHMARK, alert_ts)
    return _spy_cache[key]


def _ret(closes: list[float], n: int) -> tuple[float | None, float | None]:
    """(price_at_n, return_decimal) for the n-th trading day, or (None,None)."""
    if not closes or len(closes) <= n:
        return None, None
    base = closes[0]
    if not base:
        return None, None
    price = closes[n]
    return round(price, 4), round((price - base) / base, 4)


# ── Fabricated-signal quarantine ─────────────────────────────────────────────
#
# `alert_outcomes` is the seed corpus for per-rule realized win rate — the reserved
# `historical_win_rate` input to calculate_opportunity_score, today a fixed 0.5
# placeholder. Whatever ends up in this table eventually PROMOTES OR DEMOTES a rule.
#
# RULE_15 emitted 9 alerts into production before its repair landed, and every one of
# them is fabricated: the rule attributed 8-K sentiment by NAME, so Boeing was scored
# from *BA Credit Card Trust* filings (BA +109%), plus XOM +637% and RTX +2557% from a
# denominator bug. Those are not noisy signals — they measure the WRONG COMPANY.
#
# Left alone they would become RULE_15's entire calibration sample (it has no other
# alerts), and a +637% "win" would make the rule look like the best performer in the
# system, feeding win_rate*10 straight back into opportunity_score. A rule would be
# promoted on the strength of signals it never really produced.
#
# So they are marked `excluded` rather than deleted. THE ALERTS THEMSELVES STAY — they
# are the evidence of what the bug did, and deleting them would erase the only proof it
# reached production (decided 2026-07-27). This excludes their OUTCOMES from
# calibration, which is a different object and a different question.
#
# Same epoch as the rule's own quarantine (rule_15_earnings_nlp.REPAIR_EPOCH), so the
# two agree about which rows are pre-repair. Forward-only: post-repair RULE_15 alerts
# label normally. To undo, empty this dict — nothing else encodes the decision.
QUARANTINED_BEFORE = {
    "RULE_15": "2026-07-27 00:00:00",
}
QUARANTINE_NOTE = ("excluded from calibration: pre-repair {rule} alert (name-based "
                   "attribution / denominator bug) — fabricated, not a real signal")


def _quarantined(rule: str | None, created_at: str | None) -> bool:
    """Is this alert a known-fabricated pre-repair row?"""
    epoch = QUARANTINED_BEFORE.get((rule or "").strip())
    return bool(epoch and created_at and str(created_at) < epoch)


def _is_equity_ticker(ticker: str | None) -> bool:
    if not ticker:
        return False
    t = ticker.strip()
    # Normalized single equity symbol: no spaces (multi-ticker baskets skipped).
    return bool(t) and " " not in t


def run(limit: int | None = None) -> dict:
    t0 = time.time()
    conn = db_connection()
    ensure_table(conn)

    lim = "LIMIT ?" if limit else ""
    params = (limit,) if limit else ()
    eligible = conn.execute(
        f"""
        SELECT a.id, a.ticker, a.created_at, a.rule
        FROM alerts a
        LEFT JOIN alert_outcomes o ON a.id = o.alert_id
        WHERE datetime(a.created_at) <= datetime('now', '-{HORIZON_CALENDAR_DAYS} days')
          AND (o.alert_id IS NULL OR o.status = 'pending')
        ORDER BY a.created_at DESC
        {lim}
        """,
        params,
    ).fetchall()

    still_pending = conn.execute(
        f"SELECT COUNT(*) n FROM alerts "
        f"WHERE datetime(created_at) > datetime('now', '-{HORIZON_CALENDAR_DAYS} days')"
    ).fetchone()["n"]

    labeled = unavailable = excluded = 0
    for a in eligible:
        ticker = (a["ticker"] or "").strip()
        now = datetime.now(timezone.utc).isoformat()

        # Fabricated rows are marked and never priced — a real forward return on a
        # signal that measured the wrong company is worse than no data, because it
        # looks like data.
        if _quarantined(a["rule"], a["created_at"]):
            _upsert(conn, a["id"], ticker or None, None, {}, "excluded",
                    QUARANTINE_NOTE.format(rule=a["rule"]), now)
            excluded += 1
            continue

        if not _is_equity_ticker(ticker):
            note = "no ticker" if not ticker else "non-single-equity (basket/multi-ticker)"
            _upsert(conn, a["id"], ticker or None, None, {}, "unavailable", note, now)
            unavailable += 1
            continue

        closes = _fetch_closes(ticker, a["created_at"])
        if not closes or len(closes) < 2:
            _upsert(conn, a["id"], ticker, None, {}, "unavailable",
                    "no price data (delisted / unpriceable)", now)
            unavailable += 1
            continue

        spy = _spy_closes(a["created_at"])
        p1, r1 = _ret(closes, 1)
        p5, r5 = _ret(closes, 5)
        p20, r20 = _ret(closes, 20)
        b1 = _ret(spy, 1)[1] if spy else None
        b5 = _ret(spy, 5)[1] if spy else None
        b20 = _ret(spy, 20)[1] if spy else None
        # Complete only once the 20-day close exists; otherwise leave pending.
        status = "complete" if p20 is not None else "pending"
        fields = {
            "price_at_detection": round(closes[0], 4),
            "price_1d": p1, "price_5d": p5, "price_20d": p20,
            "return_1d": r1, "return_5d": r5, "return_20d": r20,
            "benchmark_return_1d": b1, "benchmark_return_5d": b5,
            "benchmark_return_20d": b20,
        }
        _upsert(conn, a["id"], ticker, fields["price_at_detection"], fields,
                status, None, now)
        if status == "complete":
            labeled += 1

    # Rows already labelled `complete` are not in `eligible` (the query only picks up
    # NULL/pending), so the fabricated returns ALREADY in the table need their own
    # sweep. Idempotent, and it never touches a row that is already excluded.
    for rule, epoch in QUARANTINED_BEFORE.items():
        swept = conn.execute(
            """UPDATE alert_outcomes SET status = 'excluded', note = ?
               WHERE status != 'excluded' AND alert_id IN (
                   SELECT id FROM alerts WHERE rule = ? AND created_at < ?)""",
            (QUARANTINE_NOTE.format(rule=rule), rule, epoch)).rowcount
        excluded += max(swept, 0)

    conn.commit()
    notes = (f"labeled={labeled}, unavailable={unavailable}, excluded={excluded}, "
             f"still_pending={still_pending}")
    record_activity("LABEL_OUTCOMES", scanned=len(eligible), flagged=unavailable,
                    emitted=labeled, duration_seconds=round(time.time() - t0, 2),
                    notes=notes)
    print(f"[LABEL_OUTCOMES] {notes}")
    conn.close()
    return {"labeled": labeled, "unavailable": unavailable, "excluded": excluded,
            "still_pending": still_pending}


def _upsert(conn, alert_id, ticker, price_at, fields, status, note, labeled_at) -> None:
    conn.execute(
        """
        INSERT INTO alert_outcomes (
            alert_id, ticker, price_at_detection, price_1d, price_5d, price_20d,
            return_1d, return_5d, return_20d,
            benchmark_return_1d, benchmark_return_5d, benchmark_return_20d,
            status, note, labeled_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(alert_id) DO UPDATE SET
            ticker=excluded.ticker,
            price_at_detection=excluded.price_at_detection,
            price_1d=excluded.price_1d, price_5d=excluded.price_5d, price_20d=excluded.price_20d,
            return_1d=excluded.return_1d, return_5d=excluded.return_5d, return_20d=excluded.return_20d,
            benchmark_return_1d=excluded.benchmark_return_1d,
            benchmark_return_5d=excluded.benchmark_return_5d,
            benchmark_return_20d=excluded.benchmark_return_20d,
            status=excluded.status, note=excluded.note, labeled_at=excluded.labeled_at
        """,
        (alert_id, ticker, price_at,
         fields.get("price_1d"), fields.get("price_5d"), fields.get("price_20d"),
         fields.get("return_1d"), fields.get("return_5d"), fields.get("return_20d"),
         fields.get("benchmark_return_1d"), fields.get("benchmark_return_5d"),
         fields.get("benchmark_return_20d"), status, note, labeled_at),
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Label alerts with forward returns.")
    p.add_argument("--limit", type=int, default=None, help="Cap alerts processed (testing).")
    p.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    run(limit=args.limit)
