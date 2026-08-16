from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from api.rate_limit import rate_limit
from jpt_common import db_connection
from api.receipts import build_receipts

router = APIRouter()

_YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _fetch_market_cap(conn, symbol: str) -> int | None:
    """The ticker page's market cap. REUSES the collector's resolver — never a copy.

    ⚠️ WHY EVERY TICKER PAGE READ "Market cap unavailable". This called Yahoo's
    `quoteSummary` endpoint, which now returns **HTTP 401 for every symbol** — verified
    live against AAPL, NVDA and LMT. The `except Exception: return None` then turned a
    dead endpoint into a silent `None`, and the page rendered "unavailable" as though the
    company simply could not be priced. It was not a `ticker_meta` coverage gap; the
    source had been dead and nothing said so.

    `scripts.rule_reddit_collector.market_cap` is the working resolver — SEC shares x a
    Yahoo chart close — and it is the one carrying the plausibility guards (magnitude
    floor and ceiling, foreign-private-issuer units, share-count staleness). Importing it
    means the page cannot drift from the guarded arithmetic, and a mis-scale can never
    reach a human as a confident number: it resolves to `unknown` instead.
    """
    try:
        from scripts.rule_reddit_collector import market_cap as _resolve
        return _resolve(conn, symbol, cache=False)
    except Exception:
        return None


def _with_cap_flags(d: dict) -> dict:
    """The ONE place the cap flags are attached, so the cache-hit and cache-miss paths
    cannot disagree — they already did, and the page silently lost its honest wording."""
    d["cap_resolved"] = True
    d["cap_status"] = "known" if d.get("market_cap") else "unknown"
    return d


def _fetch_social_spike(symbol: str) -> bool:
    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
        r = requests.get(url, timeout=6)
        messages = r.json().get("messages", [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent = [
            m for m in messages
            if datetime.fromisoformat(
                m["created_at"].replace("Z", "+00:00")
            ) >= cutoff
        ]
        return len(recent) >= 5
    except Exception:
        return False


def _stale(ts_str: str | None, hours: float = 24) -> bool:
    if not ts_str:
        return True
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt) >= timedelta(hours=hours)
    except Exception:
        return True


# ── watchlist ─────────────────────────────────────────────────────────────────

@router.get("/watchlist")
def get_watchlist():
    conn = db_connection()
    rows = conn.execute(
        "SELECT symbol, added_at FROM watchlist ORDER BY added_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


_TICKER_SHAPE = re.compile(r"^[A-Z0-9.\-]{1,10}$")


@router.post("/watchlist/{symbol}", dependencies=[Depends(rate_limit(20, 60))])
def add_watchlist(symbol: str):
    # `symbol` reaches `watchlist.html`'s render() unescaped, including inside a
    # single-quoted onclick="removeTicker('...')" JS string — the client-side
    # `.replace(/[^A-Z]/g, '')` in watchlist.html is a UI convenience only, not a
    # security boundary, since this endpoint is reachable directly. Without a
    # server-side shape check, POSTing an id like `x');alert(1)//` stores verbatim
    # (the SQL insert below is already parameterized and safe) and then executes
    # for anyone who next loads /watchlist. Real ticker shapes (incl. class
    # shares like BRK.B or BF-B) fit this; anything else is rejected outright.
    clean = symbol.upper().strip()
    if not _TICKER_SHAPE.match(clean):
        return JSONResponse(status_code=422, content={"error": "invalid ticker symbol"})
    conn = db_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)",
            (clean,),
        )
        conn.commit()
    except Exception as exc:
        conn.close()
        return JSONResponse(status_code=400, content={"error": str(exc)})
    conn.close()
    return {"status": "added", "symbol": clean}


@router.delete("/watchlist/{symbol}")
def remove_watchlist(symbol: str):
    conn = db_connection()
    conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
    conn.commit()
    conn.close()
    return {"status": "removed", "symbol": symbol.upper()}


# ── leaderboard ───────────────────────────────────────────────────────────────

