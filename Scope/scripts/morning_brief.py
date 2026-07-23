#!/usr/bin/env python3
"""
morning_brief.py — the deterministic Daily Morning Brief (source DAILY_BRIEF).

A synthesis of what already exists — it computes nothing new, only reads existing
tables. Renders server-side HTML (/brief/<date>) and a standalone plain-text
version (/brief/<date>.txt), cached in the `briefs` table so a subscriber loads
instantly.

Anti-slop: every fact, count, timestamp and link is deterministic. Groq is used
for at most ONE thing — a 1-2 sentence "what's noteworthy" preamble, clearly
labeled, wrapped so its failure never blocks the brief.

Sections (in order): (a) headline, (b) yesterday in congress, (c) overnight
signals, (d) active theses, (e) live clusters, (f) earnings this week [omitted —
RULE_15's source has no forward earnings calendar], (g) system health.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpt_common import db_connection, record_activity, congress_day_digest

RULES_TOTAL = 19  # ground-truth rule count (see CLAUDE.md rules table)


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _ago(ts) -> str:
    if not ts:
        return "—"
    try:
        t = datetime.fromisoformat(str(ts).replace(" ", "T"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except ValueError:
        return "—"
    secs = (datetime.now(timezone.utc) - t).total_seconds()
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _norm_ticker(t) -> str:
    return (t or "").replace("$", "").split(" ")[0]


def war_room_path(rule, tags_json, theme_id, ticker) -> str:
    if rule == "RULE_CLUSTER":
        try:
            fp = json.loads(tags_json or "{}").get("fingerprint", "")
            if fp:
                return "/cluster/" + fp.replace("CLUSTER::", "").replace("::", "__")
        except Exception:
            pass
    if rule == "RULE_10" and theme_id:
        return f"/thesis/{theme_id}"
    tk = _norm_ticker(ticker)
    return f"/ticker/{tk}" if tk else "#"


# ── gather (all deterministic) ────────────────────────────────────────────────

def gather(conn) -> dict:
    d: dict = {}

    # (a) HEADLINE — top opportunity among HIGH/CRITICAL in last 24h.
    d["headline"] = conn.execute(
        """SELECT id, rule, ticker, severity, headline, why_matters, tags, theme_id,
                  opportunity_score, evidence_confidence, created_at
           FROM alerts
           WHERE severity IN ('HIGH','CRITICAL')
             AND created_at >= datetime('now','-24 hours')
           ORDER BY COALESCE(opportunity_score,0) DESC,
                    COALESCE(evidence_confidence,0) DESC,
                    datetime(created_at) DESC
           LIMIT 1"""
    ).fetchone()

    # (b) YESTERDAY IN CONGRESS — rolling last-24h digest (top 10). Same shared
    # aggregation the standalone /congress/digest/<date> view uses (full list).
    d["congress"] = congress_day_digest(conn, day=None, limit=10)

    # (c) OVERNIGHT SIGNALS — OSINT + Polymarket in last 24h (IMMEDIATE horizon).
    d["overnight"] = [dict(r) for r in conn.execute(
        """SELECT rule, ticker, headline, tags, created_at
           FROM alerts
           WHERE rule IN ('RULE_OSINT','RULE_07')
             AND created_at >= datetime('now','-24 hours')
           ORDER BY datetime(created_at) DESC LIMIT 6"""
    ).fetchall()]

    # (d) ACTIVE THESES.
    d["theses"] = [dict(r) for r in conn.execute(
        """SELECT id, title, primary_ticker, evidence_confidence, opportunity_score,
                  last_updated, status
           FROM themes
           WHERE status IN ('Emerging','Developing','Confirmed')
           ORDER BY COALESCE(opportunity_score,0) DESC"""
    ).fetchall()]

    # (e) LIVE CLUSTERS — RULE_CLUSTER in last 7 days (incl. superseded chains).
    d["clusters"] = [dict(r) for r in conn.execute(
        """SELECT id, ticker, headline, tags, lifecycle_stage, created_at
           FROM alerts
           WHERE rule='RULE_CLUSTER' AND created_at >= datetime('now','-7 days')
           ORDER BY datetime(created_at) DESC LIMIT 25"""
    ).fetchall()]

    # (f) EARNINGS THIS WEEK — RULE_15's source (SEC 8-K sentiment) has no forward
    # earnings calendar, and we may not add a new source, so per spec this section
    # is omitted whenever no forward-dated earnings data exists (always, today).
    d["earnings"] = _earnings_this_week(conn)

    # (g) SYSTEM HEALTH.
    ran = {r["source"] for r in conn.execute(
        "SELECT DISTINCT source FROM activity_log WHERE run_at >= datetime('now','-24 hours')"
    ).fetchall()}
    stalls = [dict(r) for r in conn.execute(
        """SELECT run_at, notes FROM activity_log
           WHERE source='MONITOR_ENRICH_STALL' AND notes LIKE 'CRITICAL%'
             AND run_at >= datetime('now','-24 hours') ORDER BY id DESC"""
    ).fetchall()]
    roster = [dict(r) for r in conn.execute(
        """SELECT run_at, notes FROM activity_log
           WHERE source='ROSTER_CHECK' AND notes LIKE 'WARNING%'
             AND run_at >= datetime('now','-30 days') ORDER BY id DESC LIMIT 5"""
    ).fetchall()]
    d["health"] = {"rules_ran": len([s for s in ran if s.startswith("RULE_")]),
                   "rules_total": RULES_TOTAL, "stalls": stalls, "roster": roster}

    return d


def _earnings_this_week(conn) -> list:
    """Forward earnings on watched tickers within 7 days. RULE_15 stores only
    historical 8-K filing_dates (no forward calendar), so this returns [] and the
    section is skipped — never render empty. Written defensively so it lights up
    automatically if a forward-dated earnings source is ever added."""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(earnings_sentiment)").fetchall()}
        if "next_report_date" not in cols:
            return []
        watched = [r["t"] for r in conn.execute(
            """SELECT DISTINCT REPLACE(ticker,'$','') t FROM alerts
               WHERE severity IN ('HIGH','CRITICAL')
                 AND created_at >= datetime('now','-30 days')
                 AND ticker IS NOT NULL AND ticker != ''"""
        ).fetchall()]
        if not watched:
            return []
        ph = ",".join("?" * len(watched))
        return [dict(r) for r in conn.execute(
            f"""SELECT ticker, next_report_date FROM earnings_sentiment
                WHERE ticker IN ({ph})
                  AND date(next_report_date) BETWEEN date('now') AND date('now','+7 days')""",
            watched,
        ).fetchall()]
    except Exception:
        return []


def _preamble(d: dict) -> str | None:
    """Optional Groq 1-2 sentence 'what's noteworthy'. Never blocks the brief."""
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        return None
    facts = (f"headline={d['headline']['headline'] if d['headline'] else 'none'}; "
             f"active_theses={len(d['theses'])}; live_clusters={len(d['clusters'])}; "
             f"congress_txns_24h={d['congress']['total_txns']}; "
             f"overnight_signals={len(d['overnight'])}")
    try:
        from groq import Groq
        client = Groq(api_key=key)
        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile", max_tokens=120,
            messages=[
                {"role": "system", "content": "You write a 1-2 sentence factual preamble "
                 "for a political-market intelligence brief. No hype, no advice, no adjectives "
                 "like 'strong'/'significant'. Just what to pay attention to today."},
                {"role": "user", "content": f"Facts: {facts}. Write the preamble."},
            ],
            timeout=8,
        )
        return r.choices[0].message.content.strip()
    except Exception:
        return None


