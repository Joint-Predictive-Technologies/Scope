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
import json
import time
from datetime import datetime, timezone

import requests

from jpt_common import db_connection


RULE = "RULE_07"

# Dedup: one evolving alert per market thesis (market_id + direction + threshold
# bucket). A new alert is only emitted when the direction flips, the move crosses
# into a higher bucket, volume changes materially, or a cooldown has elapsed.
COOLDOWN_HOURS = 24
VOLUME_MATERIAL_PCT = 0.5  # 50%
GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
CLOB_PRICE_HISTORY = "https://clob.polymarket.com/prices-history"
HEADERS = {"User-Agent": "Scope/0.1 sloppysecondstbb@gmail.com"}
SLEEP = 0.3

# Trigger thresholds — require BOTH a meaningful price move AND meaningful volume
MIN_PP_MOVE = 20.0        # percentage points
MIN_VOLUME_24H = 500_000  # USD

# Alert severity thresholds
HIGH_PP = 20.0
HIGH_VOL = 500_000
CRITICAL_PP = 30.0
CRITICAL_VOL = 1_000_000

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
          AND datetime(created_at) >= datetime('now', '-3 days')
        LIMIT 1
        """,
        (RULE, headline),
    ).fetchone()
    return row is not None


def market_id_of(m: dict) -> str:
    """Stable identifier for a Polymarket market."""
    return str(m.get("conditionId") or m.get("id") or m.get("slug") or "")


def threshold_bucket(abs_pp: float) -> int:
    """Coarse move buckets so small refreshes don't spawn new alerts."""
    if abs_pp >= 50:
        return 2
    if abs_pp >= 30:
        return 1
    return 0


def _find_existing(conn, market_id: str):
    return conn.execute(
        """SELECT id, tags, created_at FROM alerts
           WHERE rule = ? AND tags LIKE ?
           ORDER BY datetime(created_at) DESC LIMIT 1""",
        (RULE, f'%"market_id": "{market_id}"%'),
    ).fetchone()


def dedup_decision(existing_row, direction_sign: str, bucket: int, volume: float) -> str:
    """Return 'new', 'update', or 'skip' for a triggering market snapshot."""
    if existing_row is None:
        return "new"
    try:
        prev = json.loads(existing_row["tags"] or "{}")
    except Exception:
        prev = {}
    prev_dir = prev.get("direction_sign")
    prev_bucket = prev.get("bucket", -1)
    prev_vol = float(prev.get("volume") or 0)

    import datetime as _dt
    try:
        ts = _dt.datetime.fromisoformat((existing_row["created_at"] or "").replace(" ", "T"))
        age_h = (_dt.datetime.utcnow() - ts).total_seconds() / 3600
    except Exception:
        age_h = 1e9

    material = (
        prev_dir != direction_sign
        or (isinstance(prev_bucket, int) and bucket > prev_bucket)
        or (prev_vol > 0 and abs(volume - prev_vol) / prev_vol >= VOLUME_MATERIAL_PCT)
    )
    if material or age_h >= COOLDOWN_HOURS:
        return "new"
    return "update"


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

        if not (triggered_pp and triggered_vol):
            continue

        triggered += 1

        tickers = map_tickers(question)
        # Use primary ticker only — multi-ticker strings break sector mapping
        ticker_str = tickers[0] if tickers else None

        direction = f"+{abs_pp:.1f}pp" if pp_change >= 0 else f"-{abs_pp:.1f}pp"
        direction_sign = "up" if pp_change >= 0 else "down"
        bucket = threshold_bucket(abs_pp)
        mkt_id = market_id_of(m)
        slug = str(m.get("slug") or "")
        ticker_label = f" — related: {ticker_str}" if ticker_str else ""
        headline = (
            f"Polymarket: '{question[:60]}' moved {direction}{ticker_label}"
        )

        severity = (
            "CRITICAL"
            if abs_pp >= CRITICAL_PP and volume_24h >= CRITICAL_VOL
            else "HIGH"
        )

        # Structured tags — carry the stable market identity for dedup + a
        # specific verify link. All related tickers travel with the one alert.
        tags = json.dumps({
            "market_id":      mkt_id,
            "slug":           slug,
            "question":       question[:120],
            "direction":      direction,
            "direction_sign": direction_sign,
            "bucket":         bucket,
            "volume":         round(volume_24h),
            "tickers":        tickers[:4],
            "source":         "Polymarket",
        })

        print(
            f"  [{severity}] {direction}  vol=${volume_24h:,.0f}  "
            f"{question[:70]}"
            + (f"  → {ticker_str}" if ticker_str else "")
        )

        if emit_alerts and conn is not None:
            if not mkt_id:
                # No stable id — fall back to headline-based dedup.
                if not alert_exists(conn, headline):
                    insert_alert(conn, headline, ticker_str, severity, tags)
                    emitted += 1
                continue
            existing = _find_existing(conn, mkt_id)
            decision = dedup_decision(existing, direction_sign, bucket, volume_24h)
            if decision == "new":
                insert_alert(conn, headline, ticker_str, severity, tags)
                emitted += 1
            elif decision == "update":
                # Evolve the existing thesis in place — no new alert.
                conn.execute(
                    "UPDATE alerts SET headline=?, severity=?, tags=?, ticker=? WHERE id=?",
                    (headline, severity, tags, ticker_str, existing["id"]),
                )
                conn.commit()
            # decision == "skip": nothing to do

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
