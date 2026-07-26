#!/usr/bin/env python3
"""
Scope API — FastAPI wrapper around jpt.db.
Run from the Scope/ directory:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import sys
import os
import asyncio
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from api.routers import (
    alerts, chat, members, tickers,
    filter as filter_router,
    social, backtest, sectors, digest,
    brief, contracts, congress, history,
    api_v1, performance, intel,
    lobbying as lobbying_router,
    evidence,
    themes as themes_router,
    annotations as annotations_router,
    warroom as warroom_router,
)

STATIC_DIR = Path(__file__).parent / "static"
CODE_DIR   = Path(__file__).resolve().parent.parent

REFRESH_INTERVAL_HOURS = 4

# ── Per-rule cadences ─────────────────────────────────────────────────────────
# interval jobs: {script: minutes}
_RULE_SCHEDULE: dict[str, int] = {
    # GDELT updates every 15 min
    "scripts/rule_osint.py":               15,
    # ADS-B flight tracking
    "scripts/rule_adsb.py":                5,
    "scripts/rule_options_correlation.py": 15,
    # Polymarket prices shift continuously
    "rule_07_polymarket.py":               20,
    # Reddit sentiment
    "scripts/rule_reddit.py":              30,
    # Congress first-touch + SEC form-4 — House updates daily
    "scripts/rule_01b_first_touch.py":     120,
    "rule_06_form4.py":                    120,
    # Corroboration runs after other rules
    "scripts/rule_10_corroboration.py":    60,
    # Phase-2 scoring — score any newly-inserted alerts every 10 min
    "scripts/enrich_scores.py":            10,
    # Stall monitor — make a silent enrich_scores failure loud (hourly)
    "scripts/monitor_enrich_stall.py":     60,
    # Anomaly detection
    "scripts/rule_anomaly.py":             180,
    # Congressional cluster detection — 3+ members, same ticker, 72h
    "scripts/rule_cluster.py":             240,
    # RULE_02 — 3+ members, same ticker, 7-day window (looser sibling of
    # RULE_CLUSTER). Same 4h cadence as the rule it overlaps; independent
    # subprocess so co-firing with rule_cluster is harmless.
    "rule_02_cluster.py":                  240,
    # Government contracts
    "scripts/rule_11_contracts.py":        360,
    # Federal Register publishes daily ~6am ET, check every 4h
    "rule_08_federal_register.py":         240,
    # Telegram OSINT
    "scripts/rule_telegram_osint.py":      60,
    # Earnings call NLP — earnings happen throughout the day
    "scripts/rule_15_earnings_nlp.py":     360,
    "scripts/telegram_bot.py":             60,
}

# cron jobs: {script: {"hour": H, "minute": M[, "day_of_week": DOW]}}
_CRON_SCHEDULE: dict[str, dict] = {
    # LDA lobbying filings are quarterly — daily scan at 3am is enough
    "rule_09_lobbying.py":              {"hour": 3,  "minute": 0},
    # Daily brief after overnight data collection
    "generate_brief.py":                {"hour": 6,  "minute": 30},
    # Deterministic morning brief (source DAILY_BRIEF) — generates + caches at 06:30.
    "scripts/morning_brief.py":         {"hour": 6,  "minute": 30},
    # FARA filings update slowly — weekly Monday 4am scan is sufficient
    "scripts/rule_12_fara.py":          {"day_of_week": "mon", "hour": 4,  "minute": 0},
    # Broad lobbying dataset (AIPAC + notable lobbies) — weekly Monday 4:45am
    "scripts/ingest_lobbying.py":       {"day_of_week": "mon", "hour": 4,  "minute": 45},
    # FEC PAC monitoring — daily at 5am
    "scripts/rule_13_fec.py":           {"hour": 5,  "minute": 0},
    # USPTO patents — twice weekly (Tue + Fri early morning)
    "scripts/rule_14_patents.py":       {"day_of_week": "tue,fri", "hour": 4, "minute": 30},
    # ── Congressional ingestion (offset so stage 2 always trails stage 1) ──
    # Stage 1: House PTR index -> filings (registers new pending), every 6h on the hour.
    "ingest_house_index.py":            {"hour": "0,6,12,18", "minute": 0},
    # Stage 2: parse pending PTR PDFs -> transactions, every 4h at :30 (never
    # simultaneous with stage 1; runs more often so it always finds work).
    "parse_house_pdfs.py":              {"hour": "0,4,8,12,16,20", "minute": 30},
    # Alert severity decay — daily at 1am, keeps the feed and counts honest
    "scripts/decay_alerts.py":          {"hour": 1,  "minute": 0},
    # Verified compressed DB backup — daily 3am UTC, low-traffic hour (DB_BACKUP).
    "scripts/db_backup.py":             {"hour": 3,  "minute": 0},
    # (interval job for scoring is registered in _RULE_SCHEDULE)
    # Backtest rebuild — weekly Sunday 2am (Yahoo Finance price lookups)
    "scripts/run_backtest.py":          {"day_of_week": "sun", "hour": 2, "minute": 0},
    # Roster freshness — monthly (1st, 4am): flag recurring unmatched House filers.
    "scripts/roster_check.py":          {"day": 1, "hour": 4, "minute": 0},
    # Forward-return outcome labeling — daily 2am (matches decay cadence).
    "scripts/label_outcomes.py":        {"hour": 2, "minute": 0},
}


def _alert_count() -> int:
    import sqlite3
    from jpt_common import _get_db_path
    db_path = str(_get_db_path(None))
    try:
        conn = sqlite3.connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return -1


def _hours_since_last_alert() -> float:
    import sqlite3
    from datetime import datetime, timezone
    from jpt_common import _get_db_path
    db_path = str(_get_db_path(None))
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT MAX(created_at) FROM alerts").fetchone()
        conn.close()
        if not row or not row[0]:
            return float("inf")
        last = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600
    except Exception:
        return float("inf")


def _log_job_failure(rule: str, kind: str, detail: str) -> None:
    """Universal safety net: guarantee an activity_log row for ANY scheduled-job
    failure — including import-time crashes (ImportError/SyntaxError) that happen
    before the script's own error handling can run and would otherwise vanish."""
    tail = (detail or "").strip()
    last_line = next((ln for ln in reversed(tail.splitlines()) if ln.strip()), "")
    notes = f"CRITICAL: job '{rule}' {kind}. {last_line[:200]} | tail: {tail[-300:]}"
    try:
        from jpt_common import record_activity
        record_activity("SCHEDULER_JOB_FAILURE", scanned=0, flagged=0, emitted=0, notes=notes)
    except Exception as exc:  # never let the safety net itself take down the scheduler
        print(f"[scheduler] failed to log job failure for {rule}: {exc}", flush=True)