@router.get("/leaderboard")
def get_leaderboard(days: int = Query(default=30, ge=1, le=90)):
    conn = db_connection()
    rows = conn.execute(
        """
        SELECT
            REPLACE(ticker, '$', '') AS ticker,
            COUNT(*)                 AS total_alerts,
            COUNT(DISTINCT rule)     AS rule_count,
            GROUP_CONCAT(DISTINCT rule) AS rules,
            MAX(CASE severity WHEN 'CRITICAL' THEN 'CRITICAL'
                              WHEN 'HIGH'     THEN 'HIGH'
                              ELSE 'MEDIUM' END) AS top_severity
        FROM alerts
        WHERE ticker IS NOT NULL AND ticker != ''
          AND ticker NOT LIKE '% %'
          AND datetime(created_at) >= datetime('now', ?)
        GROUP BY ticker
        ORDER BY rule_count DESC, total_alerts DESC
        LIMIT 10
        """,
        (f"-{days} days",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── ticker list ───────────────────────────────────────────────────────────────

@router.get("")
def get_tickers(limit: int = Query(default=200, ge=1, le=1000)):
    conn = db_connection()
    rows = conn.execute(
        "SELECT symbol, company_name FROM tickers ORDER BY symbol LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── meta (market cap + social) ────────────────────────────────────────────────

@router.get("/{symbol}/meta")
def get_ticker_meta(symbol: str):
    symbol = symbol.upper()
    conn = db_connection()
    row = conn.execute(
        "SELECT * FROM ticker_meta WHERE symbol = ?", (symbol,)
    ).fetchone()
    now = datetime.now(timezone.utc).isoformat()

    if row and not _stale(row["cap_updated"]):
        conn.close()
        # ⚠️ THE CACHE HIT MUST CARRY THE SAME FLAGS AS THE MISS. Returning a bare
        # `dict(row)` here meant `cap_resolved` was absent on every request after the
        # first, so the page fell back to "Market cap unavailable" — the exact dead-end
        # string this change exists to remove. Since the endpoint stamps a fresh
        # `cap_updated` on every resolve, the honest wording was reachable at most once
        # per ticker per 24h, and in production — where the collector has already warmed
        # `ticker_meta` — very likely never.
        return _with_cap_flags(dict(row))

    # `cache=False` on the resolver: THIS endpoint owns the `ticker_meta` write below,
    # and letting both write produced two upserts per request.
    market_cap   = _fetch_market_cap(conn, symbol)
    social_spike = _fetch_social_spike(symbol)

    if row:
        conn.execute(
            """UPDATE ticker_meta
               SET market_cap=?, cap_updated=?, social_spike=?, social_at=?
               WHERE symbol=?""",
            (market_cap, now, int(social_spike), now, symbol),
        )
    else:
        conn.execute(
            """INSERT INTO ticker_meta (symbol, market_cap, cap_updated, social_spike, social_at)
               VALUES (?,?,?,?,?)""",
            (symbol, market_cap, now, int(social_spike), now),
        )
    conn.commit()

    result = conn.execute(
        "SELECT * FROM ticker_meta WHERE symbol = ?", (symbol,)
    ).fetchone()
    conn.close()
    out = dict(result) if result else {
        "symbol": symbol, "market_cap": market_cap,
        "social_spike": int(social_spike), "cap_updated": now,
    }
    # RESOLVED-AND-UNKNOWN is a different fact from NOT-YET-LOOKED-UP, and the page must
    # be able to say so. Without this the frontend cannot tell "we asked SEC and Yahoo and
    # genuinely cannot price this" from "this lookup never ran", and it rendered both as
    # the same dead-end string.
    return _with_cap_flags(out)


# ── position sizing (materiality context) ─────────────────────────────────────

def _field(value, status="known", reason=None, **extra) -> dict:
    """One panel field, ALWAYS carrying its own status.

    ⚠️ THE STATUS IS NOT OPTIONAL AND IT IS NOT DERIVABLE FROM THE VALUE. A missing number
    and a real zero are different facts, and every previous version of a Scope surface that
    let the client infer one from the other rendered them identically — the
    "Market cap unavailable" dead-end on this very page came from exactly that (see
    `_fetch_market_cap`). `status` is `known`, `unavailable`, or a field-specific state like
    `not_burning`; `reason` says WHY on everything that is not `known`, so the UI never has
    to invent an explanation for an empty cell.
    """
    return {"value": value, "status": status, "reason": reason, **extra}


# ⚠️ NOT DERIVABLE, AND THIS IS A FINDING RATHER THAN A TODO. Three independent
# disqualifications, spelled out because the next person to look at this will see
# `us-gaap:ClassOfWarrantOrRightOutstanding` in companyfacts and assume it is the answer:
#   1. Scope ingests no SEC filing text. `filings` is the CONGRESSIONAL PTR table.
#   2. ATM capacity lives in S-3/424B5 PROSE. There is no XBRL concept for shelf remaining.
#   3. The warrant concept is normally DIMENSIONED per class, and the undimensioned fact
#      companyfacts returns may be one class or the total with nothing to tell them apart.
#      Measured on ONDS (CIK 0001646188) it reads 267,857 as of 2025-12-31, down from
#      3,616,071 — a number that would render as a near-zero overhang and cannot be shown
#      to be complete.
# A partial overhang printed as THE overhang is a confident wrong number, which is strictly
# worse than an empty state on a panel a human sizes a position from.
_DILUTION_UNAVAILABLE = (
    "Scope ingests no SEC filing text, ATM shelf capacity is disclosed only in S-3/424B5 "
    "prose, and the XBRL warrant concept is dimensioned per class so an undimensioned "
    "total cannot be shown to be complete. Not estimated."
)


def _dollar_events(conn, symbol: str, market_cap, ttm_revenue) -> list[dict]:
    """Every dollar-denominated alert on this ticker, re-expressed against the company.

    Two structured sources, and only structured ones:

      RULE_11  `contracts.amount` reached through `alerts.award_key`.
      RULE_16  `tags.value_usd` on the 13F whale disclosure.

    ⚠️ RULE_09 IS DELIBERATELY ABSENT. Lobbying spend is dollar-denominated and
    `lobbying_filings` even carries a ticker on 286 of 640 rows — but the RULE_09 ALERTS
    have `ticker IS NULL` for every row in prod, and their amounts live in a positional
    comma tag string (`"...,Defense,$30K→$80K"`), pre-rounded to the nearest thousand.
    Joining `lobbying_filings.ticker` instead would put events on this panel that the
    page's own alert list does not contain, sourced from a different key than everything
    around them. Left out rather than half-wired.

    ⚠️ EXACT TICKER MATCH, NOT THE PAGE'S `LIKE '%sym%'`. The alert list above matches
    loosely because an alert's ticker field can hold a multi-symbol basket; that predicate
    also matches BABA for BA. A loose match is survivable in a list a human reads and is
    not survivable in an arithmetic claim about THIS company's market cap.

    ⚠️ `MAX(amount)` PER AWARD, matching `jpt_common.contract_leg_weight`'s
    `ORDER BY amount DESC LIMIT 1`. One `award_id` can hold several rows, and the panel
    and the gate must not pick different ones for the same award.
    """
    events = []

    for r in conn.execute(
        """
        SELECT a.id, a.rule, a.headline, a.event_date, a.created_at, a.award_key,
               MAX(c.amount) AS amount_usd, c.recipient_name
        FROM alerts a
        JOIN contracts c ON c.award_id = a.award_key
        WHERE a.rule = 'RULE_11' AND a.award_key IS NOT NULL AND a.ticker = ?
        GROUP BY a.id
        ORDER BY amount_usd DESC
        """,
        (symbol,),
    ).fetchall():
        events.append({
            "alert_id": r["id"], "rule": r["rule"], "headline": r["headline"],
            "event_date": r["event_date"] or r["created_at"],
            "amount_usd": r["amount_usd"],
            "amount_source": "contracts.amount (total obligated to date)",
            "counterparty": r["recipient_name"],
        })

    for r in conn.execute(
        "SELECT id, rule, headline, event_date, created_at, tags FROM alerts "
        "WHERE rule = 'RULE_16' AND ticker = ?",
        (symbol,),
    ).fetchall():
        # Parsed in Python, not with `json_extract`: RULE_16's tags are JSON today, but
        # `tags` is a free TEXT column that several other rules fill with a positional
        # comma string, and a malformed value must skip one event rather than 500 the
        # whole panel.
        try:
            value_usd = (json.loads(r["tags"]) or {}).get("value_usd")
        except (TypeError, ValueError):
            continue
        if not isinstance(value_usd, (int, float)) or value_usd <= 0:
            continue
        events.append({
            "alert_id": r["id"], "rule": r["rule"], "headline": r["headline"],
            "event_date": r["event_date"] or r["created_at"],
            "amount_usd": float(value_usd),
            "amount_source": "13F reported position value",
            "counterparty": None,
        })

    for e in events:
        amount = e["amount_usd"]
        e["pct_of_market_cap"] = (
            round(amount / market_cap * 100, 4) if market_cap and amount else None)
        e["pct_of_ttm_revenue"] = (
            round(amount / ttm_revenue * 100, 4) if ttm_revenue and amount else None)
    events.sort(key=lambda e: e["amount_usd"], reverse=True)
    return events


@router.get("/{symbol}/position-sizing")
def get_position_sizing(symbol: str):
    """Materiality context for the ticker page. DISPLAY ONLY — never reaches the score.

    Reads `position_sizing_cache`, which no rule, no instrument and no part of the
    corroboration gate touches. It deliberately does NOT read `ticker_meta`, whose
    freshness `contract_leg_weight` depends on: a page view must not decide whether a
    contracts leg gets weighted.
    """
    symbol = symbol.upper()
    conn = db_connection()
    try:
        return _position_sizing_payload(conn, symbol)
    except Exception as exc:
        # An honest failure state, not an empty panel that reads as "this company has none
        # of these things". `available: False` is a THIRD state, distinct from a field-level
        # `unavailable`: the lookup itself did not complete, which says nothing about the
        # company.
        return {"symbol": symbol, "available": False,
                "reason": f"Position-sizing lookup failed: {type(exc).__name__}",
                "events": []}
    finally:
        # `finally`, not a close on each path. The events query runs after the resolve, and
        # on the previous shape an exception there returned through the endpoint without
        # ever closing the handle — a leaked SQLite connection per failing page view.
        conn.close()


def _position_sizing_payload(conn, symbol: str) -> dict:
    from scripts.position_sizing import (ADV_WINDOW_DAYS, PARTICIPATION_RATE,
                                         cash_runway_months, fill_profile,
                                         max_fillable_usd, monthly_burn_rate, resolve)
    row = resolve(conn, symbol) or {}

    cap = row.get("market_cap")
    revenue = row.get("ttm_revenue")
    ocf, ocf_start, ocf_end = (row.get("operating_cash_flow"),
                               row.get("ocf_period_start"), row.get("ocf_period_end"))

    # The cap gates the two numbers it is the product of — publishing SEC ordinary shares
    # beside a US ADR price is the TSM mis-scale the resolver's guards exist to prevent,
    # and a reader can perform that multiplication by hand. See `scripts/position_sizing`.
    cap_reason = ("SEC shares outstanding x last close could not be resolved within the "
                  "plausibility guards (foreign private issuer, stale or implausible "
                  "share count, or no price).")

    # ⚠️ THE GUARD IS ACTUALLY APPLIED HERE, not merely described. A previous version set
    # `adv = row.get("adv_shares")` and relied on the resolver never producing an
    # ADV-without-close row — true of every live path, but the comment below claimed the
    # coupling was enforced at this layer too, and it was not. A hand-written or
    # partially-migrated row would have rendered "Avg daily volume: 500,000 sh" directly
    # beside "Last close: Unavailable", which is the exact pairing the coupling exists to
    # prevent. Defence in depth costs one conditional.
    adv = row.get("adv_shares") if row.get("last_close") else None
    adv_usd = (adv * row["last_close"]) if adv else None
    adv_reason = (
        f"Fewer than {ADV_WINDOW_DAYS} traded sessions available, or no usable last close. "
        "A thinly-traded name is exactly where this matters, so it is reported as unknown "
        "rather than averaged over whatever sessions happened to exist.")

    if ocf is None or not ocf_start:
        runway = _field(None, "unavailable",
                        "No operating cash flow reported in SEC XBRL data.")
    elif ocf >= 0:
        runway = _field(None, "not_burning",
                        "Operating cash flow was positive over the reported period — "
                        "there is no burn to divide cash by.",
                        period_start=ocf_start, period_end=ocf_end,
                        operating_cash_flow=ocf)
    elif row.get("cash_usd") is None:
        runway = _field(None, "unavailable",
                        "Operating burn is known but no cash balance is reported.")
    else:
        runway = _field(
            cash_runway_months(row.get("cash_usd"), ocf, ocf_start, ocf_end),
            "known", None,
            monthly_burn=monthly_burn_rate(ocf, ocf_start, ocf_end),
            period_start=ocf_start, period_end=ocf_end, operating_cash_flow=ocf)

    payload = {
        "symbol": symbol,
        "available": True,
        "fetched_at": row.get("fetched_at"),
        # ⚠️ `resolved_at`, NOT `as_of`, ON THESE TWO — the distinction is the honesty.
        # `as_of` elsewhere is the date of the FILED FACT (a share count as of 2026-07-21, a
        # public float as of the prior 30 June). A market cap has no such date: it is a
        # freshly computed product, and its price leg is "the last close Yahoo served us".
        # Labelling the resolve timestamp `as_of` implied a fact date and quietly claimed
        # more precision than exists — on a panel whose entire premise is that every figure
        # is dated. Truthiness rather than `is not None` here IS correct: the resolver's own
        # floors (`MIN_PLAUSIBLE_CAP`, `MIN_PLAUSIBLE_SHARES`) make zero unreachable, and if
        # one ever appeared, "unavailable" is the right rendering for it anyway.
        "market_cap": _field(cap, "known" if cap else "unavailable",
                             None if cap else cap_reason,
                             resolved_at=row.get("cap_updated")),
        "shares_outstanding": _field(
            row.get("shares_outstanding"),
            "known" if row.get("shares_outstanding") else "unavailable",
            None if row.get("shares_outstanding") else cap_reason,
            as_of=row.get("shares_as_of")),
        "last_close": _field(row.get("last_close"),
                             "known" if row.get("last_close") else "unavailable",
                             None if row.get("last_close") else cap_reason,
                             resolved_at=row.get("cap_updated")),
        # ⚠️ `is not None`, NEVER TRUTHINESS. A filer that reported ZERO revenue — a
        # pre-revenue biotech, a SPAC — is a company that ANSWERED, and `if revenue else`
        # relabelled that answer as "No annual or interim revenue facts in SEC XBRL data",
        # which is a factually false sentence shown to a user. `EntityPublicFloat = 0` is
        # common for the same filers. The cell erred safe (it read "Unavailable" rather
        # than inventing a number) but the REASON was a lie, and the reason is the whole
        # point of the honest empty state.
        "public_float_usd": _field(
            row.get("public_float_usd"),
            "known" if row.get("public_float_usd") is not None else "unavailable",
            None if row.get("public_float_usd") is not None else
            "No dei:EntityPublicFloat reported by this filer.",
            as_of=row.get("public_float_as_of")),
        "ttm_revenue": _field(
            revenue, "known" if revenue is not None else "unavailable",
            None if revenue is not None else
            "No annual or interim revenue facts in SEC XBRL data for this filer.",
            as_of=row.get("ttm_revenue_as_of"), basis=row.get("ttm_revenue_basis")),
        "cash": _field(row.get("cash_usd"),
                       "known" if row.get("cash_usd") is not None else "unavailable",
                       None if row.get("cash_usd") is not None else
                       "No cash and equivalents balance reported.",
                       as_of=row.get("cash_as_of")),
        "cash_runway_months": runway,
        # ── liquidity ────────────────────────────────────────────────────────
        # A signal can be perfectly real and still not tradeable at size. GCTS carries a
        # $181M market cap on ~$4.8M of daily dollar volume, so a $1M position is ~21% of
        # a day's entire tape — the gap between a theoretical edge and an executable one.
        #
        # ⚠️ ADV IS PUBLISHED ONLY WHERE `last_close` IS. Dollar volume is ADV x that same
        # close, so surfacing volume after the close has been withheld would invite the
        # reader to supply a price from elsewhere — which for a foreign private issuer
        # reproduces the ADR mis-scale the cap guards exist to prevent, arriving through the
        # liquidity panel instead of the cap one. Enforced in the resolver, restated here.
        "adv_shares": _field(
            adv, "known" if adv else "unavailable",
            None if adv else adv_reason,
            window_days=row.get("adv_window_days"),
            period_start=row.get("adv_period_start"),
            period_end=row.get("adv_period_end")),
        "adv_usd": _field(adv_usd, "known" if adv_usd else "unavailable",
                          None if adv_usd else adv_reason,
                          window_days=row.get("adv_window_days")),
        # ⚠️ THE CAVEAT TRAVELS WITH THE NUMBER, NOT ONLY IN THE PAGE COPY. `participation_
        # rate: 0.1` is a machine-readable PARAMETER, not a disclaimer — a consumer reading
        # this endpoint directly (the brief, a future export, anything that is not
        # `ticker.html`) would otherwise receive a confident dollar figure with nothing
        # saying it rests on a rule of thumb. The rendered panel says so in words; so does
        # this.
        "max_fillable_usd": _field(
            max_fillable_usd(adv_usd), "known" if adv_usd else "unavailable",
            None if adv_usd else adv_reason,
            participation_rate=PARTICIPATION_RATE, window_days=ADV_WINDOW_DAYS,
            basis=(f"{int(PARTICIPATION_RATE * 100)}% of {ADV_WINDOW_DAYS}-session average "
                   "daily dollar volume. A common execution convention, NOT a measured "
                   "impact limit for this stock — real capacity depends on spread, borrow "
                   "and news, none of which Scope models.")),
        "fill_profile": fill_profile(adv_usd),
        "dilution_overhang": _field(None, "unavailable", _DILUTION_UNAVAILABLE),
        "events": _dollar_events(conn, symbol, cap, revenue),
    }
    return payload


# ── price action during disclosure window ─────────────────────────────────────

@router.get("/{symbol}/price-action")
def get_price_action(
    symbol: str,
    start: str = Query(description="Transaction date YYYY-MM-DD"),
    end:   str = Query(description="Filing date YYYY-MM-DD"),
):
    symbol = symbol.upper()
    conn = db_connection()

    cached = conn.execute(
        "SELECT * FROM price_action WHERE symbol=? AND start_date=? AND end_date=?",
        (symbol, start, end),
    ).fetchone()
    if cached:
        conn.close()
        return dict(cached)

    try:
        p1 = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
        p2 = int(datetime.strptime(end,   "%Y-%m-%d").timestamp()) + 86400

        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?interval=1d&period1={p1}&period2={p2}"
        )
        r = requests.get(url, headers=_YF_HEADERS, timeout=8)
        data = r.json()

        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]

        if len(closes) < 2:
            conn.close()
            return {"symbol": symbol, "start_date": start, "end_date": end,
                    "pct_change": None, "start_price": None, "end_price": None}

        s_price = round(closes[0], 4)
        e_price = round(closes[-1], 4)
        pct     = round(((e_price - s_price) / s_price) * 100, 2)

        conn.execute(
            """INSERT OR REPLACE INTO price_action
               (symbol, start_date, end_date, start_price, end_price, pct_change)
               VALUES (?,?,?,?,?,?)""",
            (symbol, start, end, s_price, e_price, pct),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM price_action WHERE symbol=? AND start_date=? AND end_date=?",
            (symbol, start, end),
        ).fetchone()
        conn.close()
        return dict(row)

    except Exception as exc:
        conn.close()
        return {"symbol": symbol, "start_date": start, "end_date": end,
                "pct_change": None, "error": str(exc)[:120]}


# ── ticker detail — alerts + related members + transactions ───────────────────

@router.get("/{symbol}/alerts")
def get_ticker_alerts(
    symbol: str,
    days:  int = Query(default=365, ge=1, le=730),
    limit: int = Query(default=200, ge=1, le=500),
):
    sym = symbol.upper()
    conn = db_connection()

    alerts = conn.execute(
        """
        SELECT id, rule, severity, headline, detail, tags, ticker, member_id, created_at,
               lifecycle_stage, source_url, verify_url, theme_id,
               corroborates, corroboration_note, event_date, award_key
        FROM alerts
        WHERE ticker LIKE ?
          AND datetime(created_at) >= datetime('now', ?)
        ORDER BY datetime(created_at) DESC
        LIMIT ?
        """,
        (f"%{sym}%", f"-{days} days", limit),
    ).fetchall()

    # Same source-link resolution the feed uses — one implementation, not two.
    # This page previously carried a verbatim COPY of the feed's old URL-guessing
    # function, so fixing the feed alone left it fabricating.
    from api.routers.alerts import _document_urls
    _docs, _idx = _document_urls(alerts, conn)

    members = conn.execute(
        """
        SELECT DISTINCT m.bioguide_id, m.full_name, m.party, m.state
        FROM transactions t
        JOIN members m ON t.member_id = m.bioguide_id
        WHERE t.raw_ticker_string LIKE ?
        LIMIT 20
        """,
        (f"%{sym}%",),
    ).fetchall()

    transactions = conn.execute(
        """
        SELECT
            t.member_id,
            m.full_name,
            t.transaction_date,
            t.filing_date,
            t.transaction_type,
            t.amount_band,
            CAST(julianday(t.filing_date) - julianday(t.transaction_date) AS INTEGER) AS filing_delay
        FROM transactions t
        JOIN members m ON t.member_id = m.bioguide_id
        WHERE t.raw_ticker_string LIKE ?
        ORDER BY t.transaction_date DESC
        LIMIT 100
        """,
        (f"%{sym}%",),
    ).fetchall()

    # ⚠️ THE VERDICT IS COMPUTED SERVER-SIDE, ON PURPOSE. `ticker.html` already rebuilds
    # the gate's ticker/severity/window predicates in JavaScript to draw its
    # "N corroborating instruments of 3 needed" header — a fourth copy of the gate's
    # candidate logic. Since 2026-07-30 the gate also decides per ALERT (an insider leg
    # counts only on a genuine open-market buy), and re-expressing THAT in the browser
    # would be a fifth copy of a rule that has already diverged twice in this codebase.
    # So the page is handed the answer instead: `corroborates_gate` is the gate's own
    # `alert_corroborates`, and the client only has to filter on it.
    from scripts.rule_10_corroboration import alert_corroborates
    alert_dicts = []
    for r in alerts:
        d = dict(r)
        ok, why = alert_corroborates(r)
        d["corroborates_gate"] = ok
        d["corroborates_reason"] = why
        d["receipts"] = build_receipts(d, conn)
        d["document_url"] = _docs.get(d["id"])
        d["source_index_url"] = _idx.get(d["id"])
        alert_dicts.append(d)
    conn.close()
    return {
        "symbol": sym,
        "alerts": alert_dicts,
        "related_members": [dict(r) for r in members],
        "transactions": [dict(r) for r in transactions],
    }
