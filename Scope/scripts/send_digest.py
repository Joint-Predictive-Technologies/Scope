#!/usr/bin/env python3
"""
send_digest.py — Daily email digest with top 5 signals.
Sends at 7am via Gmail SMTP.

Setup:
  1. Enable 2FA on Gmail account
  2. Create an App Password: Google Account → Security → 2-Step → App passwords
  3. Set GMAIL_FROM and GMAIL_APP_PASSWORD in .env

Run via cron:
  0 7 * * * cd /path/to/Scope && python scripts/send_digest.py

Or manually: python scripts/send_digest.py --to your@email.com
"""
from __future__ import annotations

import argparse
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection

from dotenv import load_dotenv
load_dotenv()

SCOPE_URL = os.getenv("SCOPE_URL", "https://scope-production-1c3a.up.railway.app")
GMAIL_FROM = os.getenv("GMAIL_FROM", "")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

SEV_COLOR = {"CRITICAL": "#e55b4d", "HIGH": "#e8aa3a", "MEDIUM": "#6ab0e0"}
SEV_EMOJI = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡"}


def _gather_top_signals(conn) -> list[dict]:
    """Top signals for the email digest, ranked by `opportunity_score`.

    Was ordered by severity, then by a hardcoded rule ladder (RULE_10 -> 1,
    RULE_06 -> 2, RULE_11 -> 3, everything else -> 4), which pinned the same rules
    to the top of every digest regardless of score. Severity stays as the
    eligibility filter in WHERE but is no longer a ranking key — ranking on it
    first would leave `opportunity_score` a tiebreak inside a severity band.

    Same *pattern* as scripts/morning_brief.py's headline query — not identical to
    it: that one adds `COALESCE(evidence_confidence,0) DESC` as its second key and
    uses a 24h window with LIMIT 1, where this keeps its own 48h window and LIMIT 5.
    What is shared is the part that matters: score first, severity only as the
    WHERE floor. (That floor is why ranking by score is safe here and was NOT safe
    in api/routers/chat.py, which has no severity filter — see
    tests/test_surfacing_sibling_ladders.py.)

    Ordering only; scores are computed at write time in jpt_common and are
    untouched here.
    """
    rows = conn.execute("""
        SELECT rule, ticker, severity, headline, detail, created_at, why_matters,
               COALESCE(opportunity_score, 0) AS opportunity_score
        FROM alerts
        WHERE severity IN ('CRITICAL', 'HIGH')
          AND datetime(created_at) >= datetime('now', '-48 hours')
        ORDER BY COALESCE(opportunity_score, 0) DESC,
                 datetime(created_at) DESC,
                 id DESC
        LIMIT 5
    """).fetchall()
    return [dict(r) for r in rows]


