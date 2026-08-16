#!/usr/bin/env python3
"""
POSITION LEDGER — records a human decision, at the moment it was made, at a price
that was real when it was made.

This is a RECORDING mechanism. It is not a recommendation mechanism, it does not
rank anything, and nothing in the convergence gate reads it. Its entire purpose is
to accumulate the one kind of data Scope does not yet have: prospective, decided-in-
advance outcomes with an entry price nobody chose in hindsight.

════════════════════════════════════════════════════════════════════════════════════
WHY THE ENTRY PRICE IS THE LOAD-BEARING FIELD, AND WHY IT FAILS CLOSED
════════════════════════════════════════════════════════════════════════════════════
Every future validation of anything Scope scores will be built on this table. A
missing row is recoverable — the human taps again, or the decision is simply absent
from the sample. A WRONG entry price is not recoverable, because nothing downstream
can tell it from a right one: it is a plausible number in the correct column, and it
silently biases every statistic computed over it, forever.

So there is exactly one way an `entry_price` may come into existence: a live quote
fetched synchronously at tap time, in `_live_quote()`, which returns None rather than
anything approximate. There is deliberately NO default, NO cached fallback, NO
previous-close substitute and NO retry that could reuse an older response. If the
quote does not resolve, the buy row is not written at all and the human is told.

⚠️ `regularMarketPrice`, NOT THE DAILY CLOSE SERIES, AND THE DIFFERENCE IS NOT
COSMETIC. `scripts/position_sizing.py` reads the same Yahoo chart response for a
`last_close` — correct for its purpose (a market-cap reconciliation), wrong for this
one. That series is daily bars: tapping Buy at 14:00 on a Tuesday would record
Monday's close and stamp it with Tuesday's timestamp, which is back-dating the entry
by a day and a price move. `meta.regularMarketPrice` is the last trade, and it
carries its own `meta.regularMarketTime`, so what was recorded and when it was struck
are two separate stored facts rather than one conflated one. (Measured on AAPL: the
meta field gives 305.93, the daily series gives 305.92999267578125 — the meta field
is also the one that is exact to the cent.)

⚠️ SAME FETCH PATH, NOT A SECOND ONE. Same host, same `/v8/finance/chart/` endpoint,
same `_YF_HEADERS` imported from `rule_reddit_collector` — that browser User-Agent is
load-bearing (a bare client gets 429) and a second copy of it is a second thing to
forget when it changes. Only the field read out of the response differs.

════════════════════════════════════════════════════════════════════════════════════
WHAT IS DELIBERATELY NOT CLAIMED HERE
════════════════════════════════════════════════════════════════════════════════════
`worthiness_score_at_entry` is recorded UNVALIDATED and is NULL today, because no
`worthiness_scores` table exists in production (checked, 57 tables, not among them —
the scoring job was designed but never approved past its Stage 1 gate). Per
`SESSION-2026-08-16-worthiness-score-fixes`, that score has no demonstrated predictive
power: r = 0.0005 across prod's full 3,677-outcome population, and its 17-ticker
sanity check was anchored entirely on gate-INELIGIBLE alerts, so it did not test what
it claimed to test. The column exists so that prospective data can one day evaluate
it. It must never be rendered anywhere in language implying it forecasts a return.

`worthiness_score_version` exists for the same reason. A score without the version of
the formula that produced it is an uninterpretable number the moment the formula
moves, and this one is expected to move.
"""
from __future__ import annotations

import hmac
import os
import sys
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from jpt_common import db_connection, normalize_ticker              # noqa: E402
from scripts.rule_reddit_collector import _YF_HEADERS               # noqa: E402

router = APIRouter()

QUOTE_SOURCE = "yahoo:chart:regularMarketPrice"

# The formula version stamped on every row. Bump when the worthiness definition moves.
# "unvalidated-v0" is not a placeholder for a better name — it IS the status: there is
# no validated version, and a row that says so is honest about what it recorded.
WORTHINESS_VERSION = "unvalidated-v0"

# A quote older than this is refused as suspect rather than recorded. Sized to clear a
# normal weekend (~2.5 days) and a long holiday weekend (~4), so it rejects a broken or
# delisted feed without rejecting an ordinary Saturday tap. The quote's own market time
# is stored regardless, so weekend staleness stays VISIBLE in the data rather than
# being smoothed away by this threshold.
MAX_QUOTE_AGE_SECONDS = 5 * 24 * 3600


