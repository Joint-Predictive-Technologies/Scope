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
import threading
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


# ── source-type taxonomy (for convergence + diversity) ───────────────────────
# Groups rules into the distinct *kinds* of signal they represent. Convergence
# (the hero) and overnight diversity both reason over these buckets, not raw
# rule names, so "three congressional rules" counts as ONE source type, not three.
_SOURCE_TYPE = {
    "RULE_01B": "Congress", "RULE_02": "Congress", "RULE_CLUSTER": "Congress",
    "RULE_06": "Insider",
    "RULE_11": "Contracts",
    "RULE_09": "Lobbying", "RULE_12": "Lobbying",
    "RULE_08": "Regulatory", "RULE_13": "Regulatory", "RULE_14": "Regulatory",
    "RULE_OSINT": "Geopolitical", "RULE_ADSB": "Geopolitical",
    "RULE_TELEGRAM_OSINT": "Geopolitical",
    "RULE_07": "Prediction",
    "RULE_10": "Corroboration",
    "RULE_15": "Earnings",
    "RULE_REDDIT": "Social",
}

# Number of distinct source-type families (denominator for the coverage notice).
_SOURCE_FAMILIES = len(set(_SOURCE_TYPE.values()))


def _source_type(rule: str) -> str | None:
    return _SOURCE_TYPE.get(rule)


