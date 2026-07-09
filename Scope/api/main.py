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
)

STATIC_DIR = Path(__file__).parent / "static"
CODE_DIR   = Path(__file__).resolve().parent.parent

LIVE_RULES = [
    "rule_06_form4.py",
    "rule_07_polymarket.py",
    "rule_08_federal_register.py",
    "rule_09_lobbying.py",
    "scripts/rule_01b_first_touch.py",
    "scripts/rule_anomaly.py",
    "scripts/rule_10_corroboration.py",
    "scripts/rule_11_contracts.py",
    "scripts/rule_12_fara.py",
    "scripts/rule_13_fec.py",
    "scripts/rule_14_patents.py",
    "scripts/rule_15_earnings_nlp.py",
    "scripts/rule_reddit.py",
    "scripts/rule_osint.py",
    "scripts/rule_adsb.py",
    "scripts/rule_telegram_osint.py",
    "scripts/rule_options_correlation.py",
    "scripts/telegram_bot.py",
]
REFRESH_INTERVAL_HOURS = 4

# ── Per-rule cadences (minutes) ────────────────────────────────────────────────
_RULE_SCHEDULE = {
    # every 15 min — fast sources
    "scripts/rule_osint.py":              15,
    "scripts/rule_adsb.py":               5,
    "scripts/rule_options_correlation.py": 15,
    "rule_07_polymarket.py":              15,
    # every 60 min — medium frequency
    "rule_06_form4.py":                   60,
    "scripts/rule_reddit.py":             60,
    "scripts/rule_11_contracts.py":       60,
    "scripts/rule_anomaly.py":            60,
    "scripts/rule_telegram_osint.py":     60,
    "scripts/rule_10_corroboration.py":   60,
    "scripts/rule_01b_first_touch.py":    60,
    # every 6 hours — slower sources
    "rule_08_federal_register.py":        360,
    "rule_09_lobbying.py":                360,
    "scripts/rule_12_fara.py":            360,
    "scripts/rule_13_fec.py":             1440,  # daily
    "scripts/rule_14_patents.py":         1440,
    "scripts/rule_15_earnings_nlp.py":    1440,
    "scripts/telegram_bot.py":            60,
}


def _alert_count() -> int:
    import sqlite3
    default_db = CODE_DIR / "data" / "jpt.db"
    db_path = os.getenv("DATABASE_PATH") or str(default_db)
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
    default_db = CODE_DIR / "data" / "jpt.db"
    db_path = os.getenv("DATABASE_PATH") or str(default_db)
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