# ── render HTML ───────────────────────────────────────────────────────────────

_CSS = """
/* Morning brief — editorial register (Slash-inspired). The ONLY serif hero. */
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--surface-canvas);color:var(--text-primary);font-family:var(--font-sans);min-height:100vh}
nav{position:sticky;top:0;z-index:100;border-bottom:1px solid var(--border-subtle);background:rgba(10,10,12,0.95);backdrop-filter:blur(12px);padding:0 var(--space-6);display:flex;align-items:center;gap:var(--space-4);height:56px}
.brand{font-family:var(--font-sans);font-weight:var(--weight-semibold);font-size:var(--text-lg);letter-spacing:0.01em;color:var(--accent);text-decoration:none}
.nav-links a{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--text-secondary);text-decoration:none;letter-spacing:var(--tracking-label);text-transform:uppercase;margin-left:var(--space-5)}
.nav-links a:hover{color:var(--accent)}
.page{max-width:860px;margin:0 auto;padding:var(--space-12) var(--space-5) var(--space-16)}
h1{font-family:var(--font-serif);font-weight:var(--weight-normal);font-size:var(--text-3xl);letter-spacing:-0.01em;line-height:1.15;color:var(--text-primary)}
.date{font-family:var(--font-sans);font-size:var(--text-sm);color:var(--text-tertiary);margin-top:var(--space-2);margin-bottom:var(--space-12)}
.summary-chip{display:inline-block;font-family:var(--font-mono);font-size:var(--text-2xs);letter-spacing:var(--tracking-label);text-transform:uppercase;color:var(--text-tertiary);margin-bottom:var(--space-2)}
.preamble{font-family:var(--font-sans);font-size:var(--text-md);color:var(--text-secondary);line-height:var(--leading-prose);border-left:2px solid var(--accent-dim);padding-left:var(--space-4);margin-bottom:var(--space-12)}
.gen-tag{display:none}
section{margin-top:var(--section-gap-editorial)}
section h2{font-family:var(--font-sans);font-size:var(--text-lg);font-weight:var(--weight-semibold);letter-spacing:-0.01em;color:var(--text-primary);margin-bottom:var(--space-4);border-bottom:1px solid var(--border-subtle);padding-bottom:var(--space-2)}
section h2 a{color:var(--text-tertiary);text-decoration:none;font-size:var(--text-sm);font-family:var(--font-mono);float:right;font-weight:var(--weight-normal)}
.headline-card{background:var(--surface-1);border:1px solid var(--border-subtle);border-left:3px solid var(--accent);border-radius:var(--radius-lg);padding:var(--space-4) var(--space-5)}
.headline-card.critical{border-left-color:var(--severity-critical)}
.hl{font-size:var(--text-md);color:var(--text-primary);margin:var(--space-1) 0;line-height:var(--leading-body)}
.why{font-size:var(--text-sm);color:var(--text-secondary);line-height:var(--leading-prose)}
.badge{font-family:var(--font-mono);font-size:var(--text-2xs);padding:2px 7px;border-radius:var(--radius-sm);letter-spacing:0.05em}
.b-critical{background:color-mix(in srgb,var(--severity-critical) 15%,transparent);color:var(--severity-critical);border:1px solid var(--severity-critical)}
.b-high{background:color-mix(in srgb,var(--severity-high) 15%,transparent);color:var(--severity-high);border:1px solid var(--severity-high)}
.row{display:flex;gap:var(--space-3);align-items:baseline;padding:var(--space-2) 0;border-bottom:1px solid var(--border-subtle);font-size:var(--text-sm)}
.row .tk{font-family:var(--font-mono);color:var(--accent);min-width:70px}
.row .meta{color:var(--text-tertiary);font-family:var(--font-mono);font-size:var(--text-xs);margin-left:auto;font-variant-numeric:tabular-nums}
.row a{color:var(--text-secondary);text-decoration:none}.row a:hover{color:var(--accent)}
.dir{font-family:var(--font-mono);font-size:var(--text-2xs);padding:1px 6px;border-radius:var(--radius-sm)}
.dir.consensus_buy{background:color-mix(in srgb,var(--direction-buy) 14%,transparent);color:var(--direction-buy)}
.dir.consensus_sell{background:color-mix(in srgb,var(--direction-sell) 14%,transparent);color:var(--direction-sell)}
.dir.mixed{background:color-mix(in srgb,var(--direction-neutral) 14%,transparent);color:var(--direction-neutral)}
.superseded{opacity:0.5}
a.wr{color:var(--accent);text-decoration:none;font-family:var(--font-mono);font-size:var(--text-xs)}
.health{margin-top:var(--section-gap-editorial);padding-top:var(--space-3);border-top:1px solid var(--border-subtle);font-family:var(--font-mono);font-size:var(--text-xs);color:var(--text-tertiary)}
.health .crit{color:var(--severity-critical)}
.empty{color:var(--text-tertiary);font-size:var(--text-sm);font-style:italic}
"""