def _synthesize_headline(conn) -> dict:
    """Build the hero, and only call it a CONVERGENCE when the gate would.

    The hero used to rank tickers by `_SOURCE_TYPE` — a taxonomy local to this
    file — and declare "X converges" at **2** distinct types. That contradicted
    the engine in two ways at once, and the result was a headline that sat on one
    ticker for days with nothing behind it:

      * the threshold was 2, while a real convergence is
        `RULE_10_MIN_INSTRUMENTS` (3) distinct INSTRUMENTS; and
      * the taxonomy counted the very rules the gate throws out as noise.
        Measured on the working DB: of the 71 alerts whose ticker is exactly
        `LMT`, **66 are RULE_OSINT (62) and RULE_ANOMALY (2) plus RULE_09 (2)** —
        with OSINT and ANOMALY both in `RULE_10_EXCLUDED`, and LMT hardcoded
        into six OSINT region ticker-lists (`api/main.py`), so that stream never
        stops and LMT won `max()` every single day.

    So the hero is now counted in the GATE's units, imported read-only: noise
    rules cannot manufacture a convergence, and "converges" is only ever printed
    when the corroboration engine would agree. Anything short of the bar is
    reported as coverage, named honestly, without the word.

      mode='converge'  >= RULE_10_MIN_INSTRUMENTS gate instruments on one ticker
      mode='coverage'  signals exist but no ticker meets the bar — says so
      mode='quiet'     nothing ticker-linked in the window
    """
    from collections import defaultdict
    from jpt_common import (RULE_10_EXCLUDED, RULE_10_MIN_INSTRUMENTS,
                            rule10_instruments)
    from scripts.rule_10_corroboration import (CONVERGENCE_WINDOW_DAYS,
                                               alert_corroborates)

    # The hero may only print "converges" where RULE_10 would actually fire, so
    # it has to count the way the gate counts — same ticker key, same severity
    # floor, same window (`_candidate_alerts`, rule_10_corroboration.py:158-170).
    # An earlier pass of this fix diverged on all three at once (it split
    # baskets, took every severity and looked back 7 days) and could therefore
    # still announce a convergence the engine refused — which is the exact class
    # of bug being fixed here, so the agreement is asserted by test.
    # ⚠️ `corroborates` IS SELECTED FOR THE SAME REASON THE OTHER THREE COLUMNS ARE
    # MATCHED: since 2026-07-30 the gate also decides per ALERT (an insider leg counts
    # only on a genuine open-market buy). Without it this hero would keep announcing a
    # convergence built on an insider SELL that the engine now refuses to fire — the exact
    # class of bug the agreement test exists to prevent, one field further in.
    rows = conn.execute(
        f"""SELECT ticker, rule, severity, headline, corroborates FROM alerts
            WHERE ticker IS NOT NULL AND ticker != ''
              AND severity IN ('HIGH','CRITICAL')
              AND created_at >= datetime('now','-{int(CONVERGENCE_WINDOW_DAYS)} days')"""
    ).fetchall()

    agg: dict = defaultdict(lambda: {"rules": set(), "noise": set(), "n": 0,
                                     "hi": 0, "example": None, "dropped": set()})
    for r in rows:
        rule = (r["rule"] or "").strip()
        # The GATE groups on the raw ticker string, so a basket ("LMT RTX NOC")
        # is its own key. Splitting it here would credit LMT with instruments the
        # gate never gives it — the measured effect was promoting LMT from 2
        # instruments to 3 and re-manufacturing the very hero this fix removes.
        tk = str(r["ticker"]).strip()
        if not tk:
            continue
        a = agg[tk]
        a["n"] += 1
        if rule.upper() in RULE_10_EXCLUDED:
            a["noise"].add(_source_type(rule) or rule)
        elif not alert_corroborates(r)[0]:
            # Present, eligible by name, but it does not say the bullish thing. It is not
            # noise either — record it separately so the brief can be honest about it
            # rather than counting it or silently discarding it.
            a["dropped"].add(rule)
        else:
            a["rules"].add(rule)
            # ⚠️ COUNTED ONLY FOR NON-EXCLUDED RULES, because `hi` is the TIE-BREAK
            # that decides who fronts the site once two tickers hold the same number
            # of gate instruments. Counting every alert here left the decision to raw
            # volume from rules the gate throws out.
            #
            # ⚠️ AND THE FIGURE FIRST QUOTED HERE WAS MEASURED OVER THE WRONG POPULATION.
            # It said LMT "won with hi=34, of which 25 (73.5%) were RULE_OSINT, excluding
            # them takes LMT to 9". Those counts are ALL-TIME; this function looks back
            # `CONVERGENCE_WINDOW_DAYS` (14), and inside that window LMT does not win even
            # before the fix — only 1 of its 25 OSINT HIGH/CRITICAL alerts falls in it.
            # The arithmetic was also wrong: 34 − 25 OSINT − 2 ANOMALY = **7**, not 9,
            # because ANOMALY is excluded too. Corrected rather than quietly dropped: a
            # number measured over one population and printed beside a function that uses
            # another is the defect this codebase keeps paying for.
            #
            # What IS re-derivable on the corpus: the tie-break stops being decided by
            # excluded volume. The behaviour is pinned by a seeded fixture in
            # `test_an_EXCLUDED_rules_VOLUME_cannot_choose_the_headline`, which fails when
            # this line is reverted. The earlier fix stopped an excluded rule CLAIMING a
            # convergence; this stops it CHOOSING the headline.
            a["hi"] += 1
        if a["example"] is None and rule.upper() not in RULE_10_EXCLUDED:
            # The example headline shown beside the hero must come from a signal that
            # counted toward it, not from whichever noise row happened to be first.
            a["example"] = r["headline"]
    if not agg:
        return {"mode": "quiet"}

    # Rank by gate instruments first — never by raw alert volume, which is what
    # let a single noisy source dominate.
    scored = {tk: (rule10_instruments(sorted(v["rules"])), v) for tk, v in agg.items()}
    tk, (instruments, top) = max(
        scored.items(), key=lambda kv: (len(kv[1][0]), kv[1][1]["hi"], kv[1][1]["n"]))

    if len(instruments) >= RULE_10_MIN_INSTRUMENTS:
        return {"mode": "converge", "ticker": tk, "types": sorted(instruments),
                "n": top["n"], "example": top["example"]}
    if not instruments:
        # Nothing has even ONE corroborating instrument. Naming a "leader" here
        # would promote a ticker whose entire presence is excluded-as-noise —
        # which is exactly how the stale hero got there. Say the true thing.
        return {"mode": "quiet", "noise_only": True,
                "noise": sorted(top["noise"]), "needed": RULE_10_MIN_INSTRUMENTS}
    return {"mode": "coverage", "ticker": tk, "types": sorted(instruments),
            "noise": sorted(top["noise"]), "needed": RULE_10_MIN_INSTRUMENTS,
            "n": top["n"], "example": top["example"]}