def _build_html(signals: list[dict], date_str: str) -> str:
    body_bg = "#0c0b09"
    card_bg = "#111009"
    amber = "#c8922a"
    cream = "#e8e0cc"
    muted = "#7a7060"

    signal_blocks = ""
    for s in signals:
        ticker = (s.get("ticker") or "").replace("$", "").split()[0]
        color = SEV_COLOR.get(s["severity"], muted)
        emoji = SEV_EMOJI.get(s["severity"], "⚪")
        detail = (s.get("detail") or "")[:200]
        why = s.get("why_matters") or ""
        ticker_link = f'<a href="{SCOPE_URL}/ticker/{ticker}" style="color:{amber}">${ticker}</a>' if ticker else ""
        signal_blocks += f"""
        <div style="background:{card_bg};border:1px solid #2a2620;border-radius:6px;padding:16px 20px;margin-bottom:12px;">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <span style="font-family:monospace;font-size:11px;color:{color};border:1px solid {color}40;padding:2px 8px;border-radius:3px;">{s['severity']}</span>
            <span style="font-family:monospace;font-size:11px;color:{muted};border:1px solid #3a3428;padding:2px 8px;border-radius:3px;">{s['rule']}</span>
            {ticker_link}
          </div>
          <div style="font-size:15px;color:{cream};margin-bottom:6px;">{emoji} {s['headline']}</div>
          {f'<div style="font-size:13px;color:{muted};margin-bottom:6px;">{detail}…</div>' if detail else ''}
          {f'<div style="font-size:12px;color:#c8bfa8;font-style:italic;border-left:2px solid {amber};padding-left:8px;">Why this matters: {why}</div>' if why else ''}
        </div>"""

    if not signal_blocks:
        signal_blocks = f'<p style="color:{muted};font-family:monospace;font-size:13px;">No high-priority signals in the last 48 hours.</p>'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="background:{body_bg};color:{cream};font-family:Inter,Helvetica,sans-serif;margin:0;padding:0;">
  <div style="max-width:600px;margin:0 auto;padding:32px 24px;">
    <div style="font-family:Georgia,serif;font-size:28px;font-weight:700;color:{amber};margin-bottom:4px;">◈ SCOPE</div>
    <div style="font-family:monospace;font-size:11px;color:{muted};letter-spacing:0.1em;text-transform:uppercase;margin-bottom:24px;">Daily Brief — {date_str}</div>

    <h2 style="font-family:Georgia,serif;font-size:20px;color:{cream};margin-bottom:16px;">Top Signals Today</h2>
    {signal_blocks}

    <div style="margin-top:24px;padding-top:16px;border-top:1px solid #2a2620;">
      <a href="{SCOPE_URL}/feed" style="display:inline-block;font-family:monospace;font-size:12px;letter-spacing:0.08em;text-transform:uppercase;background:{amber};color:{body_bg};padding:10px 20px;border-radius:4px;text-decoration:none;font-weight:600;">View Live Feed →</a>
      &nbsp;&nbsp;
      <a href="{SCOPE_URL}/ask" style="display:inline-block;font-family:monospace;font-size:12px;letter-spacing:0.08em;text-transform:uppercase;border:1px solid {amber};color:{amber};padding:10px 20px;border-radius:4px;text-decoration:none;">Ask Scope →</a>
    </div>

    <p style="font-size:11px;color:{muted};margin-top:24px;font-family:monospace;">
      Scope Political Intelligence · AI SUMMARY — not investment advice<br>
      <a href="{SCOPE_URL}" style="color:{muted};">scope-production-1c3a.up.railway.app</a>
    </p>
  </div>
</body>
</html>"""


def send(recipient: str, signals: list[dict]) -> bool:
    if not GMAIL_FROM or not GMAIL_PASSWORD:
        print("[digest] GMAIL_FROM or GMAIL_APP_PASSWORD not set — skipping send")
        return False

    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Scope Brief — {today}"
    msg["From"] = GMAIL_FROM
    msg["To"] = recipient

    html = _build_html(signals, today)
    plain = f"Scope Daily Brief — {today}\n\n" + "\n\n".join(
        f"{s['severity']} [{s['rule']}] {s.get('ticker','')}: {s['headline']}" for s in signals
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_FROM, GMAIL_PASSWORD)
            server.sendmail(GMAIL_FROM, recipient, msg.as_string())
        print(f"[digest] Sent to {recipient}")
        return True
    except Exception as e:
        print(f"[digest] Send failed: {e}")
        return False


def run(recipients: list[str]) -> None:
    conn = db_connection()
    signals = _gather_top_signals(conn)
    conn.close()

    print(f"[digest] {len(signals)} signals found")
    for r in recipients:
        send(r, signals)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Send Scope daily digest email")
    p.add_argument("--to", nargs="+", required=False, help="Recipient email(s)")
    args = p.parse_args()

    load_dotenv()
    recipients = args.to or [os.getenv("DIGEST_RECIPIENT", "")]
    recipients = [r for r in recipients if r]

    if not recipients:
        print("[digest] No recipients — set DIGEST_RECIPIENT in .env or pass --to email@example.com")
        sys.exit(1)

    run(recipients)
