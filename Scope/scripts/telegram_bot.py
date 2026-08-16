#!/usr/bin/env python3
"""
Scope Telegram Bot — push high-priority alerts to a Telegram channel/chat.

Setup:
  1. Get a bot token from @BotFather on Telegram
  2. Get your chat/channel ID from @userinfobot
  3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
  4. (Optional) Set SCOPE_URL to the deployed URL

Run after each rule sweep, or add to the scheduler in main.py.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from jpt_common import db_connection

SCOPE_URL = os.getenv("SCOPE_URL", "https://scope-production-1c3a.up.railway.app")

EMOJI = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡"}

PUSH_RULES = {"RULE_10", "RULE_01B", "RULE_ANOMALY"}


def _should_push(alert: dict) -> bool:
    sev  = alert.get("severity", "")
    rule = alert.get("rule", "")
    if sev == "CRITICAL":
        return True
    if sev == "HIGH" and rule in PUSH_RULES:
        return True
    return False


def _format_message(alert: dict) -> str:
    emoji  = EMOJI.get(alert.get("severity", ""), "⚪")
    ticker = (alert.get("ticker") or "").replace("$", "").split()[0]
    detail = (alert.get("detail") or "")[:280].strip()

    lines = [
        f"{emoji} *{alert.get('severity')} — {alert.get('rule')}*",
    ]
    if ticker:
        lines.append(f"Ticker: `${ticker}`")
    lines.append(f"\n{alert.get('headline', '')}")
    if detail:
        lines.append(f"\n_{detail}_")

    links = [f"[View Feed]({SCOPE_URL}/feed)"]
    if ticker:
        links.append(f"[{ticker} Drill-Down]({SCOPE_URL}/ticker/{ticker})")
    lines.append("\n" + " · ".join(links))

    return "\n".join(lines)


def _send(bot_token: str, chat_id: str, text: str, reply_markup: dict | None = None) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as exc:
        print(f"[telegram] request error: {exc}")
        return False


def _decision_keyboard(alert: dict) -> dict | None:
    """Buy/Pass buttons, but only where a decision is actually recordable.

    ⚠️ NO TICKER ⇒ NO BUTTONS, and that is a correctness guard rather than a tidiness
    one. The ledger's entry price comes from a live quote keyed on the symbol, so a
    button on a tickerless alert (RULE_08's basket alerts, most OSINT rows) would offer
    the human an action that can only ever fail at the quote step. Better no button
    than a button that cannot work.

    Buttons are also withheld unless the inbound webhook is actually configured — an
    unanswerable button is worse than none, because a tap that silently does nothing
    reads to the human exactly like a tap that recorded something.

    ⚠️ AND NO BUTTONS ON A BASKET ALERT ("LMT RTX NOC"), WHICH IS THE LESS OBVIOUS
    CASE. The rest of this codebase resolves a basket by taking the first symbol
    (`firstTicker` in `alerts.html`), and that is right for a DISPLAY badge — it is
    wrong for a decision. A basket alert does not identify one security, so a single
    Buy button on it would silently commit the human to LMT for a signal that was
    about three companies, and the resulting row would be indistinguishable from a
    deliberate LMT decision forever after. The ambiguity is refused rather than
    resolved on the human's behalf; a basket can still be acted on manually.
    """
    if not os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip():
        return None
    symbols = (alert.get("ticker") or "").replace("$", "").split()
    if len(symbols) != 1:
        return None
    try:
        from api.routers.positions import build_alert_keyboard
        return build_alert_keyboard(int(alert["id"]), symbols[0])
    except Exception:
        return None


def run() -> None:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("[telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping")
        return

    conn = db_connection()

    # Find alerts not yet pushed (or marked as seen)
    rows = conn.execute("""
        SELECT a.id, a.rule, a.severity, a.headline, a.detail, a.ticker
        FROM alerts a
        WHERE NOT EXISTS (
            SELECT 1 FROM telegram_pushes p WHERE p.alert_id = a.id
        )
        ORDER BY a.created_at DESC
        LIMIT 100
    """).fetchall()

    pushed = 0
    for r in rows:
        alert = dict(r)
        # Always mark as seen so we never re-evaluate
        conn.execute(
            "INSERT OR IGNORE INTO telegram_pushes (alert_id) VALUES (?)",
            (alert["id"],),
        )
        conn.commit()

        if not _should_push(alert):
            continue

        msg     = _format_message(alert)
        success = _send(token, chat_id, msg, reply_markup=_decision_keyboard(alert))
        if success:
            pushed += 1
            print(f"[telegram] pushed alert {alert['id']} — {alert['rule']} {alert['ticker']}")
        else:
            print(f"[telegram] failed to push alert {alert['id']}")

    conn.close()
    print(f"[telegram] Done — {pushed} alerts pushed from {len(rows)} unseen")


if __name__ == "__main__":
    run()