def _run_rule(rule: str) -> str:
    """Run a single rule script; return 'ok' or the last 300 chars of stderr.

    Rule scripts write their own activity_log row (with scanned/flagged/emitted)
    from inside run(); the scheduler additionally guarantees a
    SCHEDULER_JOB_FAILURE row for ANY failure (non-zero exit, import-time crash,
    timeout, or exception invoking the subprocess) so no failure is ever silent.
    """
    try:
        r = subprocess.run(
            [sys.executable, rule, "--emit-alerts"],
            capture_output=True, text=True, timeout=300,
            cwd=str(CODE_DIR),
        )
        if r.returncode == 0:
            result = "ok"
        else:
            result = r.stderr[-300:].strip()
            _log_job_failure(rule, f"exited with code {r.returncode}", r.stderr or r.stdout)
    except subprocess.TimeoutExpired:
        result = "timeout after 300s"
        _log_job_failure(rule, "timed out after 300s", "subprocess.TimeoutExpired")
    except Exception as e:
        result = str(e)[:200]
        _log_job_failure(rule, f"could not be invoked ({type(e).__name__})", str(e))
    print(f"[scheduler] {rule}: {result}", flush=True)
    return result


def _run_rules(rules: list[str]) -> dict[str, str]:
    return {rule: _run_rule(rule) for rule in rules}


_scheduler = None
_job_last_run: dict[str, str] = {}


def _start_scheduler() -> None:
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("[scheduler] apscheduler not installed — falling back to single loop", flush=True)
        return

    _scheduler = BackgroundScheduler(daemon=True)

    def _make_job(r: str):
        def _job():
            _job_last_run[r] = "running"
            _job_last_run[r] = _run_rule(r)
        return _job

    for rule, minutes in _RULE_SCHEDULE.items():
        _scheduler.add_job(
            _make_job(rule),
            IntervalTrigger(minutes=minutes),
            id=rule.replace("/", "_").replace(".", "_"),
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,  # survive Railway container restarts
        )

    for rule, cron_kwargs in _CRON_SCHEDULE.items():
        _scheduler.add_job(
            _make_job(rule),
            CronTrigger(**cron_kwargs),
            id=rule.replace("/", "_").replace(".", "_"),
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,
        )

    import atexit
    atexit.register(lambda: _scheduler.shutdown(wait=False))

    _scheduler.start()
    total = len(_RULE_SCHEDULE) + len(_CRON_SCHEDULE)
    print(f"[scheduler] APScheduler started — {total} jobs scheduled", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    hours_stale = _hours_since_last_alert()
    if hours_stale >= REFRESH_INTERVAL_HOURS:
        print(f"[startup] data is {hours_stale:.1f}h old — running all rules now …", flush=True)
        asyncio.create_task(asyncio.to_thread(_run_rules, [*_RULE_SCHEDULE, *_CRON_SCHEDULE]))
    else:
        print(f"[startup] data is fresh ({hours_stale:.1f}h old)", flush=True)

    # Start APScheduler (non-blocking background thread)
    _start_scheduler()
    yield

    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


app = FastAPI(
    title="Scope Political Intelligence API",
    description="Real-time alerts on congressional trading, insider activity, lobbying, and regulatory proposals.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/swagger",   # move interactive Swagger; /docs is the canonical human API docs page
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(alerts.router,        prefix="/alerts",   tags=["Alerts"])
app.include_router(chat.router,          prefix="/chat",     tags=["Chat"])
app.include_router(members.router,       prefix="/members",  tags=["Members"])
app.include_router(tickers.router,       prefix="/tickers",  tags=["Tickers"])
app.include_router(filter_router.router, prefix="/filter",   tags=["Filter"])
app.include_router(social.router,        prefix="/social",    tags=["Social"])
app.include_router(backtest.router,      prefix="/backtest",  tags=["Backtest"])
app.include_router(sectors.router,       prefix="/sectors",   tags=["Sectors"])
@app.get("/digest", include_in_schema=False)
def digest_entry(request: Request):
    """Content-negotiated entry (registered BEFORE the digest router so the JSON
    root no longer shadows the page): browsers (Accept: text/html) get the
    tokenized digest.html; the page's own fetch('/digest') and API clients get
    the weekly-digest JSON."""
    if "text/html" in request.headers.get("accept", ""):
        return FileResponse(STATIC_DIR / "digest.html")
    from api.routers.digest import get_digest
    return get_digest()


app.include_router(digest.router,        prefix="/digest",    tags=["Digest"])
app.include_router(brief.router,         prefix="/brief",     tags=["Brief"])
app.include_router(contracts.router,     prefix="/contracts", tags=["Contracts"])
app.include_router(congress.router,      prefix="/congress",  tags=["Congress"])
app.include_router(history.router,       prefix="/history",   tags=["History"])
app.include_router(api_v1.router,        prefix="/api/v1",    tags=["Public API v1"])
app.include_router(performance.router,   prefix="/api/performance", tags=["Performance"])
app.include_router(intel.router,         prefix="/api/intel", tags=["Intel"])
app.include_router(lobbying_router.router, prefix="/api/lobbying", tags=["Lobbying"])
app.include_router(evidence.router,      prefix="/api/evidence", tags=["Evidence"])
app.include_router(themes_router.router, prefix="/api/themes", tags=["Themes"])
app.include_router(annotations_router.router, prefix="/api/annotations", tags=["Annotations"])
app.include_router(warroom_router.router, prefix="/api", tags=["War Rooms"])


@app.get("/health", tags=["Health"])
def health():
    import sqlite3
    from pathlib import Path
    from jpt_common import _get_db_path
    db_path = str(_get_db_path(None))
    groq_set = bool(os.getenv("GROQ_API_KEY", "").strip())
    db_exists = Path(db_path).exists()
    alert_count = 0
    db_error = None
    if db_exists:
        try:
            conn = sqlite3.connect(db_path)
            alert_count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            conn.close()
        except Exception as e:
            db_error = str(e)
    return {
        "status": "ok",
        "db_path": db_path,
        "db_exists": db_exists,
        "db_error": db_error,
        "alert_count": alert_count,
        "groq_key_set": groq_set,
        "cwd": os.getcwd(),
    }


@app.get("/api/scheduler-status", tags=["Admin"])
def scheduler_status():
    """Return the state of each scheduled rule job."""
    if _scheduler is None:
        return {"status": "not_started", "jobs": []}
    # Last activity_log run per source (ground truth that a job actually ran).
    last_runs: dict = {}
    try:
        from jpt_common import db_connection as _dbc
        _conn = _dbc()
        for r in _conn.execute(
            "SELECT source, MAX(run_at) AS last_run, "
            "       (SELECT alerts_emitted FROM activity_log a2 WHERE a2.source = a1.source "
            "        ORDER BY run_at DESC LIMIT 1) AS last_emitted "
            "FROM activity_log a1 GROUP BY source"
        ).fetchall():
            last_runs[r["source"]] = {"last_run": r["last_run"], "last_emitted": r["last_emitted"]}
        _conn.close()
    except Exception:
        pass

    jobs = []
    for job in _scheduler.get_jobs():
        interval_seconds = None
        try:
            interval_seconds = int(job.trigger.interval.total_seconds())  # IntervalTrigger
        except Exception:
            pass
        jobs.append({
            "id":         job.id,
            "next_run":   str(job.next_run_time) if job.next_run_time else None,
            "last_result": _job_last_run.get(job.id, "pending"),
            "trigger":    str(job.trigger),
            "interval_seconds": interval_seconds,
        })
    return {
        "status":    "running" if _scheduler.running else "stopped",
        "job_count": len(jobs),
        "jobs":      sorted(jobs, key=lambda j: j["next_run"] or ""),
        "activity":  last_runs,
    }


@app.get("/admin/refresh", tags=["Admin"])
def admin_refresh(key: str = ""):
    """Manually trigger all live rules. Pass ?key=ADMIN_KEY."""
    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key or key != admin_key:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"error": "Invalid or missing key"})
    results = _run_rules(LIVE_RULES)
    return {"status": "done", "alert_count": _alert_count(), "results": results}


