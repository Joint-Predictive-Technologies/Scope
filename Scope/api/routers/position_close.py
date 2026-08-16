#!/usr/bin/env python3
"""
POSITION CLOSE — the exit half of the ledger, and the arithmetic that turns two
recorded prices into a realized number without turning it into a track record.

════════════════════════════════════════════════════════════════════════════════════
APPEND-ONLY, NOT AN UPDATE — DECISION (a)
════════════════════════════════════════════════════════════════════════════════════
Closing writes a NEW row to `position_closes`. It does not UPDATE `positions`.

`api/routers/positions.py` contains no UPDATE and no DELETE anywhere, and that is not
tidiness — it is what makes "a `pass` row can never become a `buy`" STRUCTURALLY true
rather than a promise. A verifier confirmed it by reading, not by trusting. The moment
this module adds the first UPDATE, that property weakens from "impossible" to "nobody
has done it yet", and the second UPDATE has precedent to point at.

⚠️ THE COST, STATED RATHER THAN HIDDEN: `positions.status` is therefore the status AT
INSERT TIME and nothing ever moves it, so a closed position's row still reads 'open'.
Live state is derived by LEFT JOIN, and `OPEN_POSITIONS_SQL` below is the ONE place
that derivation exists. `scripts/insider_clusters.py` is this codebase's own hard-won
lesson on the alternative: fourteen "bugs" there turned out to be one bug fourteen
times — several places each reconstructing one truth from a different projection. One
derivation, imported, never re-expressed.

════════════════════════════════════════════════════════════════════════════════════
CLOSING KEYS ON position_id, NEVER ON TICKER — DECISION (b)
════════════════════════════════════════════════════════════════════════════════════
The entry session deliberately ALLOWS several open positions on one ticker, because two
taps on two different alerts are two real decisions and collapsing them loses data. That
makes `/sell AAPL` ambiguous by construction, and ticker a PROJECTION of identity rather
than identity itself — the same shape as the insider-cluster keys above.

So the command takes a `position_id`. Ambiguity is refused with the candidates listed,
never resolved by guessing "the oldest" or "the only one I found".

════════════════════════════════════════════════════════════════════════════════════
THE BENCHMARK IS COARSER THAN THE POSITION, ON PURPOSE AND ON THE RECORD — DECISION (c)
════════════════════════════════════════════════════════════════════════════════════
SPY's price at entry was never captured — the entry ledger stores no benchmark — so it
has to be reconstructed historically, and history is only available at DAILY CLOSE
granularity. `entry_price` and `exit_price`, by contrast, are tap-time last-trade prints.

That asymmetry is real and it is not fixable after the fact, so it is STORED instead of
smoothed over: `benchmark_basis` names the source, the granularity, and the actual dates
used on both legs. Both SPY legs use daily closes so the benchmark is at least internally
consistent (close-to-close), leaving exactly ONE asymmetry to state rather than two.

Concretely, what this means for a reader: if the position was entered at 10:00 and the
market rose through that day, SPY's close overstates where SPY was at the moment of
entry, and the comparison flatters the benchmark. The direction of the error is not
knowable per-position, only its existence.

⚠️ FOR A FUTURE WORK ORDER, DELIBERATELY NOT BUNDLED HERE: SPY's tap-time price should be
captured at ENTRY going forward, which removes this asymmetry for all future positions
while leaving existing ones honestly marked as reconstructed. That is a change to the
entry path and belongs to its own review.

════════════════════════════════════════════════════════════════════════════════════
OUT OF SCOPE, NAMED RATHER THAN HALF-BUILT
════════════════════════════════════════════════════════════════════════════════════
Partial sells, scaling out, short positions, and any close-reminder job are NOT
implemented. A close here is total and long-only. Half-supporting a partial sell would
mean a `positions` row whose remaining size is implied by subtraction across rows, and
the entry ledger records no size at all (see `realized_pnl_abs` below).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from jpt_common import db_connection                                   # noqa: E402
from scripts.rule_reddit_collector import _YF_HEADERS                  # noqa: E402
from api.routers.positions import (                                    # noqa: E402
    _live_quote, _log_denied, _utcnow_iso, validate_price,
)

BENCHMARK_SYMBOL = "SPY"

# ⚠️ THE ONE DERIVATION OF "OPEN". Imported, never re-expressed — see the module
# docstring on why several projections of one truth is this codebase's recurring bug.
# A position is open iff it is a 'buy' with no close row. A 'pass' is not a position.
OPEN_POSITIONS_SQL = """
    SELECT p.*
    FROM positions p
    LEFT JOIN position_closes c ON c.position_id = p.id
    WHERE p.decision = 'buy' AND c.id IS NULL