def _activity_strip(conn) -> dict:
    """Dense 24h activity figures for the main-page strip: distinct rules that
    fired (of the total), alerts generated, new clusters formed, and how many
    distinct ingestion/rule sources were active — plus a per-category breakdown."""
    from collections import defaultdict
    rule_runs = conn.execute(
        """SELECT source, COUNT(*) AS c FROM activity_log
           WHERE source LIKE 'RULE\\_%' ESCAPE '\\'
             AND run_at >= datetime('now','-24 hours')
           GROUP BY source"""
    ).fetchall()
    breakdown: dict = defaultdict(int)
    for r in rule_runs:
        breakdown[_source_type(r["source"]) or "Other"] += 1
    one = lambda q: (conn.execute(q).fetchone() or [0])[0]
    return {
        "rules_fired":  len(rule_runs),
        "rules_total":  RULES_TOTAL,
        "alerts_24h":   one("SELECT COUNT(*) FROM alerts WHERE created_at >= datetime('now','-24 hours')"),
        "clusters_24h": one("SELECT COUNT(*) FROM alerts WHERE rule='RULE_CLUSTER' AND created_at >= datetime('now','-24 hours')"),
        "sources_24h":  one("SELECT COUNT(DISTINCT source) FROM activity_log WHERE run_at >= datetime('now','-24 hours')"),
        "breakdown":    dict(sorted(breakdown.items(), key=lambda kv: -kv[1])),
    }


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

    # (a2) HERO — cross-source convergence (synthesized, not a single signal).
    d["hero"] = _synthesize_headline(conn)

    # (a3) ACTIVITY STRIP — dense 24h figures.
    d["activity_strip"] = _activity_strip(conn)

    # (c) OVERNIGHT SIGNALS — last 24h, DIVERSIFIED across source types rather
    # than the old OSINT+Polymarket-only filter (which collapsed to a defense
    # monoculture). Round-robin across buckets so no single source dominates;
    # carry an honest coverage figure (how many source families are active).
    from collections import defaultdict as _dd
    _raw = conn.execute(
        """SELECT rule, ticker, headline, tags, severity, created_at
           FROM alerts
           WHERE created_at >= datetime('now','-24 hours')
             AND rule != 'RULE_10'
           ORDER BY CASE WHEN severity IN ('CRITICAL','HIGH') THEN 0 ELSE 1 END,
                    datetime(created_at) DESC
           LIMIT 300"""
    ).fetchall()
    _buckets: dict = _dd(list)
    for _r in _raw:
        _st = _source_type(_r["rule"])
        if _st:
            _buckets[_st].append(dict(_r))
    _overnight: list = []
    _order = sorted(_buckets, key=lambda k: -len(_buckets[k]))
    while len(_overnight) < 6 and any(_buckets.values()):
        _progressed = False
        for _st in _order:
            if _buckets[_st]:
                _item = _buckets[_st].pop(0)
                _item["source_type"] = _st
                _overnight.append(_item)
                _progressed = True
                if len(_overnight) >= 6:
                    break
        if not _progressed:
            break
    d["overnight"] = _overnight
    d["overnight_coverage"] = {
        "active": len({_source_type(r["rule"]) for r in _raw if _source_type(r["rule"])}),
        "total": _SOURCE_FAMILIES,
    }

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
nav{position:sticky;top:0;z-index:100;border-bottom:1px solid var(--border-subtle);background:var(--surface-nav);backdrop-filter:blur(12px);padding:0 var(--space-6);display:flex;align-items:center;gap:var(--space-4);height:56px}
.brand{font-family:var(--font-sans);font-weight:var(--weight-semibold);font-size:var(--text-lg);letter-spacing:0.01em;color:var(--accent);text-decoration:none}
.nav-links{display:flex;align-items:center;gap:var(--space-4);overflow-x:auto;flex:1;scrollbar-width:none}
.nav-links::-webkit-scrollbar{display:none}
.nav-links a{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--text-secondary);text-decoration:none;letter-spacing:var(--tracking-label);text-transform:uppercase;white-space:nowrap}
.nav-links a:hover,.nav-links a.active{color:var(--accent)}
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
/* Ticker conveyor belt (restored; motion added in the functional-motion pass). */
.tape{border-bottom:1px solid var(--border-subtle);background:var(--surface-1);overflow:hidden;white-space:nowrap}
/* Continuous horizontal scroll (~40s), pauses on hover. transform only. Items
   are duplicated in JS so translateX(-50%) loops seamlessly. */