@app.post("/admin/upload-db", tags=["Admin"])
async def admin_upload_db(key: str, request: "Request"):
    """
    Replace the live DB with an uploaded binary — writes directly to the volume.
    POST /admin/upload-db?key=ADMIN_KEY  (raw .db bytes as request body)
    """
    from fastapi.responses import JSONResponse
    import shutil
    from jpt_common import _get_db_path

    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key or key != admin_key:
        return JSONResponse(status_code=403, content={"error": "Invalid or missing key"})

    body = await request.body()
    if len(body) < 4096:
        return JSONResponse(status_code=400, content={"error": "Body too small — send the full .db file"})

    db_path = _get_db_path(None)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    tmp = Path(str(db_path) + ".upload_tmp")
    tmp.write_bytes(body)
    shutil.move(str(tmp), str(db_path))

    size = db_path.stat().st_size
    return {"status": "ok", "db_path": str(db_path), "size_bytes": size, "size_mb": round(size / 1e6, 2)}


@app.get("/api/telegram/test", tags=["Admin"])
def telegram_test():
    """Send a test message to verify the Telegram bot is connected."""
    import requests as _rq
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"ok": False,
                     "error": "TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set"},
        )
    try:
        r = _rq.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "✅ *Scope* — Telegram bot connected. "
                        "Critical and corroborated signals will arrive here.",
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        return {"ok": r.status_code == 200, "telegram_status": r.status_code}
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)[:200]})


TAPE_RULE_LABELS = {
    "RULE_01":      "Congressional",
    "RULE_01B":     "Congressional",
    "RULE_02":      "Cluster",
    "RULE_06":      "Insider",
    "RULE_07":      "Polymarket",
    "RULE_08":      "Fed Register",
    "RULE_09":      "Lobbying",
    "RULE_10":      "Corroboration",
    "RULE_11":      "Gov Contract",
    "RULE_12":      "FARA",
    "RULE_13":      "PAC Funding",
    "RULE_14":      "Patent Cluster",
    "RULE_15":      "Earnings NLP",
    "RULE_ANOMALY": "Attention",
    "RULE_ADSB":    "ADSB",
    "RULE_TELEGRAM_OSINT": "OSINT",
}
TAPE_RULE_ICON = {
    "RULE_01":  "↑", "RULE_01B": "↑", "RULE_02": "↑",
    "RULE_06":  "↓", "RULE_07":  "↑", "RULE_08": "↑",
    "RULE_09":  "↑", "RULE_10":  "★", "RULE_11": "●",
    "RULE_12":  "🌍", "RULE_13": "💰", "RULE_14": "🔬",
    "RULE_15":  "📞",
    "RULE_ANOMALY": "⚡", "RULE_ADSB": "✈", "RULE_TELEGRAM_OSINT": "📡",
}


@app.get("/api/stats", tags=["Meta"])
def api_stats():
    """Single-shot endpoint for homepage stats — avoids 3 separate JS fetches."""
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    try:
        corr_count = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE rule='RULE_10' "
            "AND datetime(created_at) >= datetime('now', '-30 days')"
        ).fetchone()[0]
        ticker_count = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM alerts WHERE ticker IS NOT NULL AND ticker != ''"
        ).fetchone()[0]
        member_count = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        alert_count = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE severity IN ('HIGH','CRITICAL') "
            "AND datetime(created_at) >= datetime('now', '-24 hours')"
        ).fetchone()[0]
        crit_count = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE severity = 'CRITICAL' "
            "AND datetime(created_at) >= datetime('now', '-24 hours')"
        ).fetchone()[0]
        week_tickers = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM alerts "
            "WHERE datetime(created_at) >= datetime('now', '-7 days') AND ticker IS NOT NULL AND ticker != ''"
        ).fetchone()[0]
        data_through = conn.execute("SELECT MAX(created_at) FROM alerts").fetchone()[0]
    finally:
        conn.close()
    from datetime import datetime as _dt, timezone as _tz
    return {
        "corr_count":    corr_count,     # 30 days
        "ticker_count":  ticker_count,   # all-time distinct
        "member_count":  member_count,
        "alert_count":   alert_count,    # HIGH+ last 24h
        "crit_count":    crit_count,     # CRITICAL last 24h
        "week_tickers":  week_tickers,   # 7 days
        "generated_at":  _dt.now(_tz.utc).isoformat(),
        "data_through":  data_through,
    }