# ── schema ───────────────────────────────────────────────────────────────────────
#
# ⚠️ DEFINED HERE, NOT IN `jpt_common`'s migration path, and that is a deliberate
# reading of this work order's "zero edits to any scoring file" rule — `jpt_common`
# holds `insert_alert` and the scoring engine, so this feature does not open it at
# all. `scripts/label_outcomes.py::ensure_table` is the existing precedent for a
# significant table owning its own DDL. The cost is that this must be called before
# first write; `_ensure_table` is therefore invoked on every insert path.
def ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            -- what was decided, and by which tap
            decision                  TEXT    NOT NULL,     -- 'buy' | 'pass'
            callback_query_id         TEXT    NOT NULL UNIQUE,
            chat_id                   TEXT,
            ticker                    TEXT    NOT NULL,
            -- the price, and the two DIFFERENT times that matter about it
            entry_price               REAL,                 -- NULL on 'pass', never NULL on a written 'buy'
            entry_at                  TEXT    NOT NULL,     -- when the human decided
            quote_source              TEXT,
            quote_fetched_at          TEXT,                 -- when Scope asked
            quote_market_time         TEXT,                 -- when the trade actually printed
            quote_error               TEXT,                 -- why there is no price (pass rows only)
            -- provenance
            triggering_alert_id       INTEGER,
            worthiness_score_at_entry REAL,                 -- NULLABLE, UNVALIDATED, see module docstring
            worthiness_score_version  TEXT,
            -- lifecycle (the only fields a later work order may mutate)
            status                    TEXT    NOT NULL,     -- 'open' | 'closed' | 'passed'
            telegram_message_id       TEXT,
            created_at                TEXT    DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── the live quote ───────────────────────────────────────────────────────────────

