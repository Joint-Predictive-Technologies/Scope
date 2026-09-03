"""OSINT-Graph convergence map — a STANDALONE service.

    uvicorn app:app --host 0.0.0.0 --port $PORT

🔴 WHY THIS EXISTS AS ITS OWN PROCESS, MEASURED RATHER THAN PREFERRED.  The map
used to be mounted into Scope's own FastAPI app.  Two optimisation passes tried to
make that safe and both failed, and the second made it worse: a hostile caller is
a closed loop, so a 27x-cheaper request just lets it issue more, and host-page
degradation went 6.6x -> 22.8x at 8 clients and 13.9x -> 137x at 30.  Per-request
cost was never what the gate measured — the map and the pages it must not starve
were sharing one Python process, one GIL, one request queue.

Separate processes was the only tested change that worked (~108x -> 1.2x on one
box).  On Railway each service runs in its own cgroup: the Scope container reports
`cpu.max = 800000 100000`, an 8 vCPU quota the kernel enforces, so this service
cannot eat Scope's entitlement no matter what it does.

────────────────────────────────────────────────────────────────────────────────
--- what it serves ---

    GET  /                          the map page
    GET  /out/map-v1/{path}         the static export (54 JSON files)
    GET  /api/graph/{entity_id}     one bounded entity neighbourhood
    GET  /theme.js                  a no-op shim, see below
    GET  /healthz                   liveness, and it reports whether the DB is there

⭐ THE PAGE IS SERVED BY THIS SERVICE, NOT BY SCOPE, AND THAT IS THE WHOLE REASON
THE FRONTEND NEEDS NO EDIT.  `osint_map.html` asks for `out/map-v1/...` and
`api/graph/...` RELATIVE to itself.  Served at this origin's root those resolve
onto the two routes below, so the file stays BYTE-IDENTICAL to
`serving/osint-map-v1.html` in the osint-graph repo — one file, no second copy to
drift.  It also makes every fetch same-origin, so CORS is not load-bearing for the
map's own operation at all.

--- the database ---

🔴 NOT `jpt.db`, AND NOT `osint.db` EITHER.  A purpose-built read-only snapshot
(`$OSINT_DB`, built by `serving/build_serving_snapshot.py`): the ten tables the
serving code reads, `events` pruned to the two types it reads, plus four covering
indexes.  This process has no code path to Scope's database and no writable
handle — `mode=ro` is re-asserted on every per-thread connection inside
`graph_api`.

--- when the snapshot is absent ---

⚠️ A CONTAINER CAN COME UP WITHOUT IT.  The map planes are static files and keep
working; only the graph plane needs the database, so a missing snapshot returns
**503 with a reason** and the page renders that honestly rather than substituting
a fabricated neighbourhood.  It does not take the page down.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
DATA = STATIC / "out" / "map-v1"

# the vendored serving code, in the layout its own repo uses — `export_map.py`
# derives its sibling `loader/` path from `__file__`, so the shape is load-bearing
sys.path.insert(0, str(HERE / "serving"))

from rate_limit import rate_limit                     # noqa: E402

app = FastAPI(title="OSINT-Graph map", docs_url=None, redoc_url=None,
              openapi_url=None)

# ⚠️ THE WILDCARD IS DELIBERATE AND WAS SIGNED OFF, and it carries over from the
# mounted version unchanged.  These endpoints are public, unauthenticated and
# cookie-free; `allow_credentials` MUST stay False, because a wildcard origin
# combined with credentials makes Starlette reflect the caller's actual Origin,
# which quietly becomes "any origin, WITH credentials".  Nothing here sets a
# cookie.  ⭐ And since the page is served from this same origin, CORS is not what
# makes the map work — it only lets other readers consume the JSON.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# 🔴 THE SAME LIMITS THE MOUNTED VERSION CARRIED, and the same route-template
# keying fix — `rate_limit.py` is vendored here rather than imported from Scope,
# because this service must not depend on Scope's code to be safe.  ⚠️ The fix
# also STAYS in Scope: `/tickers/watchlist/{symbol}` is a live parameterised
# rate-limited route there and the fix is what bounds it.
# ⚠️ CONFIGURABLE SO THE RESOURCE-ISOLATION GATE CAN ACTUALLY BE RUN, and the
# reason is a finding in itself: Railway's edge NORMALISES `X-Forwarded-For`, so a
# forged header never reaches the app and one source IP is held to 30/60 no matter
# what it claims to be. That is good news for the limiter's documented weakness —
# and it makes the hostile load the deploy gate requires impossible to generate
# from one machine. Raise these for a measurement window, then put them back.
# 🔴 The DEFAULTS are the real policy; an env var is an operator action, not a
# setting anyone should leave changed.
GRAPH_LIMIT = rate_limit(int(os.environ.get("MAP_RATE_GRAPH", "30")),
                         int(os.environ.get("MAP_RATE_WINDOW", "60")))
DATA_LIMIT = rate_limit(int(os.environ.get("MAP_RATE_DATA", "90")),
                        int(os.environ.get("MAP_RATE_WINDOW", "60")))

_api = None
_api_error: str | None = None
_api_error_at: float = 0.0
_api_lock = threading.Lock()

# How long a failed open is allowed to stand before the next request retries it.
# 🔴 The point of this is a container that starts BEFORE its volume is mounted:
# the snapshot is absent for a few seconds and then appears.  Without a retry the
# process caches "not on this machine" for its whole life and serves 503 on the
# graph plane until a human notices and restarts it.
_RETRY_AFTER = float(os.environ.get("MAP_GRAPH_RETRY_SECONDS", "30"))


def _db_path() -> str:
    return os.environ.get("OSINT_DB") or "/app/data/osint-map-serving.db"


def _graph():
    """The read-only handle, opened once and reused across threads.

    The cache is ON here and off in development: a neighbourhood cache is only
    sound against a database that does not change under it, and this process reads
    a static snapshot replaced as a whole file.

    🔴 A FAILED OPEN IS NOT PERMANENT.  This used to short-circuit on
    `_api_error is not None` forever, so a container that lost the race with its
    own volume mount served a broken graph plane for the rest of its life while
    `/healthz` reported the self-contradictory `db_present: true, graph: false`.
    A failure is now retried after `_RETRY_AFTER` seconds, which is bounded enough
    that a genuinely missing snapshot does not turn every request into a fresh
    `stat` + connect, and short enough that a volume arriving late heals itself
    without anyone being paged.

    Success is latched under a lock and read without one, so the healthy path —
    every request after the first — stays a plain attribute check."""
    global _api, _api_error, _api_error_at
    if _api is not None:                    # healthy: no lock, no clock
        return _api
    with _api_lock:
        if _api is not None:                # another thread won the race
            return _api
        if _api_error is not None and (time.monotonic() - _api_error_at) < _RETRY_AFTER:
            return None                     # still inside the cooldown
        try:
            import graph_api                          # noqa: PLC0415
            db = _db_path()
            if not os.path.exists(db):
                _api_error = f"the serving snapshot is not on this machine ({db})"
                _api_error_at = time.monotonic()
                return None
            _api = graph_api.GraphAPI(db, cache_size=512)
            # cleared only on success, so `/healthz` cannot report a stale reason
            # next to a working graph plane
            _api_error = None
        except Exception as exc:                      # noqa: BLE001
            _api_error = f"{type(exc).__name__}: {exc}"
            _api_error_at = time.monotonic()
        return _api


@app.get("/", include_in_schema=False)
def page():
    return FileResponse(STATIC / "osint_map.html", media_type="text/html")


@app.get("/theme.js", include_in_schema=False)
def theme_js():
    """A no-op shim so `osint_map.html` stays byte-identical to its source.

    The page declares `data-theme-lock` and loads `/theme.js` because Scope's page
    contract requires every page to load it synchronously — a rule that exists so
    a light-theme user never sees a flash of dark.  That asset belongs to Scope;
    here it would 404 on every load.  Two lines is a cheaper price than a second,
    divergent copy of a 1,500-line page."""
    return PlainTextResponse(
        "/* no-op: the theme lock is declared in the page itself */\n",
        media_type="application/javascript")


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness — and it says whether the graph plane is actually available, so a
    container that came up without its snapshot is visible rather than silently
    half-working."""
    db = _db_path()
    return {"ok": True, "db": db, "db_present": os.path.exists(db),
            "graph": _graph() is not None, "detail": _api_error}