@app.get("/api/activity", tags=["Meta"])
def api_activity(mode: str = "signal", days: int = 14):
    """
    Daily per-rule signal activity for the heat map.

    mode="signal" (default) buckets by the real event date where known
    (event_date), falling back to created_at. mode="ingestion" buckets by
    created_at (when the alert was written — reflects bulk imports/backfill).

    Each cell reports count, unique tickers, high/critical count, and how many
    were backfilled (event_date differs from the ingestion day), so a bulk
    import can't masquerade as a genuine same-day market event.
    """
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    mode = "ingestion" if mode == "ingestion" else "signal"
    day_expr = ("date(created_at)" if mode == "ingestion"
                else "date(COALESCE(event_date, created_at))")
    rows = conn.execute(
        f"""
        SELECT {day_expr} AS day, rule,
               COUNT(*) AS count,
               COUNT(DISTINCT ticker) AS tickers,
               SUM(CASE WHEN severity IN ('HIGH','CRITICAL') THEN 1 ELSE 0 END) AS high_crit,
               SUM(CASE WHEN event_date IS NOT NULL
                         AND date(event_date) != date(created_at) THEN 1 ELSE 0 END) AS backfilled
        FROM alerts
        WHERE {day_expr} >= date('now', ?)
          AND {day_expr} <= date('now')
        GROUP BY day, rule
        ORDER BY day, rule
        """,
        (f"-{int(days)} days",),
    ).fetchall()
    conn.close()
    result: dict = {}
    for row in rows:
        day = row["day"]
        result.setdefault(day, {})[row["rule"]] = {
            "count":      row["count"],
            "tickers":    row["tickers"] or 0,
            "high_crit":  row["high_crit"] or 0,
            "backfilled": row["backfilled"] or 0,
        }
    return {"mode": mode, "days": days, "data": result}