.tape-inner{display:inline-flex;gap:var(--space-5);padding:var(--space-2) var(--space-5);align-items:center;animation:tape-scroll 40s linear infinite;will-change:transform}
.tape:hover .tape-inner{animation-play-state:paused}
@keyframes tape-scroll{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.tape-item{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--text-secondary);text-decoration:none;display:inline-flex;gap:6px;align-items:center;white-space:nowrap}
.tape-item:hover{color:var(--accent)}
.tape-item .tk{color:var(--accent);font-weight:var(--weight-medium)}
.tape-item.sev-critical .tk{color:var(--severity-critical)}
.tape-item.sev-high .tk{color:var(--severity-high)}
/* Week calendar (7 cells: date + alert count, tinted by high/critical activity). */
.weekcal{display:grid;grid-template-columns:repeat(7,1fr);gap:var(--space-2)}
.daycell{border:1px solid var(--border-subtle);border-radius:var(--radius-sm);padding:var(--space-2);text-decoration:none;display:flex;flex-direction:column;gap:2px;transition:background 120ms ease}
.daycell:hover{background:var(--surface-2)}
.daycell .d{font-family:var(--font-mono);font-size:var(--text-2xs);color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.04em}
.daycell .n{font-family:var(--font-mono);font-size:var(--text-lg);color:var(--text-primary);font-variant-numeric:tabular-nums;transition:color 120ms ease}
.daycell:hover .n{color:var(--accent)}
.daycell.has-high{border-color:var(--severity-high)}
.daycell.today{border-left:2px solid var(--accent)}
/* Reduced motion: kill the belt scroll; hover/opacity states are fine. */
@media (prefers-reduced-motion:reduce){.tape-inner{animation:none}}
/* Hero (synthesized convergence) + dense activity strip. */
.hero-accent{color:var(--accent)}
.hero-context{font-family:var(--font-sans);font-size:var(--text-md);color:var(--text-secondary);line-height:var(--leading-prose);margin-top:var(--space-4);margin-bottom:var(--space-10)}
.hero-context .wr{white-space:nowrap}
.astrip{display:flex;flex-wrap:wrap;border:1px solid var(--border-subtle);border-radius:var(--radius-lg);overflow:hidden}
.astat{flex:1;min-width:130px;padding:var(--space-3) var(--space-4);border-right:1px solid var(--border-subtle);display:flex;flex-direction:column;gap:2px}
.astat:last-child{border-right:none}
.an{font-family:var(--font-mono);font-size:var(--text-2xl);color:var(--text-primary);font-variant-numeric:tabular-nums;line-height:1}
.an .ad{font-size:var(--text-md);color:var(--text-tertiary)}
.al{font-family:var(--font-mono);font-size:var(--text-2xs);text-transform:uppercase;letter-spacing:var(--tracking-label);color:var(--text-tertiary)}
.astrip-bd{font-family:var(--font-mono);font-size:var(--text-2xs);color:var(--text-tertiary);letter-spacing:0.03em;margin-top:var(--space-2)}
.stype{font-family:var(--font-mono);font-size:var(--text-2xs);text-transform:uppercase;letter-spacing:0.05em;color:var(--accent);min-width:88px}
"""


# The hand-rolled nav constant that used to live here HAS BEEN DELETED, and its
# absence is the point. This page is GENERATED, not a static file, so it was
# invisible to a sweep of `api/static/*.html` — and an independent verifier
# caught that the product's DEFAULT LANDING PAGE was still serving a
# twelve-link bar with no `/signals` in it, while the shared nav's own first
# entry ("Brief") pointed straight at it. The one page the new nav promoted was
# the one page it could not reach.
#
# The nav now comes from `/nav.js` like every other page. Re-adding a local
# copy here would recreate exactly the drift that caused this.

# Client-side hydration for the two live components on the main page: the ticker
# belt (/api/ticker-tape) and the week calendar (/api/activity). Plain string
# (NOT an f-string) so JS braces need no escaping. Both fail closed (hide belt /
# leave calendar empty) rather than showing a broken widget.
_MAIN_JS = """<script>
(function(){
  var esc=function(s){return (s==null?'':String(s)).replace(/[<>&]/g,'');};
  fetch('/api/ticker-tape').then(function(r){return r.json();}).then(function(items){
    var el=document.getElementById('tape'); if(!el)return;
    if(!Array.isArray(items)||!items.length){var t=el.closest('.tape'); if(t)t.style.display='none'; return;}
    var html=items.map(function(it){
      var sev=(it.severity||'').toLowerCase();
      var tk=(it.ticker||'').replace(/[^A-Za-z0-9.-]/g,'');
      var href=tk?('/ticker/'+encodeURIComponent(tk)):'/feed';
      return '<a class="tape-item sev-'+sev+'" href="'+href+'"><span class="tk">'+(tk||'\\u25CF')+'</span><span>'+esc(it.text)+'</span></a>';
    }).join('');
    el.innerHTML=html+html;  // duplicated so the translateX(-50%) scroll loops seamlessly
  }).catch(function(){var el=document.getElementById('tape'); var t=el&&el.closest('.tape'); if(t)t.style.display='none';});
  fetch('/api/activity?days=7&mode=signal').then(function(r){return r.json();}).then(function(res){
    var data=(res&&res.data)||{}; var el=document.getElementById('weekcal'); if(!el)return;
    var iso=function(dt){return dt.toISOString().slice(0,10);};
    var today=iso(new Date()); var cells=[];
    for(var i=6;i>=0;i--){
      var dt=new Date(Date.now()-i*86400000); var key=iso(dt); var rules=data[key]||{};
      var count=0,high=0;
      Object.keys(rules).forEach(function(k){count+=rules[k].count||0; high+=rules[k].high_crit||0;});
      var cls='daycell'+(key===today?' today':'')+(high>0?' has-high':'');
      var dow=dt.toLocaleDateString('en-US',{weekday:'short'});
      cells.push('<a class="'+cls+'" href="/feed?since='+key+'"><span class="d">'+dow+' '+dt.getDate()+'</span><span class="n">'+count+'</span></a>');
    }
    el.innerHTML=cells.join('');
  }).catch(function(){});
})();
</script>"""


def render_html(d: dict, date_str: str, preamble: str | None) -> str:
    parts = []
    P = parts.append

    # ── HERO — cross-source convergence (synthesized), not a single top signal.
    hero = d.get("hero") or {"mode": "quiet"}
    top = d.get("headline")  # strongest single signal → war-room link target
    top_wr = war_room_path(top["rule"], top["tags"], top["theme_id"], top["ticker"]) if top else "#"
    if hero.get("mode") == "converge":
        types = hero["types"]
        tlist = (", ".join(types[:-1]) + " and " + types[-1]) if len(types) > 1 else types[0]
        headline_txt = (f'<span class="hero-accent">{_esc(hero["ticker"])}</span> '
                        f'converges across {len(types)} instruments')
        context = (f'{_esc(tlist)} all point to {_esc(hero["ticker"])} in the last 7 days '
                   f'({hero["n"]} signals) — the day\'s strongest cross-source read.')
    elif hero.get("mode") == "coverage":
        # Deliberately does NOT print the word "converges": nothing cleared the
        # gate. Naming the leader is still useful, so long as the page does not
        # dress coverage up as corroboration.
        have = len(hero.get("types") or [])
        headline_txt = 'No cross-source convergence today'
        noise = hero.get("noise") or []
        noise_txt = (f' Its other mentions come from {_esc(", ".join(noise))}, '
                     f'which the corroboration engine excludes as noise.'
                     if noise else '')
        context = (f'The most-covered ticker is '
                   f'<span class="hero-accent">{_esc(hero["ticker"])}</span>, on '
                   f'{have} corroborating instrument{"" if have == 1 else "s"} — '
                   f'{hero.get("needed", 3)} are needed to fire.{noise_txt}')
    elif hero.get("noise_only"):
        headline_txt = 'No corroborating signal today'
        src = ", ".join(hero.get("noise") or []) or "excluded sources"
        context = (f'Signals arrived, but none from an instrument the '
                   f'corroboration engine counts — only {_esc(src)}, which it '
                   f'excludes as noise. No ticker has a single corroborating '
                   f'instrument, and {hero.get("needed", 3)} are needed to fire.')
    else:
        headline_txt = 'A quiet window'
        context = 'No ticker-linked signals in the last 7 days.'
    wr_link = f'<a class="wr" href="{_esc(top_wr)}">Open strongest war room →</a>' if top else ''
    hero_html = (f'<h1>{headline_txt}</h1>'
                 f'<div class="date">{date_str} · 06:30 UTC</div>'
                 f'<div class="hero-context">{context} {wr_link}</div>')

    # ── ACTIVITY STRIP — dense 24h figures + per-category breakdown.
    ast = d.get("activity_strip") or {}
    bd = ast.get("breakdown") or {}
    bd_txt = " · ".join(f"{k} {v}" for k, v in bd.items()) or "no rule runs logged"
    strip_html = (
        '<div class="astrip">'
        f'<div class="astat"><span class="an">{ast.get("rules_fired",0)}<span class="ad">/{ast.get("rules_total",0)}</span></span><span class="al">rules fired 24h</span></div>'
        f'<div class="astat"><span class="an">{ast.get("alerts_24h",0)}</span><span class="al">alerts 24h</span></div>'
        f'<div class="astat"><span class="an">{ast.get("clusters_24h",0)}</span><span class="al">new clusters 24h</span></div>'
        f'<div class="astat"><span class="an">{ast.get("sources_24h",0)}</span><span class="al">sources active 24h</span></div>'
        '</div>'
        f'<div class="astrip-bd">by category — {_esc(bd_txt)}</div>')

    # ── sections, in main-page order: overnight → clusters → theses → congress.
    # (c) overnight signals — diversified across source types, honest coverage.
    cov = d.get("overnight_coverage") or {}
    if d["overnight"]:
        rows = "".join(
            f'<div class="row"><span class="stype">{_esc(s.get("source_type",""))}</span>'
            f'<a href="{_esc(war_room_path(s["rule"], s["tags"], None, s["ticker"]))}">{_esc(s["headline"])}</a>'
            f'<span class="meta">{_ago(s["created_at"])}</span></div>'
            for s in d["overnight"])
        note = ""
        if cov.get("total") and cov.get("active", 0) < cov["total"]:
            note = (f'<div class="empty" style="margin-bottom:0.4rem">Limited source coverage — '
                    f'{cov.get("active",0)} of {cov["total"]} rule families active in the last 24h.</div>')
        P(f'<section id="overnight"><h2>Overnight Signals <a href="#overnight">¶</a></h2>{note}{rows}</section>')

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

    # (d) active theses
    if d["theses"]:
        rows = "".join(
            f'<div class="row"><a href="/thesis/{t["id"]}">{_esc(t["title"])}</a>'
            f'{f"<span class=tk style=min-width:auto>{_esc(_norm_ticker(t['primary_ticker']))}</span>" if t["primary_ticker"] else ""}'
            f'<span class="meta">ev {t["evidence_confidence"] or 0} · opp {t["opportunity_score"] or 0} · last {_ago(t["last_updated"])}</span></div>'
            for t in d["theses"])
        P(f'<section id="theses"><h2>Active Theses <a href="#theses">¶</a></h2>{rows}</section>')

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
          f'(<a href="/congress/digest/{_esc(date_str)}" style="color:var(--accent)">see full digest for this day →</a>):'
          f'</div>{rows}</section>')

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
{_TEMPLATE_MARKER}
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<script src="/theme.js"></script>
<link rel="stylesheet" href="/tokens.css"/>
<script src="/rule-names.js"></script>
<script src="/nav.js"></script>
<script src="/cmdk.js" defer></script>
<style>{_CSS}</style></head><body>
<nav><a href="/" class="brand">◈ SCOPE</a></nav>
<div class="tape" aria-label="Live signal ticker"><div class="tape-inner" id="tape"></div></div>
<div class="page">{hero_html}{strip_html}
<section id="thisweek"><h2>This Week <a href="#thisweek">¶</a></h2><div class="weekcal" id="weekcal"></div></section>
{pre}{body}</div>{_MAIN_JS}</body></html>"""


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

