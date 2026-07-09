#!/usr/bin/env python3
"""
rule_07_polymarket.py

Detects significant position movements on politically-linked Polymarket
prediction markets and emits RULE_07 alerts.

Discovery uses the Gamma REST API (active markets, sorted by 24h CLOB volume)
rather than the raw CLOB /markets cursor which serves mostly closed archives.
Price-history detail is fetched from the CLOB prices-history endpoint using
each market's first CLOB token ID and a 24-hour startTs/endTs window.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

import requests

from jpt_common import db_connection


RULE = "RULE_07"
GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
CLOB_PRICE_HISTORY = "https://clob.polymarket.com/prices-history"
HEADERS = {"User-Agent": "Scope/0.1 sloppysecondstbb@gmail.com"}
SLEEP = 0.3

# Trigger thresholds
MIN_PP_MOVE = 15.0        # percentage points
MIN_VOLUME_24H = 100_000  # USD

# Alert severity thresholds
HIGH_PP = 15.0
HIGH_VOL = 200_000

# ---------------------------------------------------------------------------
# Blocklist — skip sports, esports, and other noise questions.
# Exception: "fifa world cup" is kept when the question also contains a
# country name that maps to a geopolitical equity ticker.
# ---------------------------------------------------------------------------
BLOCKLIST_TERMS = [
    "vs.",
    "score first",
    "first corner",
    "first blood",
    "o/u",
    "nba",
    "nfl",
    "mlb",
    "nhl",
    "soccer",
    "esports",
    "counter-strike",
    "valorant",
    "ufc",
    "george hirst",
    "fifa world cup",
]

# Countries whose presence in a FIFA World Cup question signals geopolitical
# relevance (maps to a TICKER_MAP entry with non-$SPY tickers).
_GEOPOLITICAL_COUNTRIES = [
    "china", "iran", "russia", "ukraine", "israel", "taiwan",
    "north korea", "venezuela", "cuba", "syria", "saudi",
]


def is_blocked(question: str) -> bool:
    q = question.lower()
    for term in BLOCKLIST_TERMS:
        if term not in q:
            continue
        # Special case: keep FIFA World Cup questions that name a
        # geopolitically significant country.
        if term == "fifa world cup":
            if any(country in q for country in _GEOPOLITICAL_COUNTRIES):
                continue
        return True
    return False


# ---------------------------------------------------------------------------
# Political keyword filter — market question must contain at least one
# ---------------------------------------------------------------------------
POLITICAL_KEYWORDS = [
    "fed ", "federal reserve", "fomc", "rate cut", "rate hike",
    "interest rate", "basis point", " bps",
    "president", "election", "congress", "senate", " vote", "ballot",
    "tariff", "trade war", "trade deal",
    "china", "iran", "russia", "ukraine", "nato",
    "sanction", " war ", "peace deal", "ceasefire", "treaty",
    "inflation", " cpi", " gdp", "recession", "fiscal", "budget",
    "debt ceiling", "deficit",
    "regulation", "deregulation", "legislation", "stimulus",
    "crypto", "bitcoin", "btc", "ethereum", "digital asset",
    "oil price", "opec", "energy policy", "climate",
    "immigration", "border",
    "nuclear", "defense spending", "military",
    " tax ", "irs", "spending bill",
    "healthcare", "medicare", "medicaid", "drug price",
    "trump", "biden", "harris", "democrat", "republican",
    "powell", "yellen", "treasury",
]

# ---------------------------------------------------------------------------
# Keyword → equity ticker mapping (longest match wins where relevant)
# ---------------------------------------------------------------------------
TICKER_MAP: list[tuple[list[str], list[str]]] = [
    (["fed ", "federal reserve", "fomc", "rate cut", "rate hike",
      "interest rate", "bps", "powell"],         ["$TLT", "$SPY"]),
    (["china", "chinese", "tariff", "trade war"], ["$FXI", "$SPY"]),
    (["crypto", "bitcoin", "btc", "ethereum"],    ["$COIN", "$MSTR", "$IBIT"]),
    (["iran", "opec", "oil price", "crude",
      "energy sanction"],                         ["$USO", "$XLE"]),
    (["ukraine", "russia", "nato", "defense",
      "military", "war", "ceasefire"],            ["$LMT", "$RTX", "$NOC"]),
    (["healthcare", "pharma", "drug price",
      "medicare", "medicaid"],                    ["$XLV"]),
    (["inflation", "cpi", "pce"],                 ["$TIP", "$TLT"]),
    (["dollar", "usd", "fiscal", "debt ceiling"], ["$DXY", "$UUP"]),
    (["election", "president", "congress",
      "senate", "trump", "biden", "harris"],      ["$SPY"]),
    (["nvidia", "semiconductor", "chip",
      "ai export"],                               ["$NVDA", "$SOXX"]),
    (["europe", "eu", "ecb"],                     ["$FEZ"]),
    (["gold", "precious metal"],                  ["$GLD", "$GDX"]),
    (["japan", "boj", "yen"],                     ["$EWJ"]),
]


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None) -> requests.Response | None:
    time.sleep(SLEEP)
    try:
        return requests.get(url, headers=HEADERS, params=params, timeout=20)
    except requests.RequestException:
        return None


# ---------------------------------------------------------------------------
# Market discovery — Gamma API, paginated by volume
# ---------------------------------------------------------------------------

def fetch_active_markets() -> list[dict]:
    markets: list[dict] = []
    offset = 0
    limit = 100

    while True:
        r = _get(GAMMA_MARKETS, params={
            "active": "true",
            "closed": "false",
            "limit": limit,
            "offset": offset,
            "order": "volume24hrClob",
            "ascending": "false",
        })
        if r is None or r.status_code != 200:
            break
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        markets.extend(batch)
        if len(batch) < limit:
            break
        offset += len(batch)

    return markets


# ---------------------------------------------------------------------------
# Political relevance filter
# ---------------------------------------------------------------------------

def is_political(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in POLITICAL_KEYWORDS)


# ---------------------------------------------------------------------------
# Equity ticker mapping
# ---------------------------------------------------------------------------

def map_tickers(question: str) -> list[str]:
    q = question.lower()
    tickers: list[str] = []
    seen: set[str] = set()
    for keywords, syms in TICKER_MAP:
        if any(kw in q for kw in keywords):
            for s in syms:
                if s not in seen:
                    tickers.append(s)
                    seen.add(s)
    return tickers


# ---------------------------------------------------------------------------
# 24-hour price change via CLOB prices-history
# ---------------------------------------------------------------------------

def fetch_24h_price_change(token_id: str) -> float | None:
    """
    Return the price change in percentage points over the last 24 hours
    for the given CLOB token ID. Returns None on failure or no data.
    """
    now_ts = int(datetime.now(timezone.utc).timestamp())
    yesterday_ts = now_ts - 86_400

    r = _get(CLOB_PRICE_HISTORY, params={
        "market": token_id,
        "startTs": yesterday_ts,
        "endTs": now_ts,
        "fidelity": 60,
    })
    if r is None or r.status_code != 200:
        return None

    history = r.json().get("history", [])
    if len(history) < 2:
        return None

    p_start = history[0]["p"]
    p_end = history[-1]["p"]
    return (p_end - p_start) * 100.0


# ---------------------------------------------------------------------------
# Alert dedup
# ---------------------------------------------------------------------------

def alert_exists(conn, headline: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE rule = ?
          AND headline = ?
          AND datetime(created_at) >= datetime('now', '-1 days')
        LIMIT 1
        """,
        (RULE, headline),
    ).fetchone()
    return row is not None