"""


def ensure_close_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS position_closes (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            -- UNIQUE is what makes a double-close impossible rather than unlikely: the
            -- constraint decides, not a read-then-write that two concurrent commands
            -- could both win.
            position_id          INTEGER NOT NULL UNIQUE,
            exit_price           REAL    NOT NULL,
            exit_at              TEXT    NOT NULL,
            quote_market_time    TEXT,
            quote_source         TEXT,
            quote_error          TEXT,
            -- ⚠️ PER SHARE. The entry ledger records no position SIZE, so a
            -- position-level dollar figure is not derivable and is not invented here.
            realized_pnl_abs     REAL,
            realized_pnl_pct     REAL,
            benchmark_symbol     TEXT,
            benchmark_return_pct REAL,
            benchmark_basis      TEXT,
            holding_days         REAL,
            created_at           TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


# ── the benchmark ────────────────────────────────────────────────────────────────

def _spy_daily_closes(start: datetime, end: datetime) -> tuple[list | None, str | None]:
    """[(date, close), …] over [start-pad, end+pad], or (None, error).

    Same host, same `/v8/finance/chart/` endpoint and same `_YF_HEADERS` as the quote
    path — but `interval=1d` closes, because a price from weeks ago simply does not
    exist at tap-time granularity. Padded back so a weekend or holiday entry still has
    a close at-or-before it.
    """
    p1 = int((start - timedelta(days=10)).timestamp())
    p2 = int((end + timedelta(days=2)).timestamp())
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{BENCHMARK_SYMBOL}"
            f"?interval=1d&period1={p1}&period2={p2}",
            headers=_YF_HEADERS, timeout=15,
        )
    except Exception as exc:
        return None, f"benchmark request failed: {type(exc).__name__}"
    if not r.ok:
        return None, f"benchmark endpoint returned HTTP {r.status_code}"
    try:
        res = r.json()["chart"]["result"][0]
        stamps = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
    except Exception:
        return None, "benchmark response was not in the expected shape"

    out = []
    for ts, c in zip(stamps, closes):
        if c is None:
            continue
        # Every benchmark close goes through the SAME gate as every other price in this
        # ledger. This is the third caller `validate_price` exists to prevent becoming a
        # third COPY of the +inf/bool check.
        price, err = validate_price(c, "benchmark close")
        if price is None:
            continue                       # skip the bad bar, do not abort the window
        out.append((datetime.fromtimestamp(ts, tz=timezone.utc).date(), price))
    if not out:
        return None, "benchmark returned no usable closes"
    return out, None


def _close_at_or_before(series: list, when: datetime):
    """The last close at or before `when`. None if the window does not reach back far
    enough — which is refused rather than approximated with the earliest available bar."""
    day = when.date()
    candidates = [(d, c) for d, c in series if d <= day]
    return candidates[-1] if candidates else None


def benchmark_return(entry_at: datetime, exit_at: datetime):
    """(pct, basis, error). Close-to-close SPY over the holding window.

    Returns the ACTUAL dates used inside `basis`, because they are not always the entry
    and exit dates — a Saturday entry resolves to Friday's close — and a benchmark whose
    real window differs from the `holding_days` beside it, without saying so, is exactly
    the kind of quietly-wrong number this ledger exists to avoid.
    """
    series, err = _spy_daily_closes(entry_at, exit_at)
    if series is None:
        return None, None, err
    a = _close_at_or_before(series, entry_at)
    b = _close_at_or_before(series, exit_at)
    if a is None or b is None:
        return None, None, "no benchmark close at or before the entry/exit date"
    (d0, c0), (d1, c1) = a, b
    if d0 == d1:
        # Entered and exited inside one session: close-to-close is 0% by construction,
        # which is not a benchmark, it is an artefact of the granularity.
        return None, None, (f"entry and exit resolve to the same benchmark close "
                            f"({d0}) — window too short to compare at daily granularity")
    pct = (c1 - c0) / c0 * 100
    # ⚠️ THE BENCHMARK WINDOW IS OFTEN NOT THE HOLDING WINDOW, AND THE DIFFERENCE IS
    # STATED IN DAYS RATHER THAN LEFT AS DATE ARITHMETIC FOR THE READER. A position held
    # Tue->Sun has a holding window of 5 days but a benchmark window of Tue->Fri, because
    # no SPY close exists for a day the market was shut. That is unavoidable, but a
    # `holding_days: 5.2` sitting beside an unlabelled 3-day benchmark return is exactly
    # the quietly-mismatched comparison this ledger is supposed to make impossible.
    bench_days = (d1 - d0).days
    basis = (f"{BENCHMARK_SYMBOL} daily close {d0}->{d1} ({c0:.4f}->{c1:.4f}) "
             f"spanning {bench_days} calendar days; source=yahoo:chart:close; "
             f"granularity=daily_close, while the position's own prices are tap-time "
             f"last-trade prints — the benchmark leg is coarser and was reconstructed "
             f"after the fact, not captured at entry. Where this span differs from "
             f"holding_days, the market was shut at one end of the holding window.")
    return pct, basis, None


# ── closing ──────────────────────────────────────────────────────────────────────

def open_positions(conn) -> list[dict]:
    ensure_close_table(conn)
    return [dict(r) for r in conn.execute(OPEN_POSITIONS_SQL + " ORDER BY p.id")]


def close_position(position_id: int) -> dict:
    """Write exactly one close row, or none."""
    conn = db_connection()
    try:
        from api.routers.positions import ensure_table
        ensure_table(conn)
        ensure_close_table(conn)

        pos = conn.execute("SELECT * FROM positions WHERE id = ?",
                           (position_id,)).fetchone()
        if pos is None:
            return {"ok": False, "closed": False,
                    "reason": f"no position with id {position_id}"}
        pos = dict(pos)
        if pos["decision"] != "buy":
            return {"ok": False, "closed": False,
                    "reason": f"position {position_id} is a '{pos['decision']}', "
                              f"not an open position"}

        already = conn.execute(
            "SELECT id, exit_at FROM position_closes WHERE position_id = ?",
            (position_id,)).fetchone()
        if already:
            return {"ok": False, "closed": False,
                    "reason": f"position {position_id} was already closed at "
                              f"{already['exit_at']}"}

        entry_price = pos["entry_price"]
        if entry_price is None:
            # Unreachable for a written 'buy' (that is the entry table's core invariant)
            # but a P&L divided by a missing entry is worth refusing explicitly.
            return {"ok": False, "closed": False,
                    "reason": f"position {position_id} has no entry price"}

        exit_price, mtime, qerr = _live_quote(pos["ticker"])
        if exit_price is None:
            # Unlike a 'pass' on the entry side, a close with no price is not a usable
            # record — there is nothing to compute against — so this fails closed with
            # no row at all rather than storing a decision-shaped stub.
            _log_denied("POSITION_CLOSE_QUOTE_FAILED",
                        f"{pos['ticker']} position={position_id}: {qerr}")
            return {"ok": False, "closed": False, "reason": qerr}

        exit_at = _utcnow_iso()
        entry_dt = _parse_iso(pos["entry_at"])
        exit_dt = _parse_iso(exit_at)
        holding_days = (exit_dt - entry_dt).total_seconds() / 86400

        # ⚠️ COMPUTED FROM THE TWO STORED PRICES AND NOTHING ELSE. No re-fetch of the
        # entry, no rounded intermediate, no substituted "current" price standing in for
        # the recorded one.
        pnl_abs = exit_price - entry_price
        pnl_pct = (exit_price - entry_price) / entry_price * 100

        bench_pct, bench_basis, bench_err = benchmark_return(entry_dt, exit_dt)
        if bench_pct is None:
            # The benchmark is REQUIRED beside every P&L, so its absence is recorded in
            # the basis field rather than left as a silent NULL a reader might not notice.
            bench_basis = f"UNAVAILABLE — {bench_err}"

        cur = conn.execute(
            """INSERT OR IGNORE INTO position_closes
               (position_id, exit_price, exit_at, quote_market_time, quote_source,
                quote_error, realized_pnl_abs, realized_pnl_pct, benchmark_symbol,
                benchmark_return_pct, benchmark_basis, holding_days)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (position_id, exit_price, exit_at, mtime,
             "yahoo:chart:regularMarketPrice", qerr,
             pnl_abs, pnl_pct, BENCHMARK_SYMBOL, bench_pct, bench_basis, holding_days))
        conn.commit()

        if cur.rowcount == 0:
            # Lost a genuine race to a concurrent close of the same position.
            row = conn.execute(
                "SELECT exit_at FROM position_closes WHERE position_id = ?",
                (position_id,)).fetchone()
            return {"ok": False, "closed": False,
                    "reason": f"position {position_id} was already closed"
                              + (f" at {row['exit_at']}" if row else "")}

        return {"ok": True, "closed": True, "close_id": cur.lastrowid,
                "position_id": position_id, "ticker": pos["ticker"],
                "entry_price": entry_price, "exit_price": exit_price,
                "realized_pnl_pct": pnl_pct, "realized_pnl_abs": pnl_abs,
                "benchmark_return_pct": bench_pct, "benchmark_basis": bench_basis,
                "holding_days": holding_days}
    finally:
        conn.close()


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace(" ", "T"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def closed_count(conn) -> int:
    ensure_close_table(conn)
    return conn.execute("SELECT COUNT(*) FROM position_closes").fetchone()[0]


# ── presentation ─────────────────────────────────────────────────────────────────

# The disclaimer is a constant, not a phrase each caller retypes, so no surface can
# render a P&L while forgetting it.
QUOTE_NOT_A_FILL = (
    "These are QUOTES, not fills: no fees, no spread, no slippage, and a quote outside "
    "market hours may not have been transactable at all."
)


def format_pnl(*, ticker: str, pnl_pct: float, benchmark_pct: float | None,
               benchmark_basis: str, n_closed: int, holding_days: float,
               pnl_abs: float | None = None) -> str:
    """Render one realized result. Benchmark and n are REQUIRED KEYWORD ARGUMENTS.

    ⚠️ THE SIGNATURE IS THE ENFORCEMENT. The honesty rule — a P&L figure never appears
    without the benchmark over the identical window and the number of closed positions
    behind it — is not a convention a caller can forget here, because a call that omits
    either one is a TypeError. Making the honest form the only expressible form is
    stronger than a style rule and much stronger than a code review.

    Deliberately absent, and they are absent because at this sample size they would all
    be false precision rather than because they are hard: annualized returns, win rate,
    Sharpe or any risk-adjusted figure, and 'track record' framing of any kind.
    """
    lines = [f"Closed {ticker} — {pnl_pct:+.2f}% per share over {holding_days:.1f} days"]
    if pnl_abs is not None:
        # "per share" is stated because the ledger records no position size, so a
        # position-level dollar figure does not exist and must not be implied.
        lines.append(f"Move: {pnl_abs:+.2f} per share")
    if benchmark_pct is None:
        lines.append(f"{BENCHMARK_SYMBOL} benchmark: UNAVAILABLE")
    else:
        # ⚠️ NOT "over the same window", which would be literally false whenever the
        # market was shut at either end of the holding period — a Tue->Sun hold gets a
        # Tue->Fri benchmark. The exact span is in the basis line below.
        lines.append(f"{BENCHMARK_SYMBOL} benchmark, close-to-close: {benchmark_pct:+.2f}% "
                     f"({pnl_pct - benchmark_pct:+.2f}% vs benchmark)")
    lines.append(f"Basis: {benchmark_basis}")
    lines.append(f"This is closed position {n_closed} of {n_closed} recorded. "
                 f"No performance conclusion can be drawn from a sample this size.")
    lines.append(QUOTE_NOT_A_FILL)
    return "\n".join(lines)


def format_open_positions(rows: list[dict]) -> str:
    """The id is the point of this listing — closing keys on it, so it has to be
    discoverable or keying on it is unusable in practice."""
    if not rows:
        return "No open positions."
    out = ["Open positions — close with /sell <id>", ""]
    for r in rows:
        entered = str(r.get("entry_at") or "")[:16].replace("T", " ")
        price = r.get("entry_price")
        out.append(f"  id {r['id']}  {r['ticker']}  entered {entered} UTC"
                   + (f"  at {price:.2f}" if price is not None else ""))
    return "\n".join(out)


# ── the /sell and /positions commands ────────────────────────────────────────────

def handle_command(text: str) -> str:
    """Dispatch a Telegram *message* command. Returns the reply text.

    ⚠️ AUTH IS NOT CHECKED HERE, AND THAT IS DELIBERATE RATHER THAN MISSING. Telegram
    delivers every update type to ONE webhook URL, so this is reached through the SAME
    endpoint and therefore the SAME `_secret_ok` + `_chat_allowed` gate the callback
    path uses. There is no second inbound surface to ship with weaker auth, because
    there is no second surface — which is a stronger guarantee than two endpoints that
    happen to call the same two helpers.
    """
    parts = (text or "").strip().split()
    if not parts:
        return ""
    cmd = parts[0].lower().lstrip("/").split("@")[0]

    if cmd in ("positions", "open"):
        conn = db_connection()
        try:
            return format_open_positions(open_positions(conn))
        finally:
            conn.close()

    if cmd != "sell":
        return ""

    if len(parts) < 2:
        conn = db_connection()
        try:
            return ("Usage: /sell <position_id>\n\n" +
                    format_open_positions(open_positions(conn)))
        finally:
            conn.close()

    arg = parts[1]
    if not arg.isdigit():
        # A ticker was passed. Refuse and list, never guess — several open positions on
        # one ticker are explicitly allowed, so a ticker does not identify a position.
        conn = db_connection()
        try:
            rows = [r for r in open_positions(conn)
                    if r["ticker"].upper() == arg.upper().lstrip("$")]
        finally:
            conn.close()
        if not rows:
            return (f"No open position for {arg}. Close by id: /sell <position_id> "
                    f"(see /positions).")
        return (f"{arg} is ambiguous — a ticker does not identify a position, and "
                f"{len(rows)} open position(s) match. Close by id:\n\n"
                + format_open_positions(rows))

    result = close_position(int(arg))
    if not result.get("closed"):
        return f"Not closed — {result.get('reason')}"

    conn = db_connection()
    try:
        n = closed_count(conn)
    finally:
        conn.close()
    return format_pnl(
        ticker=result["ticker"], pnl_pct=result["realized_pnl_pct"],
        pnl_abs=result["realized_pnl_abs"],
        benchmark_pct=result["benchmark_return_pct"],
        benchmark_basis=result["benchmark_basis"],
        n_closed=n, holding_days=result["holding_days"])