@app.get("/out/map-v1/{path:path}", include_in_schema=False,
         dependencies=[Depends(DATA_LIMIT)])
def data(path: str):
    """🔴 The path is resolved and then CONFINED: a request for `../../secrets`
    must not become a file read outside this directory, and `Path.resolve()` plus
    an explicit ancestry check is what makes that true rather than hoped."""
    target = (DATA / path).resolve()
    if not target.is_file() or DATA.resolve() not in target.parents:
        raise HTTPException(status_code=404, detail="not found")
    if target.suffix != ".json":
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target, media_type="application/json")


@app.get("/api/graph/{entity_id}", include_in_schema=False,
         dependencies=[Depends(GRAPH_LIMIT)])
def graph(entity_id: str, k: int = 40):
    """`k` is clamped inside `graph_api.neighborhood()` to its own `MAX_K`, so
    this route cannot widen it and neither can a caller."""
    g = _graph()
    if g is None:
        # 🔴 503 AND A REASON, NEVER AN EMPTY GRAPH.  "No connections" and "the
        # database is not here" are different facts and the UI must not be able to
        # confuse them.
        return JSONResponse(
            status_code=503,
            content={"error": "graph_unavailable", "detail": _api_error,
                     "note": "The map planes are static files and are unaffected. "
                             "Nothing is substituted for the graph plane."})
    try:
        r = g.neighborhood(entity_id, k=k)
    except Exception as exc:                          # noqa: BLE001
        raise HTTPException(status_code=500, detail=type(exc).__name__) from exc
    if r is None:
        raise HTTPException(status_code=404, detail="unknown_entity")
    return r