# Bump this whenever render_html's markup/design changes in a way that makes an
# already-cached brief visually stale (e.g. a design-system pass). A cached brief
# whose embedded marker != the current version is treated as a cache MISS by
# generate() and as "not current" by brief_is_current(), so the next scheduled
# run — or a non-blocking page-load-triggered regen — rebuilds it.
TEMPLATE_VERSION = "shared-nav-1"    # nav: rendered by /nav.js, not hand-rolled;
                                    # hero counted in the gate's units
_TEMPLATE_MARKER = f"<!--scope-brief-template:{TEMPLATE_VERSION}-->"

# In-process de-dup so concurrent page loads trigger at most one async regen/date.
_regen_lock = threading.Lock()
_regen_inflight: set[str] = set()


def brief_is_current(html: str | None) -> bool:
    """True if cached brief HTML was rendered by the CURRENT template version.

    Version-stale or empty briefs return False so callers can trigger a rebuild."""
    return bool(html) and _TEMPLATE_MARKER in html


def regenerate_if_stale_async(date_str: str) -> None:
    """Fire-and-forget rebuild of the brief for date_str, de-duplicated per date.

    Safe to call from a request handler: spawns a daemon thread and returns
    immediately so the page load never blocks on generation (LLM included)."""
    with _regen_lock:
        if date_str in _regen_inflight:
            return
        _regen_inflight.add(date_str)

    def _work():
        try:
            generate(date_str=date_str, force=True)
        except Exception as e:  # never let a background regen crash the worker
            print(f"[DAILY_BRIEF] async regen failed for {date_str}: {e}")
        finally:
            with _regen_lock:
                _regen_inflight.discard(date_str)

    threading.Thread(target=_work, name=f"brief-regen-{date_str}", daemon=True).start()


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
        if cached and cached["html"] and brief_is_current(cached["html"]):
            conn.close()
            return {"date": date_str, "html": cached["html"], "text": cached["text"],
                    "generated_at": cached["generated_at"], "cache": "hit"}
        # A cached-but-template-stale brief falls through and is regenerated below.

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