def insert_alert(
    conn,
    headline: str,
    ticker: str | None,
    severity: str,
    tags: str,
) -> None:
    conn.execute(
        "INSERT INTO alerts (rule, headline, severity, tags, ticker) VALUES (?,?,?,?,?)",
        (RULE, headline, severity, tags, ticker),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(emit_alerts: bool) -> tuple[int, int]:
    markets = fetch_active_markets()

    triggered = 0
    emitted = 0
    conn = db_connection() if emit_alerts else None

    for m in markets:
        question = m.get("question") or ""
        if not is_political(question):
            continue
        if is_blocked(question):
            continue

        volume_24h = float(m.get("volume24hr") or 0)

        # Price change: prefer the pre-computed Gamma field, fall back to CLOB.
        gamma_change = m.get("oneDayPriceChange")
        if gamma_change is not None:
            pp_change = float(gamma_change) * 100.0
        else:
            token_ids_raw = m.get("clobTokenIds") or "[]"
            try:
                import json
                token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
            except Exception:
                token_ids = []
            if not token_ids:
                continue
            pp_change = fetch_24h_price_change(str(token_ids[0]))
            if pp_change is None:
                # Can't determine price movement; still trigger on volume alone.
                pp_change = 0.0

        abs_pp = abs(pp_change)
        triggered_pp = abs_pp >= MIN_PP_MOVE
        triggered_vol = volume_24h >= MIN_VOLUME_24H

        if not (triggered_pp or triggered_vol):
            continue

        triggered += 1

        tickers = map_tickers(question)
        ticker_str = " ".join(tickers) if tickers else None

        direction = f"+{abs_pp:.1f}pp" if pp_change >= 0 else f"-{abs_pp:.1f}pp"
        ticker_label = f" — related: {ticker_str}" if ticker_str else ""
        headline = (
            f"Polymarket: '{question[:60]}' moved {direction}{ticker_label}"
        )

        severity = (
            "HIGH"
            if abs_pp >= HIGH_PP or volume_24h >= HIGH_VOL
            else "MEDIUM"
        )

        tags = f"{question[:100]},{direction},{volume_24h:.0f}"

        print(
            f"  [{severity}] {direction}  vol=${volume_24h:,.0f}  "
            f"{question[:70]}"
            + (f"  → {ticker_str}" if ticker_str else "")
        )

        if emit_alerts and conn is not None:
            if not alert_exists(conn, headline):
                insert_alert(conn, headline, ticker_str, severity, tags)
                emitted += 1

    if conn is not None:
        conn.close()

    return triggered, emitted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect Polymarket political market movements (RULE_07)."
    )
    parser.add_argument(
        "--emit-alerts",
        action="store_true",
        help="Insert triggered markets into the alerts table.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    print("Fetching active Polymarket markets …")
    triggered, emitted = run(args.emit_alerts)
    print(f"{triggered} markets triggered, {emitted} alerts emitted")


if __name__ == "__main__":
    main()