@app.get("/api/activity-log", tags=["Meta"])
def activity_log(limit: int = 20):
    """Recent scan runs — proves the system is watching even when nothing fired."""
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    rows = conn.execute(
        """SELECT source, events_scanned, events_flagged, alerts_emitted,
                  run_at, duration_seconds, notes
           FROM activity_log ORDER BY datetime(run_at) DESC LIMIT ?""",
        (max(1, min(limit, 100)),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/ticker-tape", tags=["Meta"])
def ticker_tape():
    """Live signal items for the scrolling ticker tape."""
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    rows = conn.execute("""
        SELECT rule, ticker, headline, severity
        FROM alerts
        WHERE severity IN ('HIGH', 'CRITICAL')
          AND rule NOT IN ('RULE_ANOMALY', 'RULE_11', 'RULE_07', 'RULE_OSINT', 'RULE_REDDIT')
          AND datetime(created_at) >= datetime('now', '-7 days')
        ORDER BY datetime(created_at) DESC
        LIMIT 60
    """).fetchall()
    conn.close()

    items = []
    seen: set = set()
    for r in rows:
        rule    = r["rule"] or ""
        ticker  = (r["ticker"] or "").replace("$", "").split(" ")[0][:6]
        headline = (r["headline"] or "")
        # Deduplicate: one entry per rule+ticker combination
        key = (rule, ticker)
        if key in seen:
            continue
        seen.add(key)
        label   = TAPE_RULE_LABELS.get(rule, rule)
        icon    = TAPE_RULE_ICON.get(rule, "●")
        if ticker:
            text = f"{label} — {ticker}"
        else:
            text = f"{label} — {headline[:35]}"
        items.append({"text": text, "rule": rule, "icon": icon,
                      "ticker": ticker, "severity": r["severity"]})

    if not items:
        return []
    # Duplicate to keep belt full (need at least 10 for seamless loop)
    while len(items) < 10:
        items = items + items
    return items[:20]


@app.get("/api/watchlist-rules", tags=["Watchlist"])
def get_watchlist_rules():
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    rows = conn.execute("SELECT id, label, condition_type, condition_value, created_at FROM watchlist_rules ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


class _WatchlistRuleBody(BaseModel):
    label: str
    condition_type: str
    condition_value: str


@app.post("/api/watchlist-rules", tags=["Watchlist"])
def add_watchlist_rule(body: _WatchlistRuleBody):
    from fastapi.responses import JSONResponse
    label = (body.label or "").strip()
    ctype = (body.condition_type or "").strip()
    cvalue = (body.condition_value or "").strip()
    if not label or not ctype or not cvalue:
        return JSONResponse(status_code=422, content={"error": "label, condition_type, condition_value required"})
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    cur = conn.execute(
        "INSERT INTO watchlist_rules (label, condition_type, condition_value) VALUES (?, ?, ?)",
        (label, ctype, cvalue),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"id": new_id, "label": label, "condition_type": ctype, "condition_value": cvalue}


@app.delete("/api/watchlist-rules/{rule_id}", tags=["Watchlist"])
def delete_watchlist_rule(rule_id: int):
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    conn.execute("DELETE FROM watchlist_rules WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()
    return {"deleted": rule_id}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    """Land on today's cached morning brief (Phase 2: brief as default landing).
    Falls back to yesterday's brief with a notice, then to the live feed with a
    notice. Never generates a brief on page load — only reads the cache.
    Reversible: restore `return FileResponse(STATIC_DIR / "index.html")`."""
    from datetime import datetime, timezone, timedelta
    from fastapi.responses import RedirectResponse
    from jpt_common import db_connection
    from api.landing import resolve_landing, inject_brief_header
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    conn = db_connection()
    try:
        mode, html, notice = resolve_landing(conn, today, yesterday)
    finally:
        conn.close()
    if mode == "brief" and html:
        # If today's brief exists but was rendered by an older template version
        # (e.g. before a design pass), serve it now and kick off a non-blocking
        # rebuild so the next load is fresh. `notice is None` == today's brief
        # (not a yesterday fallback); the fallback path keeps the existing policy.
        if notice is None:
            try:
                from scripts.morning_brief import brief_is_current, regenerate_if_stale_async
                if not brief_is_current(html):
                    regenerate_if_stale_async(today)
            except Exception:
                pass  # regen is best-effort; never block the landing page on it
        return HTMLResponse(inject_brief_header(html, notice))
    return RedirectResponse(url="/feed?notice=nobrief", status_code=302)

@app.get("/home", response_class=HTMLResponse, include_in_schema=False)
def landing_dashboard():
    """The original dashboard/landing, preserved (was at `/` before brief-as-landing)."""
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/feed", response_class=HTMLResponse, include_in_schema=False)
def feed():
    return FileResponse(STATIC_DIR / "alerts.html")

@app.get("/tokens.css", include_in_schema=False)
def design_tokens():
    """Single source of truth for the design system (Fey/Slash synthesis)."""
    return FileResponse(STATIC_DIR / "tokens.css", media_type="text/css")

@app.get("/ask", response_class=HTMLResponse, include_in_schema=False)
def ask():
    return FileResponse(STATIC_DIR / "chat.html")

@app.get("/members-list", response_class=HTMLResponse, include_in_schema=False)
def members_list_page():
    return FileResponse(STATIC_DIR / "members.html")

@app.get("/watchlist", response_class=HTMLResponse, include_in_schema=False)
def watchlist_page():
    return FileResponse(STATIC_DIR / "watchlist.html")

@app.get("/ticker/{symbol}", response_class=HTMLResponse, include_in_schema=False)
def ticker_page(symbol: str):
    return FileResponse(STATIC_DIR / "ticker.html")

@app.get("/member/{bioguide_id}", response_class=HTMLResponse, include_in_schema=False)
def member_page(bioguide_id: str):
    return FileResponse(STATIC_DIR / "member.html")

@app.get("/backtest", response_class=HTMLResponse, include_in_schema=False)
def backtest_page():
    return FileResponse(STATIC_DIR / "backtest.html")

@app.get("/sectors", response_class=HTMLResponse, include_in_schema=False)
def sectors_page():
    return FileResponse(STATIC_DIR / "sectors.html")

# NOTE: /digest is served by digest_entry() (content-negotiated) registered above
# the digest router — it replaced the old digest_page() that the JSON router shadowed.

@app.get("/brief", response_class=HTMLResponse, include_in_schema=False)
def brief_page():
    return FileResponse(STATIC_DIR / "brief.html")

def _valid_brief_date(date: str) -> bool:
    import re as _re
    from datetime import datetime as _dt
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return False
    try:
        _dt.strptime(date, "%Y-%m-%d")
        return True
    except ValueError:
        return False

@app.get("/brief/{date}.txt", include_in_schema=False)
def morning_brief_text(date: str):
    from fastapi.responses import PlainTextResponse
    from scripts.morning_brief import generate
    if not _valid_brief_date(date):
        return PlainTextResponse("Not found", status_code=404)
    out = generate(date_str=date)
    return PlainTextResponse(out["text"])

@app.get("/brief/{date}", response_class=HTMLResponse, include_in_schema=False)
def morning_brief_page(date: str):
    from scripts.morning_brief import generate
    if not _valid_brief_date(date):
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    out = generate(date_str=date)
    return HTMLResponse(out["html"])

# ── Standalone congressional digest (browsable, dated) ───────────────────────
def _digest_today() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

@app.get("/api/congress-digest", tags=["Congress"])
@app.get("/api/congress-digest/{date}", tags=["Congress"])
def congress_digest_data(date: str | None = None):
    """Full below-threshold congressional trade digest for a day (no top-N cap).
    Same shared aggregation the morning brief's section (b) uses."""
    from datetime import datetime, timedelta, timezone
    from fastapi.responses import JSONResponse
    from jpt_common import db_connection as _dbc, congress_day_digest
    day = date or _digest_today()
    if not _valid_brief_date(day):
        return JSONResponse(status_code=404, content={"error": "bad date"})
    conn = _dbc()
    out = congress_day_digest(conn, day=day, limit=None)
    conn.close()
    d = datetime.strptime(day, "%Y-%m-%d").date()
    today = datetime.now(timezone.utc).date()
    out["prev"] = (d - timedelta(days=1)).isoformat()
    out["next"] = (d + timedelta(days=1)).isoformat() if d < today else None
    return out

@app.get("/congress/digest", response_class=HTMLResponse, include_in_schema=False)
@app.get("/congress/digest/{date}", response_class=HTMLResponse, include_in_schema=False)
def congress_digest_page(date: str | None = None):
    return FileResponse(STATIC_DIR / "congress_digest.html")

@app.get("/contracts", response_class=HTMLResponse, include_in_schema=False)
def contracts_page():
    return FileResponse(STATIC_DIR / "contracts.html")

@app.get("/congress", response_class=HTMLResponse, include_in_schema=False)
def congress_page():
    return FileResponse(STATIC_DIR / "congress.html")

@app.get("/history", response_class=HTMLResponse, include_in_schema=False)
def history_page():
    return FileResponse(STATIC_DIR / "history.html")

@app.get("/insiders", response_class=HTMLResponse, include_in_schema=False)
def insiders_page():
    return FileResponse(STATIC_DIR / "insiders.html")

@app.get("/lobbying", response_class=HTMLResponse, include_in_schema=False)
def lobbying_page():
    return FileResponse(STATIC_DIR / "lobbying.html")

@app.get("/osint", response_class=HTMLResponse, include_in_schema=False)
def osint_page():
    return FileResponse(STATIC_DIR / "osint.html")

@app.get("/region/{region_name}", response_class=HTMLResponse, include_in_schema=False)
def region_page(region_name: str):
    return FileResponse(STATIC_DIR / "osint_region.html")

@app.get("/performance", response_class=HTMLResponse, include_in_schema=False)
def performance_page():
    return FileResponse(STATIC_DIR / "performance.html")

@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
def docs_page():
    return FileResponse(STATIC_DIR / "api_docs.html")

@app.get("/api-docs", include_in_schema=False)
def api_docs_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs", status_code=308)

@app.get("/foreign-influence", response_class=HTMLResponse, include_in_schema=False)
def foreign_influence_page():
    return FileResponse(STATIC_DIR / "foreign_influence.html")

@app.get("/intelligence", response_class=HTMLResponse, include_in_schema=False)
def intelligence_page():
    return FileResponse(STATIC_DIR / "intelligence.html")

@app.get("/status", response_class=HTMLResponse, include_in_schema=False)
def status_page():
    return FileResponse(STATIC_DIR / "status.html")

@app.get("/thesis/{theme_id}", response_class=HTMLResponse, include_in_schema=False)
def thesis_page(theme_id: int):
    return FileResponse(STATIC_DIR / "thesis.html")

@app.get("/theses", response_class=HTMLResponse, include_in_schema=False)
def theses_page():
    return FileResponse(STATIC_DIR / "intelligence.html")

@app.get("/clusters", response_class=HTMLResponse, include_in_schema=False)
def clusters_page():
    return FileResponse(STATIC_DIR / "clusters.html")

@app.get("/cluster/{fingerprint}", response_class=HTMLResponse, include_in_schema=False)
def cluster_page(fingerprint: str):
    return FileResponse(STATIC_DIR / "cluster.html")

@app.get("/sector/{sector_name}", response_class=HTMLResponse, include_in_schema=False)
def sector_war_room_page(sector_name: str):
    return FileResponse(STATIC_DIR / "sector.html")


# ── OSINT / Globe API ──────────────────────────────────────────────────────────

REGION_COORDS = {
    "Middle East":      (31.0, 35.0),
    "Eastern Europe":   (49.0, 32.0),
    "Russia":           (60.0, 90.0),
    "Taiwan Strait":    (23.5, 120.5),
    "Korean Peninsula": (37.5, 127.5),
    "South China Sea":  (12.0, 114.0),
    "South Asia":       (30.0, 70.0),
    "West Africa":      (8.0, 2.0),
    "East Africa":      (0.0, 38.0),
    "Latin America":    (-15.0, -60.0),
    "North Africa":     (25.0, 15.0),
    "Southeast Asia":   (10.0, 106.0),
}


_DEMO_HOTSPOTS = [
    {"region": "Middle East",      "lat": 29.5,  "lng": 45.0,   "severity": "HIGH",     "count": 3, "ticker_list": ["USO", "XLE", "LMT"],        "last_at": None},
    {"region": "Eastern Europe",   "lat": 49.0,  "lng": 31.0,   "severity": "HIGH",     "count": 2, "ticker_list": ["LMT", "RTX", "NOC"],        "last_at": None},
    {"region": "Russia",           "lat": 60.0,  "lng": 90.0,   "severity": "MEDIUM",   "count": 1, "ticker_list": ["LMT", "RTX"],               "last_at": None},
    {"region": "Taiwan Strait",    "lat": 24.0,  "lng": 121.0,  "severity": "CRITICAL", "count": 2, "ticker_list": ["TSM", "NVDA", "AMAT"],      "last_at": None},
    {"region": "Korean Peninsula", "lat": 37.5,  "lng": 127.5,  "severity": "MEDIUM",   "count": 1, "ticker_list": ["LMT", "RTX"],               "last_at": None},
    {"region": "South China Sea",  "lat": 12.0,  "lng": 114.0,  "severity": "HIGH",     "count": 2, "ticker_list": ["TSM", "LMT", "NOC"],        "last_at": None},
    {"region": "South Asia",       "lat": 28.0,  "lng": 77.0,   "severity": "MEDIUM",   "count": 1, "ticker_list": ["LMT", "RTX"],               "last_at": None},
    {"region": "West Africa",      "lat": 8.0,   "lng": 2.0,    "severity": "MEDIUM",   "count": 1, "ticker_list": ["XOM", "CVX"],               "last_at": None},
    {"region": "East Africa",      "lat": 0.0,   "lng": 38.0,   "severity": "MEDIUM",   "count": 1, "ticker_list": ["USO", "XLE"],               "last_at": None},
    {"region": "Latin America",    "lat": -15.0, "lng": -60.0,  "severity": "MEDIUM",   "count": 1, "ticker_list": ["XOM", "CVX"],               "last_at": None},
    {"region": "North Africa",     "lat": 25.0,  "lng": 15.0,   "severity": "MEDIUM",   "count": 1, "ticker_list": ["USO", "XLE"],               "last_at": None},
    {"region": "Southeast Asia",   "lat": 10.0,  "lng": 106.0,  "severity": "MEDIUM",   "count": 1, "ticker_list": ["TSM", "NVDA"],              "last_at": None},
]


@app.get("/api/osint-hotspots", tags=["OSINT"])
def osint_hotspots():
    """Return grouped RULE_OSINT alerts from last 14 days as globe hotspots."""
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    from jpt_common import db_connection as _dbc, REGION_TICKERS
    conn = _dbc()
    rows = conn.execute("""
        SELECT id, ticker, headline, severity, tags, created_at
        FROM alerts
        WHERE rule = 'RULE_OSINT'
          AND datetime(created_at) >= datetime('now', '-14 days')
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()

    groups: dict = {}
    for row in rows:
        tags_raw = row["tags"] or ""
        region = None
        try:
            tags_obj = _json.loads(tags_raw)
            region = tags_obj.get("region")
        except Exception:
            for rname in REGION_COORDS:
                if rname.lower() in tags_raw.lower():
                    region = rname
                    break

        if not region:
            ticker = (row["ticker"] or "").replace("$", "").split()[0]
            for rname, tickers in REGION_TICKERS.items():
                if ticker in tickers:
                    region = rname
                    break

        if not region:
            region = "Middle East"

        if region not in groups:
            coords = REGION_COORDS.get(region, (31.0, 35.0))
            try:
                tags_obj = _json.loads(tags_raw)
                lat = float(tags_obj.get("lat") or coords[0])
                lng = float(tags_obj.get("lng") or coords[1])
            except Exception:
                lat, lng = coords
            groups[region] = {
                "region": region, "lat": lat, "lng": lng,
                "count": 0, "severity": "MEDIUM",
                "ticker_list": [], "last_at": None,
            }

        g = groups[region]
        g["count"] += 1
        sev_rank = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
        if sev_rank.get(row["severity"], 0) > sev_rank.get(g["severity"], 0):
            g["severity"] = row["severity"]
        ticker = (row["ticker"] or "").replace("$", "").split()[0]
        if ticker and ticker not in g["ticker_list"]:
            g["ticker_list"].append(ticker)
        if not g["last_at"] or row["created_at"] > g["last_at"]:
            g["last_at"] = row["created_at"]

    result = list(groups.values())

    # Globe must never be blank — show demo hotspots if no OSINT data yet
    if not result:
        now = _dt.now(_tz.utc).isoformat()
        result = [{**h, "last_at": now, "demo": True} for h in _DEMO_HOTSPOTS]

    return result


@app.get("/api/osint-summary", tags=["OSINT"])
def osint_summary(region: str):
    """Return cached or freshly generated AI summary for a region."""
    import json as _json
    from datetime import datetime, timezone
    from jpt_common import db_connection as _dbc
    conn = _dbc()

    cached = conn.execute(
        "SELECT summary, generated_at FROM region_summaries WHERE region = ?", (region,)
    ).fetchone()

    if cached:
        generated_at = cached["generated_at"] or ""
        try:
            gen_dt = datetime.fromisoformat(generated_at).replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 3600
        except Exception:
            age_hours = 999
        if age_hours < 6:
            conn.close()
            return {"region": region, "summary": cached["summary"], "cached": True}

    # Gather recent alerts for this region
    rows = conn.execute("""
        SELECT headline, severity, ticker, created_at
        FROM alerts
        WHERE rule = 'RULE_OSINT'
          AND datetime(created_at) >= datetime('now', '-14 days')
        ORDER BY created_at DESC
        LIMIT 10
    """).fetchall()

    signals_text = "\n".join(
        f"- [{r['severity']}] {r['ticker'] or ''}: {r['headline']}"
        for r in rows
    ) or "No recent signals."

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        conn.close()
        return {"region": region, "summary": "GROQ_API_KEY not configured.", "cached": False}

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        prompt = (
            f"You are a geopolitical intelligence analyst. Summarize the current situation in the {region} region "
            f"based on these recent signals from the Scope platform:\n\n{signals_text}\n\n"
            f"Write 3-4 sentences covering: the main threat or development, affected markets, and what to watch next. "
            f"Be concise and direct. Do not use bullet points."
        )
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            messages=[
                {"role": "system", "content": "You are a geopolitical intelligence analyst. Be concise and factual."},
                {"role": "user", "content": prompt},
            ],
        )
        summary = resp.choices[0].message.content.strip()
    except Exception as e:
        summary = f"Unable to generate summary: {e}"

    generated_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO region_summaries (region, summary, generated_at) VALUES (?, ?, ?)",
        (region, summary, generated_at),
    )
    conn.commit()
    conn.close()
    return {"region": region, "summary": summary, "cached": False}


@app.get("/api/conflict-news", tags=["OSINT"])
def conflict_news():
    """Return recent conflict headlines — from RULE_OSINT DB alerts, then static fallback."""
    import json as _json
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    rows = conn.execute("""
        SELECT headline, tags, created_at
        FROM alerts
        WHERE rule IN ('RULE_OSINT', 'RULE_ADSB', 'RULE_TELEGRAM_OSINT')
        ORDER BY created_at DESC
        LIMIT 30
    """).fetchall()
    conn.close()

    items = []
    for row in rows:
        tags_raw = row["tags"] or ""
        source = "OSINT"
        url = None
        try:
            tags_obj = _json.loads(tags_raw)
            source = tags_obj.get("source", "OSINT")
            url = tags_obj.get("source_url")
        except Exception:
            pass
        items.append({"title": row["headline"], "source": source, "url": url or "/feed?rule=RULE_OSINT"})

    # Static fallback so tape never shows "Loading…"
    if not items:
        items = [
            {"title": "Middle East tensions — monitoring GDELT + ADS-B feeds", "source": "Scope OSINT", "url": "/feed"},
            {"title": "Eastern Europe — tracking military movements via open-source signals", "source": "Scope OSINT", "url": "/feed"},
            {"title": "Taiwan Strait — semiconductor supply chain risk monitor active", "source": "Scope OSINT", "url": "/feed"},
            {"title": "OSINT pipeline active — run rule_osint.py to populate live signals", "source": "Scope", "url": "/feed"},
            {"title": "Korean Peninsula — monitoring via GDELT event classification", "source": "Scope OSINT", "url": "/feed"},
            {"title": "South China Sea — ADS-B military flight tracking enabled", "source": "Scope OSINT", "url": "/feed"},
        ]

    return items


@app.get("/api/member-funding/{bioguide_id}", tags=["Members"])
def member_funding(bioguide_id: str):
    """Return FEC funding profile for a member."""
    import json as _json
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    row = conn.execute("""
        SELECT mf.*, m.full_name
        FROM member_funding mf
        JOIN members m ON m.bioguide_id = mf.bioguide_id
        WHERE mf.bioguide_id = ?
    """, (bioguide_id,)).fetchone()
    conn.close()
    if not row:
        return {"bioguide_id": bioguide_id, "has_data": False}
    data = dict(row)
    # Parse JSON fields
    for field in ("top_industries", "pac_summary"):
        try:
            data[field] = _json.loads(data.get(field) or "[]")
        except Exception:
            data[field] = []
    data["has_data"] = True
    return data


@app.get("/api/signal-integrity/{bioguide_id}", tags=["Members"])
def member_signal_integrity(bioguide_id: str):
    """Return signal accuracy stats for a member's historical trades."""
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    row = conn.execute("""
        SELECT
            COUNT(*) as trade_count,
            AVG(b.return_30d) as avg_return_30d,
            SUM(CASE WHEN b.return_30d > 5 THEN 1 ELSE 0 END) as winning_trades,
            SUM(CASE WHEN b.return_30d IS NOT NULL THEN 1 ELSE 0 END) as backtested
        FROM transactions t
        LEFT JOIN alerts a ON a.ticker = t.raw_ticker_string AND a.member_id = t.member_id
        LEFT JOIN backtest_results b ON b.alert_id = a.id
        WHERE t.member_id = ?
    """, (bioguide_id,)).fetchone()
    conn.close()
    if not row or not row["backtested"]:
        return {"has_data": False}
    backtested   = row["backtested"] or 0
    winning      = row["winning_trades"] or 0
    win_rate     = round(winning / backtested * 100, 1) if backtested else 0
    avg_return   = round(row["avg_return_30d"] or 0, 1)
    return {
        "has_data":      True,
        "trade_count":   row["trade_count"],
        "backtested":    backtested,
        "winning_trades": winning,
        "win_rate":      win_rate,
        "avg_return_30d": avg_return,
    }


@app.get("/api/fara-activity", tags=["FARA"])
def fara_activity(country: str = "", limit: int = 20):
    """Return recent FARA filings, optionally filtered by country."""
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    if country:
        rows = conn.execute("""
            SELECT registrant, foreign_principal, country, period_start, total_receipts, issues_lobbied
            FROM fara_filings
            WHERE country LIKE ?
            ORDER BY total_receipts DESC LIMIT ?
        """, (f"%{country}%", limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT registrant, foreign_principal, country, period_start, total_receipts, issues_lobbied
            FROM fara_filings
            ORDER BY total_receipts DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/patent-activity", tags=["Patents"])
def patent_activity(ticker: str = "", category: str = ""):
    """Return recent patent filings, optionally filtered by ticker or category."""
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    if ticker:
        rows = conn.execute("""
            SELECT patent_number, patent_title, patent_date, assignee, category
            FROM patent_filings WHERE ticker = ?
            ORDER BY patent_date DESC LIMIT 20
        """, (ticker.upper(),)).fetchall()
    elif category:
        rows = conn.execute("""
            SELECT patent_number, patent_title, patent_date, assignee, ticker, category
            FROM patent_filings WHERE category = ?
            ORDER BY patent_date DESC LIMIT 20
        """, (category,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT patent_number, patent_title, patent_date, assignee, ticker, category
            FROM patent_filings ORDER BY patent_date DESC LIMIT 20
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/earnings-sentiment", tags=["Earnings"])
def earnings_sentiment(ticker: str):
    """Return political keyword density trend for a ticker's earnings calls."""
    import json as _json
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    rows = conn.execute("""
        SELECT filing_date, political_score, keyword_counts
        FROM earnings_sentiment WHERE ticker = ? AND political_score > 0
        ORDER BY filing_date DESC LIMIT 8
    """, (ticker.upper(),)).fetchall()
    conn.close()
    result = []
    for r in rows:
        counts = {}
        try:
            counts = _json.loads(r["keyword_counts"] or "{}")
        except Exception:
            pass
        result.append({
            "filing_date":    r["filing_date"],
            "political_score": round(r["political_score"], 2),
            "top_keywords":   sorted(counts.items(), key=lambda x: -x[1])[:5],
        })
    return result


@app.get("/api/heat-index", tags=["Sectors"])
def heat_index(days: int = 30):
    """Return heat score for every sector, sorted by score descending."""
    from jpt_common import db_connection as _dbc, SECTOR_MAP, calculate_heat_index
    conn = _dbc()
    results = []
    for sector in SECTOR_MAP:
        h = calculate_heat_index(sector, conn, days=days)
        h["sector"] = sector
        results.append(h)
    conn.close()
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


@app.get("/api/search", tags=["Search"])
def search(q: str = ""):
    """Live search across tickers, members, and alert headlines."""
    from jpt_common import db_connection as _dbc
    q = q.strip()
    if not q or len(q) < 2:
        return {"tickers": [], "members": [], "headlines": []}
    conn = _dbc()
    like = f"%{q}%"

    ticker_rows = conn.execute(
        """SELECT DISTINCT ticker FROM alerts
           WHERE ticker LIKE ? AND ticker != ''
           LIMIT 6""",
        (like,),
    ).fetchall()

    member_rows = conn.execute(
        """SELECT bioguide_id, full_name, state, party FROM members
           WHERE full_name LIKE ? LIMIT 6""",
        (like,),
    ).fetchall()

    headline_rows = conn.execute(
        """SELECT id, headline, ticker, severity, rule
           FROM alerts WHERE headline LIKE ?
           ORDER BY created_at DESC LIMIT 6""",
        (like,),
    ).fetchall()

    # Influence organizations (lobbying entities) — resolve aliases like AIPAC.
    orgs = []
    try:
        from jpt_common import resolve_org
        rec, conf, _by = resolve_org(q)
        seen = set()
        if rec:
            orgs.append({"name": rec["canonical"], "confidence": conf})
            seen.add(rec["canonical"].lower())
        for r in conn.execute(
            """SELECT client_name, SUM(amount) AS spend FROM lobbying_filings
               WHERE client_name LIKE ? GROUP BY client_name
               ORDER BY spend DESC LIMIT 5""",
            (like,),
        ).fetchall():
            nm = r["client_name"]
            if nm and nm.lower() not in seen:
                orgs.append({"name": nm, "spend": r["spend"] or 0})
                seen.add(nm.lower())
    except Exception:
        pass

    conn.close()
    return {
        "tickers":   [{"ticker": r["ticker"]} for r in ticker_rows],
        "members":   [dict(r) for r in member_rows],
        "headlines": [dict(r) for r in headline_rows],
        "orgs":      orgs[:6],
    }


@app.get("/api/osint-region-context", tags=["OSINT"])
def osint_region_context(region: str):
    """Return congressional trades and contracts for tickers linked to this region."""
    from jpt_common import db_connection as _dbc, REGION_TICKERS
    conn = _dbc()
    tickers = REGION_TICKERS.get(region, [])

    trades = []
    contracts = []

    if tickers:
        placeholders = ",".join("?" * len(tickers))
        trade_rows = conn.execute(f"""
            SELECT t.raw_ticker_string AS ticker, m.full_name, t.transaction_type, t.filing_date
            FROM transactions t
            JOIN members m ON t.member_id = m.bioguide_id
            WHERE t.raw_ticker_string IN ({placeholders})
              AND t.filing_date >= date('now', '-90 days')
            ORDER BY t.filing_date DESC
            LIMIT 20
        """, tickers).fetchall()
        trades = [dict(r) for r in trade_rows]

        contract_rows = conn.execute(f"""
            SELECT recipient, amount, agency, period_of_performance_start
            FROM contracts
            WHERE ticker IN ({placeholders})
              AND period_of_performance_start >= date('now', '-90 days')
            ORDER BY period_of_performance_start DESC
            LIMIT 20
        """, tickers).fetchall()
        contracts = [dict(r) for r in contract_rows]

    conn.close()
    return {"region": region, "tickers": tickers, "trades": trades, "contracts": contracts}