def render_html(d: dict, date_str: str, preamble: str | None) -> str:
    parts = []
    P = parts.append

    # (a) headline
    h = d["headline"]
    if h:
        wr = war_room_path(h["rule"], h["tags"], h["theme_id"], h["ticker"])
        sev = (h["severity"] or "").lower()
        P(f'<section id="headline"><h2>Headline <a href="#headline">¶</a></h2>'
          f'<div class="headline-card {sev}">'
          f'<span class="badge b-{sev}">{_esc(h["severity"])}</span> '
          f'<span class="badge" style="color:var(--muted)">{_esc(h["rule"])}</span>'
          f'<div class="hl">{_esc(h["headline"])}</div>'
          f'{f"<div class=why>{_esc(h['why_matters'])}</div>" if h["why_matters"] else ""}'
          f'<div style="margin-top:0.5rem"><a class="wr" href="{_esc(wr)}">Open war room →</a> '
          f'<span class="meta">opp {h["opportunity_score"] or 0} · ev {h["evidence_confidence"] or 0} · {_ago(h["created_at"])}</span></div>'
          f'</div></section>')

    # (b) yesterday in congress
    cg = d["congress"]
    if cg["rows"]:
        rows = "".join(
            f'<div class="row"><span class="tk">{_esc(r["ticker"])}</span>'
            f'<a href="/ticker/{_esc(_norm_ticker(r["ticker"]))}">{r["members"]} member{"s" if r["members"]!=1 else ""} · '
            f'{r["buys"] or 0} buy / {r["sells"] or 0} sell</a>'
            f'<span class="meta">{r["txns"]} txns</span></div>'
            for r in cg["rows"])
        P(f'<section id="congress"><h2>Yesterday in Congress <a href="#congress">¶</a></h2>'
          f'<div class="empty" style="margin-bottom:0.4rem">{cg["total_txns"]} congressional '
          f'transactions across {len(cg["rows"])} tickers (last 24h). Highlights '
          f'(<a href="/congress/digest/{_esc(date_str)}" style="color:var(--amber)">see full digest for this day →</a>):'
          f'</div>{rows}</section>')

    # (c) overnight signals
    if d["overnight"]:
        rows = "".join(
            f'<div class="row"><span class="badge" style="color:var(--muted)">{_esc(s["rule"])}</span>'
            f'<a href="{_esc(war_room_path(s["rule"], s["tags"], None, s["ticker"]))}">{_esc(s["headline"])}</a>'
            f'<span class="meta">{_ago(s["created_at"])}</span></div>'
            for s in d["overnight"])
        P(f'<section id="overnight"><h2>Overnight Signals <a href="#overnight">¶</a></h2>{rows}</section>')

    # (d) active theses
    if d["theses"]:
        rows = "".join(
            f'<div class="row"><a href="/thesis/{t["id"]}">{_esc(t["title"])}</a>'
            f'{f"<span class=tk style=min-width:auto>{_esc(_norm_ticker(t['primary_ticker']))}</span>" if t["primary_ticker"] else ""}'
            f'<span class="meta">ev {t["evidence_confidence"] or 0} · opp {t["opportunity_score"] or 0} · last {_ago(t["last_updated"])}</span></div>'
            for t in d["theses"])
        P(f'<section id="theses"><h2>Active Theses <a href="#theses">¶</a></h2>{rows}</section>')

    # (e) live clusters
    if d["clusters"]:
        rows = ""
        for c in d["clusters"]:
            tg = json.loads(c["tags"] or "{}") if c["tags"] else {}
            wr = war_room_path("RULE_CLUSTER", c["tags"], None, c["ticker"])
            sup = "superseded" if c["lifecycle_stage"] == "superseded" else ""
            direction = tg.get("direction", "")
            rows += (f'<div class="row {sup}"><span class="tk">{_esc(c["ticker"])}</span>'
                     f'<a href="{_esc(wr)}">{_esc(c["headline"])}</a>'
                     f'{f"<span class=dir {direction}>{_esc(direction.replace(chr(95),chr(32)))}</span>" if direction else ""}'
                     f'<span class="meta">{_ago(c["created_at"])}{" · superseded" if sup else ""}</span></div>')
        P(f'<section id="clusters"><h2>Live Clusters <a href="#clusters">¶</a></h2>{rows}</section>')

    # (f) earnings — only if forward data exists
    if d["earnings"]:
        rows = "".join(
            f'<div class="row"><span class="tk">{_esc(e["ticker"])}</span>'
            f'<span>reports {_esc(e.get("next_report_date"))}</span></div>'
            for e in d["earnings"])
        P(f'<section id="earnings"><h2>Earnings This Week <a href="#earnings">¶</a></h2>{rows}</section>')

    # (g) system health
    hh = d["health"]
    health = f'{hh["rules_ran"]}/{hh["rules_total"]} rules ran in the last 24h.'
    if hh["stalls"]:
        health += f' <span class="crit">⚠ {len(hh["stalls"])} scoring-stall CRITICAL(s) in 24h.</span>'
    if hh["roster"]:
        health += f' <span class="crit">⚠ {len(hh["roster"])} roster warning(s) in 30d.</span>'
    if not hh["stalls"] and not hh["roster"]:
        health += " No stall or roster warnings."
    P(f'<div class="health" id="health">{health}</div>')

    pre = ""
    if preamble:
        pre = (f'<div class="summary-chip">Summary</div>'
               f'<div class="preamble">{_esc(preamble)}</div>')

    body = "".join(parts) or '<div class="empty">No activity to report in this window.</div>'
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Daily Brief {date_str} — Scope</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<link rel="stylesheet" href="/tokens.css"/>
<style>{_CSS}</style></head><body>
<nav><a href="/" class="brand">◈ SCOPE</a><div class="nav-links">
<a href="/feed">Alerts</a><a href="/clusters">Clusters</a><a href="/intelligence">Theses</a></div></nav>
<div class="page"><h1>Daily Brief</h1><div class="date">{date_str} · 06:30 UTC</div>
{pre}{body}</div></body></html>"""


# ── render plain text (email/telegram ready) ─────────────────────────────────

def render_text(d: dict, date_str: str, preamble: str | None) -> str:
    L = []
    L.append(f"SCOPE DAILY BRIEF — {date_str}")
    L.append("=" * 48)
    if preamble:
        L.append("")
        L.append(f"[SUMMARY — generated] {preamble}")
    h = d["headline"]
    if h:
        L += ["", "HEADLINE", "-" * 8,
              f"[{h['severity']}/{h['rule']}] {h['headline']}"]
        if h["why_matters"]:
            L.append(f"  {h['why_matters']}")
        L.append(f"  opp {h['opportunity_score'] or 0} · ev {h['evidence_confidence'] or 0} · {_ago(h['created_at'])}")

    cg = d["congress"]
    if cg["rows"]:
        L += ["", "YESTERDAY IN CONGRESS", "-" * 21,
              f"{cg['total_txns']} transactions across {len(cg['rows'])} tickers. Highlights:"]
        for r in cg["rows"]:
            L.append(f"  {r['ticker']}: {r['members']} members · {r['buys'] or 0} buy / {r['sells'] or 0} sell ({r['txns']} txns)")
        L.append(f"  Full digest for this day: /congress/digest/{date_str}")

    if d["overnight"]:
        L += ["", "OVERNIGHT SIGNALS", "-" * 17]
        for s in d["overnight"]:
            L.append(f"  [{s['rule']}] {s['headline']} ({_ago(s['created_at'])})")

    if d["theses"]:
        L += ["", "ACTIVE THESES", "-" * 13]
        for t in d["theses"]:
            tk = _norm_ticker(t["primary_ticker"])
            L.append(f"  {t['title']}{(' ['+tk+']') if tk else ''} — ev {t['evidence_confidence'] or 0} · opp {t['opportunity_score'] or 0} · last {_ago(t['last_updated'])}")

    if d["clusters"]:
        L += ["", "LIVE CLUSTERS (7d)", "-" * 18]
        for c in d["clusters"]:
            tg = json.loads(c["tags"] or "{}") if c["tags"] else {}
            sup = " (superseded)" if c["lifecycle_stage"] == "superseded" else ""
            L.append(f"  {c['ticker']}: {c['headline']}{sup}")

    if d["earnings"]:
        L += ["", "EARNINGS THIS WEEK", "-" * 18]
        for e in d["earnings"]:
            L.append(f"  {e['ticker']} reports {e.get('next_report_date')}")

    hh = d["health"]
    L += ["", "SYSTEM HEALTH", "-" * 13,
          f"  {hh['rules_ran']}/{hh['rules_total']} rules ran in last 24h"]
    if hh["stalls"]:
        L.append(f"  ! {len(hh['stalls'])} scoring-stall CRITICAL(s) in 24h")
    if hh["roster"]:
        L.append(f"  ! {len(hh['roster'])} roster warning(s) in 30d")
    if not hh["stalls"] and not hh["roster"]:
        L.append("  no stall or roster warnings")

    if len(L) <= 2:
        L.append("\nNo activity to report in this window.")
    return "\n".join(L) + "\n"


# ── generate + cache ──────────────────────────────────────────────────────────

def _sections_populated(d: dict) -> int:
    n = 0
    if d["headline"]: n += 1
    if d["congress"]["rows"]: n += 1
    if d["overnight"]: n += 1
    if d["theses"]: n += 1
    if d["clusters"]: n += 1
    if d["earnings"]: n += 1
    n += 1  # system health always renders
    return n


def generate(date_str: str | None = None, force: bool = False, use_llm: bool = True) -> dict:
    t0 = time.time()
    conn = db_connection()
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not force:
        cached = conn.execute(
            "SELECT html, text, generated_at FROM briefs WHERE date=?", (date_str,)
        ).fetchone()
        if cached and cached["html"]:
            conn.close()
            return {"date": date_str, "html": cached["html"], "text": cached["text"],
                    "generated_at": cached["generated_at"], "cache": "hit"}

    d = gather(conn)
    preamble = _preamble(d) if use_llm else None
    html_doc = render_html(d, date_str, preamble)
    text_doc = render_text(d, date_str, preamble)
    generated_at = datetime.now(timezone.utc).isoformat()
    meta = {
        "sections_populated": _sections_populated(d),
        "headline_rule": d["headline"]["rule"] if d["headline"] else None,
        "active_theses": len(d["theses"]),
        "live_clusters": len(d["clusters"]),
    }
    conn.execute(
        "INSERT OR REPLACE INTO briefs (date, html, text, meta_json, generated_at) "
        "VALUES (?,?,?,?,?)",
        (date_str, html_doc, text_doc, json.dumps(meta), generated_at),
    )
    conn.commit()
    conn.close()

    notes = (f"sections_populated={meta['sections_populated']}, "
             f"headline_rule={meta['headline_rule']}, "
             f"active_theses={meta['active_theses']}, live_clusters={meta['live_clusters']}")
    record_activity("DAILY_BRIEF", scanned=_sections_populated(d), flagged=meta["active_theses"],
                    emitted=1, duration_seconds=round(time.time() - t0, 2), notes=notes)
    print(f"[DAILY_BRIEF] {date_str} — {notes}")
    return {"date": date_str, "html": html_doc, "text": text_doc,
            "generated_at": generated_at, "cache": "miss", "meta": meta}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Generate the deterministic daily morning brief.")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default today UTC)")
    p.add_argument("--force", action="store_true", help="Regenerate even if cached")
    p.add_argument("--no-llm", action="store_true", help="Skip the Groq preamble")
    p.add_argument("--emit-alerts", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    generate(date_str=args.date, force=args.force, use_llm=not args.no_llm)