def _run_rule(rule: str) -> str:
    """Run a single rule script; return 'ok' or the last 300 chars of stderr."""
    try:
        r = subprocess.run(
            [sys.executable, rule, "--emit-alerts"],
            capture_output=True, text=True, timeout=300,
            cwd=str(CODE_DIR),
        )
        result = "ok" if r.returncode == 0 else r.stderr[-300:].strip()
    except subprocess.TimeoutExpired:
        result = "timeout after 300s"
    except Exception as e:
        result = str(e)[:200]
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
    except ImportError:
        print("[scheduler] apscheduler not installed — falling back to single loop", flush=True)
        return

    _scheduler = BackgroundScheduler(daemon=True)

    for rule, minutes in _RULE_SCHEDULE.items():
        def _make_job(r=rule):
            def _job():
                _job_last_run[r] = "running"
                _job_last_run[r] = _run_rule(r)
            return _job

        _scheduler.add_job(
            _make_job(),
            IntervalTrigger(minutes=minutes),
            id=rule.replace("/", "_").replace(".", "_"),
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=60,
        )

    _scheduler.start()
    print(f"[scheduler] APScheduler started — {len(_RULE_SCHEDULE)} jobs scheduled", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    hours_stale = _hours_since_last_alert()
    if hours_stale >= REFRESH_INTERVAL_HOURS:
        print(f"[startup] data is {hours_stale:.1f}h old — running all rules now …", flush=True)
        asyncio.create_task(asyncio.to_thread(_run_rules, LIVE_RULES))
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
app.include_router(digest.router,        prefix="/digest",    tags=["Digest"])
app.include_router(brief.router,         prefix="/brief",     tags=["Brief"])
app.include_router(contracts.router,     prefix="/contracts", tags=["Contracts"])
app.include_router(congress.router,      prefix="/congress",  tags=["Congress"])
app.include_router(history.router,       prefix="/history",   tags=["History"])


@app.get("/health", tags=["Health"])
def health():
    import sqlite3
    from pathlib import Path
    default_db = Path(__file__).resolve().parent.parent / "data" / "jpt.db"
    db_path = os.getenv("DATABASE_PATH") or str(default_db)
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
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id":       job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "last_result": _job_last_run.get(
                next((r for r in _RULE_SCHEDULE if r.replace("/","_").replace(".","_") == job.id), ""),
                "pending"
            ),
            "interval_minutes": _RULE_SCHEDULE.get(
                next((r for r in _RULE_SCHEDULE if r.replace("/","_").replace(".","_") == job.id), ""),
                None
            ),
        })
    return {
        "status":    "running" if _scheduler.running else "stopped",
        "job_count": len(jobs),
        "jobs":      sorted(jobs, key=lambda j: j["interval_minutes"] or 9999),
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
    finally:
        conn.close()
    return {
        "corr_count":    corr_count,
        "ticker_count":  ticker_count,
        "member_count":  member_count,
        "alert_count":   alert_count,
        "crit_count":    crit_count,
        "week_tickers":  week_tickers,
    }


@app.get("/api/activity", tags=["Meta"])
def api_activity():
    """Daily alert counts by rule for the last 14 days — used for the signal heat map."""
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    rows = conn.execute("""
        SELECT date(created_at) as day, rule, COUNT(*) as count
        FROM alerts
        WHERE datetime(created_at) >= datetime('now', '-14 days')
        GROUP BY day, rule
        ORDER BY day, rule
    """).fetchall()
    conn.close()
    result: dict = {}
    for row in rows:
        day = row["day"]
        if day not in result:
            result[day] = {}
        result[day][row["rule"]] = row["count"]
    return result


@app.get("/api/ticker-tape", tags=["Meta"])
def ticker_tape():
    """Live signal items for the scrolling ticker tape."""
    from jpt_common import db_connection as _dbc
    conn = _dbc()
    rows = conn.execute("""
        SELECT rule, ticker, headline, severity
        FROM alerts
        WHERE severity IN ('HIGH', 'CRITICAL')
          AND rule NOT IN ('RULE_ANOMALY', 'RULE_11')
          AND datetime(created_at) >= datetime('now', '-7 days')
        ORDER BY datetime(created_at) DESC
        LIMIT 20
    """).fetchall()
    conn.close()

    items = []
    for r in rows:
        rule    = r["rule"] or ""
        ticker  = (r["ticker"] or "").replace("$", "").split(" ")[0][:6]
        headline = (r["headline"] or "")
        label   = TAPE_RULE_LABELS.get(rule, rule)
        icon    = TAPE_RULE_ICON.get(rule, "●")
        # Build short display text
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
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/feed", response_class=HTMLResponse, include_in_schema=False)
def feed():
    return FileResponse(STATIC_DIR / "alerts.html")

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

@app.get("/digest", response_class=HTMLResponse, include_in_schema=False)
def digest_page():
    return FileResponse(STATIC_DIR / "digest.html")

@app.get("/brief", response_class=HTMLResponse, include_in_schema=False)
def brief_page():
    return FileResponse(STATIC_DIR / "brief.html")

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
    {"region": "Middle East",      "lat": 29.5,  "lng": 45.0,  "severity": "HIGH",   "count": 1, "ticker_list": ["USO", "XLE"], "last_at": None},
    {"region": "Eastern Europe",   "lat": 49.0,  "lng": 31.0,  "severity": "HIGH",   "count": 1, "ticker_list": ["LMT", "RTX"], "last_at": None},
    {"region": "Taiwan Strait",    "lat": 24.0,  "lng": 121.0, "severity": "MEDIUM", "count": 1, "ticker_list": ["TSM", "NVDA"], "last_at": None},
    {"region": "Korean Peninsula", "lat": 37.5,  "lng": 127.5, "severity": "MEDIUM", "count": 1, "ticker_list": ["LMT"], "last_at": None},
    {"region": "South Asia",       "lat": 28.0,  "lng": 77.0,  "severity": "MEDIUM", "count": 1, "ticker_list": ["LMT", "RTX"], "last_at": None},
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
