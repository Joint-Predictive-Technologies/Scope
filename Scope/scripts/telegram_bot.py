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


def _send(bot_token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as exc:
        print(f"[telegram] request error: {exc}")
        return False


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
        success = _send(token, chat_id, msg)
        if success:
            pushed += 1
            print(f"[telegram] pushed alert {alert['id']} — {alert['rule']} {alert['ticker']}")
        else:
            print(f"[telegram] failed to push alert {alert['id']}")

    conn.close()
    print(f"[telegram] Done — {pushed} alerts pushed from {len(rows)} unseen")


if __name__ == "__main__":
    run()