def _live_quote(symbol: str) -> tuple[float | None, str | None, str | None]:
    """(price, market_time_iso, error). Price is None unless it is a real live quote.

    Every failure mode returns (None, None, reason). There is no path through this
    function that invents, caches, defaults or substitutes a price — that property is
    what the whole ledger rests on, and it is asserted by test rather than trusted.
    """
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?interval=1d&range=1d",
            headers=_YF_HEADERS,
            timeout=15,
        )
    except Exception as exc:                     # network down, DNS, timeout
        return None, None, f"quote request failed: {type(exc).__name__}"

    if not r.ok:
        return None, None, f"quote endpoint returned HTTP {r.status_code}"

    try:
        result = r.json()["chart"]["result"][0]
        meta = result["meta"]
    except Exception:
        return None, None, "quote response was not in the expected shape"

    price = meta.get("regularMarketPrice")
    mtime = meta.get("regularMarketTime")

    if price is None:
        return None, None, "quote response carried no regularMarketPrice"
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None, None, "regularMarketPrice was not a number"
    if not (price > 0):
        # A zero or negative print is a broken feed, not a cheap stock.
        return None, None, f"implausible quote price ({price})"

    if mtime is None:
        # Without the trade time there is no way to tell a live print from a stale
        # one, and an undateable entry price is exactly the kind of number this
        # table must not accumulate.
        return None, None, "quote response carried no regularMarketTime"
    try:
        mdt = datetime.fromtimestamp(int(mtime), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None, None, "regularMarketTime was not a valid timestamp"

    age = (datetime.now(timezone.utc) - mdt).total_seconds()
    if age > MAX_QUOTE_AGE_SECONDS:
        return None, None, (f"quote is stale — last trade {mdt.isoformat()} "
                            f"({age / 86400:.1f} days old)")
    if age < -3600:
        # A trade time in the future is a clock or feed fault, not a quote.
        return None, None, f"quote timestamp is in the future ({mdt.isoformat()})"

    return price, mdt.isoformat(), None


# ── auth ─────────────────────────────────────────────────────────────────────────

def _secret_ok(request: Request) -> bool:
    """Telegram's own webhook authentication, compared in constant time.

    Telegram echoes the `secret_token` given at setWebhook time back in this header on
    every delivery. `hmac.compare_digest` rather than `!=` for the same reason the
    admin routes were changed to it: a plain comparison leaks, through timing, how many
    leading characters of a guess were right.

    Unset secret ⇒ False, not True. An endpoint that authenticates only when configured
    is an open write endpoint on any deploy where the env var was forgotten.
    """
    expected = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    supplied = request.headers.get("x-telegram-bot-api-secret-token", "")
    return bool(expected) and hmac.compare_digest(supplied, expected)


def _chat_allowed(chat_id) -> bool:
    """Only the configured operator's own chat may write to the ledger.

    Knowing the bot token is enough to send it messages; this is what keeps a decision
    row attributable to one human rather than to anyone who found the bot.
    """
    allowed = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return bool(allowed) and str(chat_id).strip() == allowed


def _log_denied(source: str, notes: str) -> None:
    """A rejected write must leave a trace. Best-effort — it may never block the 403."""
    try:
        from jpt_common import record_activity
        record_activity(source, notes=notes[:500])
    except Exception:
        pass


# ── telegram plumbing ────────────────────────────────────────────────────────────

def _answer_callback(callback_id: str, text: str) -> None:
    """Clear the button's spinner and show the human what happened. Best-effort: a
    failure here must never decide whether the row was written."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not callback_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text[:200],
                  "show_alert": True},
            timeout=10,
        )
    except Exception:
        pass


def build_alert_keyboard(alert_id: int, ticker: str) -> dict:
    """The two inline buttons. Callback data carries only ids — never a price, which
    would let the client propose the number this table exists to observe."""
    return {
        "inline_keyboard": [[
            {"text": "Buy",  "callback_data": f"pos:buy:{alert_id}:{ticker}"},
            {"text": "Pass", "callback_data": f"pos:pass:{alert_id}:{ticker}"},
        ]]
    }


def format_decision_prompt(alert: dict, worthiness: float | None = None) -> str:
    """The message body. States what fired and on what; makes no claim about what it
    is worth.

    ⚠️ NO PROJECTED RETURN, NO "STRONG BUY", NO CONFIDENCE FRAMING. The underlying
    data does not support any of it, and this message is the surface where an
    unsupported number would be most readily believed. Where a worthiness score is
    attached at all it is labelled unvalidated in the same breath as it is shown.
    """
    ticker = (alert.get("ticker") or "").replace("$", "").split()[0]
    lines = [
        f"*{alert.get('severity', '')} — {alert.get('rule', '')}*",
        f"Ticker: `${ticker}`",
        "",
        str(alert.get("headline", "")),
        "",
        f"Alert #{alert.get('id')}",
    ]
    if worthiness is not None:
        lines.append(
            f"Worthiness {worthiness:.1f} — UNVALIDATED, no demonstrated relationship "
            f"to returns. Recorded for future testing only."
        )
    lines.append("")
    lines.append("Record a decision. Buy fetches a live entry price at the moment you tap.")
    return "\n".join(lines)


# ── the handler ──────────────────────────────────────────────────────────────────

@router.post("/telegram/callback", include_in_schema=False)
async def telegram_callback(request: Request):
    if not _secret_ok(request):
        _log_denied("POSITION_LEDGER_AUTH_DENIED", "bad or missing telegram secret token")
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    try:
        update = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "malformed body"})

    cq = (update or {}).get("callback_query")
    if not cq:
        # Not a button tap (an ordinary message, an edit, …). Nothing to record, and
        # 200 so Telegram does not retry a delivery that will never be actionable.
        return {"ok": True, "ignored": "not a callback_query"}

    chat_id = (((cq.get("message") or {}).get("chat")) or {}).get("id")
    if not _chat_allowed(chat_id):
        _log_denied("POSITION_LEDGER_CHAT_DENIED", f"callback from chat_id={chat_id!r}")
        return JSONResponse(status_code=403, content={"error": "forbidden"})

    callback_id = str(cq.get("id") or "")
    if not callback_id:
        return JSONResponse(status_code=400, content={"error": "callback_query has no id"})

    parts = str(cq.get("data") or "").split(":")
    if len(parts) != 4 or parts[0] != "pos" or parts[1] not in ("buy", "pass"):
        return JSONResponse(status_code=400, content={"error": "unrecognised callback data"})
    decision = parts[1]
    try:
        alert_id = int(parts[2])
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "bad alert id"})
    ticker = normalize_ticker(parts[3]) or parts[3]

    message_id = str(((cq.get("message") or {}).get("message_id")) or "")

    return _record(decision=decision, callback_id=callback_id, chat_id=chat_id,
                   ticker=ticker, alert_id=alert_id, message_id=message_id)


def _record(*, decision: str, callback_id: str, chat_id, ticker: str,
            alert_id: int, message_id: str) -> dict:
    """Write exactly one row, or none. Separated from the HTTP layer so the tests can
    drive it directly without forging a whole Telegram update."""
    conn = db_connection()
    try:
        ensure_table(conn)

        # ── idempotency, enforced by the DATABASE rather than by a read-then-write ──
        # A double tap and a Telegram webhook retry both arrive as the same
        # `callback_query.id`. Checking "does a row exist?" first and inserting second
        # is a race two concurrent deliveries can both win; a UNIQUE column with
        # INSERT OR IGNORE cannot be raced, because the constraint is what decides.
        existing = conn.execute(
            "SELECT id, decision, entry_price FROM positions WHERE callback_query_id = ?",
            (callback_id,),
        ).fetchone()
        if existing:
            _answer_callback(callback_id, "Already recorded.")
            return {"ok": True, "duplicate": True, "position_id": existing["id"]}

        decided_at = _utcnow_iso()

        price = mtime = qerr = None
        if decision == "buy":
            fetched_at = _utcnow_iso()
            price, mtime, qerr = _live_quote(ticker)
            if price is None:
                # ⚠️ THE ONE BRANCH THAT MUST NEVER LEARN TO GUESS. No row, no partial
                # row, no placeholder to be filled in later — and the human is told
                # explicitly, because a silent no-op reads exactly like a success.
                _log_denied("POSITION_LEDGER_QUOTE_FAILED",
                            f"{ticker} alert={alert_id}: {qerr}")
                _answer_callback(
                    callback_id,
                    f"NOT recorded — no live price for {ticker}. {qerr}. "
                    f"Nothing was saved; tap again when quotes resolve.")
                return {"ok": False, "recorded": False, "reason": qerr}
        else:
            # A decline is worth as much as a buy for future validation, so its row is
            # never blocked by a quote failure: the DECISION is the datum here, and the
            # price is context. The asymmetry with 'buy' above is deliberate — a buy
            # without a real price is a poisoned row, a pass without one is a complete
            # record of a decision that simply has less context attached.
            fetched_at = _utcnow_iso()
            price, mtime, qerr = _live_quote(ticker)

        # `worthiness_scores` does not exist in production (verified: 57 tables, not
        # among them). NULL is recorded rather than an inline recomputation — a score
        # computed here would be a different number from one computed by a scheduled
        # job, and this column is supposed to capture what the system believed at the
        # moment of the tap, not what this request could derive.
        worthiness = _worthiness_for(conn, ticker)

        cur = conn.execute(
            """INSERT OR IGNORE INTO positions
               (decision, callback_query_id, chat_id, ticker, entry_price, entry_at,
                quote_source, quote_fetched_at, quote_market_time, quote_error,
                triggering_alert_id, worthiness_score_at_entry, worthiness_score_version,
                status, telegram_message_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (decision, callback_id, str(chat_id), ticker, price, decided_at,
             QUOTE_SOURCE if price is not None else None,
             fetched_at if price is not None else None,
             mtime, qerr,
             alert_id, worthiness, WORTHINESS_VERSION,
             "open" if decision == "buy" else "passed", message_id),
        )
        conn.commit()

        if cur.rowcount == 0:
            # Lost the race to a concurrent identical delivery. The other one wrote it.
            row = conn.execute(
                "SELECT id FROM positions WHERE callback_query_id = ?", (callback_id,)
            ).fetchone()
            _answer_callback(callback_id, "Already recorded.")
            return {"ok": True, "duplicate": True,
                    "position_id": row["id"] if row else None}

        pos_id = cur.lastrowid
        if decision == "buy":
            _answer_callback(callback_id, f"Recorded: {ticker} at {price:.2f}.")
        else:
            _answer_callback(callback_id, f"Recorded: passed on {ticker}.")
        return {"ok": True, "recorded": True, "position_id": pos_id,
                "decision": decision, "entry_price": price}
    finally:
        conn.close()


def _worthiness_for(conn, ticker: str) -> float | None:
    """Read the persisted worthiness score, or None.

    ⚠️ READ ONLY, AND NEVER RECOMPUTED. If the table is absent — which it is, in
    production today — this returns None and the row honestly records that no score
    existed at the moment of the decision. Computing one here instead would fabricate
    a value that no scheduled job ever produced and that no later run could reproduce.
    """
    try:
        row = conn.execute(
            "SELECT score FROM worthiness_scores WHERE ticker = ? "
            "ORDER BY computed_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        return row["score"] if row else None
    except Exception:
        return None            # table does not exist yet — the expected case today
