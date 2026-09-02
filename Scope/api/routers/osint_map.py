"""OSINT-Graph convergence map — the alpha surface, mounted into Scope's app.

Four routes, all read-only, all additive.  Nothing here touches `jpt.db`, Scope's
own routes, or anything resembling an admin surface.

    GET  /osint-map/                          the page
    GET  /osint-map/out/map-v1/{path}         the static export (54 JSON files)
    GET  /osint-map/api/graph/{entity_id}     one bounded entity neighbourhood

────────────────────────────────────────────────────────────────────────────────
--- why the trailing slash, and why these exact paths ---

`osint_map.html` is BYTE-IDENTICAL to `serving/osint-map-v1.html` in the
osint-graph repo, and it asks for `out/map-v1/...` and `api/graph/...` RELATIVE to
itself.  Mounting the page at `/osint-map/` makes those relative paths resolve to
the two routes below with **no edit to the page**.  The alternative — rewriting
two constants in a copied 1,500-line file — creates a second divergent copy of a
page whose whole purpose is not saying two different things about the same data.
So `/osint-map` redirects to `/osint-map/`, and the page is never served from a
URL where its own fetches would break.

--- the database ---

🔴 NOT `jpt.db`, AND NOT `osint.db` EITHER.  This reads a purpose-built read-only
SNAPSHOT (`$OSINT_DB`, built by the vendored `build_serving_snapshot.py`): the ten
tables the serving code references, with `events` pruned to the two types it reads.
Separate file, separate handle, `mode=ro` enforced inside `graph_api`.  There is no
code path from this router to `jpt.db` and none to a writable handle.

--- when the snapshot is absent ---

⚠️ A CONTAINER CAN COME UP WITHOUT IT — a fresh volume, a remount, a snapshot not
yet uploaded.  The map planes are static files and keep working; only the graph
plane needs the database.  So a missing snapshot returns **503 with a reason**, and
the page already renders that honestly ("The entity graph is served by ... which is
not answering. Nothing is substituted for it — a fabricated neighbourhood is
exactly what this surface exists to not show").  It does NOT fall back to anything,
and it does NOT take the page down.

--- what is deliberately NOT here ---

* No write route of any kind, no upload, no admin, no key check — there is nothing
  to authorise because there is nothing to change.
* No auth or access-gating beyond per-IP rate limiting.  Named rather than
  implied: this is a public alpha, the data is public-source, and gating it was
  not in scope for this pass.
* No CORS block of its own.  The app already sets `allow_origins=["*"]` with
  `allow_credentials=False` app-wide, deliberately and documented in `main.py`;
  these endpoints are public, unauthenticated and cookie-free like the rest of the
  API, so narrowing origins here alone would protect nothing and would leave the
  app saying two different things. See the session log for the reasoning.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from api.rate_limit import rate_limit

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAP_HTML = STATIC_DIR / "osint_map.html"
MAP_DATA = STATIC_DIR / "osint-map"

# The vendored serving code, imported the way its own repo lays it out — see
# api/osint_map/VENDORED.json for why the directory shape is load-bearing.
_VENDOR = Path(__file__).resolve().parent.parent / "osint_map" / "serving"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

# 🔴 RATE LIMITS, CHOSEN AGAINST SCOPE'S OWN PRECEDENT, NOT INVENTED.
# `/chat` is 10/60 (it makes a billed API call), the admin routes are 5/60, and
# `/tickers/watchlist` is 20/60. A graph expansion is a read with no external
# cost, and a person tracing a web clicks steadily — 30/60 leaves real use
# untouched while bounding one IP to ~30 x the worst-case query per minute.
# The static export is cheaper per request but larger (national.json is 380 KB),
# so it gets a higher count and is limited for bandwidth rather than CPU.
GRAPH_LIMIT = rate_limit(30, 60)
DATA_LIMIT = rate_limit(90, 60)

_api = None
_api_error: str | None = None


def _graph():
    """The read-only handle, opened once and reused.

    ⚠️ THE CACHE IS ON HERE AND OFF IN DEVELOPMENT, and that difference is the
    point: a neighbourhood cache is only sound against a database that does not
    change under it. This process reads a static snapshot that is replaced as a
    whole file, so it holds; a dev box reads the live `osint.db`, which other
    sessions write, so it does not.
    """
    global _api, _api_error
    if _api is not None or _api_error is not None:
        return _api
    try:
        import graph_api                      # noqa: PLC0415 — vendored, path-injected
        db = graph_api.DEFAULT_DB
        if not os.path.exists(db):
            _api_error = f"the serving snapshot is not on this machine ({db})"
            return None
        _api = graph_api.GraphAPI(db, cache_size=512)
    except Exception as exc:                  # noqa: BLE001
        _api_error = f"{type(exc).__name__}: {exc}"
    return _api


@router.get("", include_in_schema=False)
def osint_map_redirect():
    """`/osint-map` -> `/osint-map/`. Without the slash the page's own relative
    fetches would resolve one level up and 404 — see the module docstring."""
    return RedirectResponse(url="/osint-map/", status_code=307)


@router.get("/", include_in_schema=False)
def osint_map_page():
    return FileResponse(MAP_HTML, media_type="text/html")


@router.get("/out/map-v1/{path:path}", include_in_schema=False,
            dependencies=[Depends(DATA_LIMIT)])
def osint_map_data(path: str):
    """The static export. 🔴 The path is resolved and then CONFINED: a request
    for `../../jpt.db` must not become a file read outside this directory, and
    `Path.resolve()` plus an explicit ancestry check is what makes that true
    rather than hoped."""
    target = (MAP_DATA / path).resolve()
    if not target.is_file() or MAP_DATA.resolve() not in target.parents:
        raise HTTPException(status_code=404, detail="not found")
    if target.suffix != ".json":
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target, media_type="application/json")


@router.get("/api/graph/{entity_id}", include_in_schema=False,
            dependencies=[Depends(GRAPH_LIMIT)])
def osint_map_graph(entity_id: str, k: int = 40):
    """One bounded neighbourhood.

    `k` is clamped inside `graph_api.neighborhood()` to its own `MAX_K`, so this
    route cannot widen it and neither can a caller."""
    g = _graph()
    if g is None:
        # 🔴 503 AND A REASON, NEVER AN EMPTY GRAPH. "No connections" and "the
        # database is not here" are different facts and the UI must not be able
        # to confuse them — the same rule the 404 below encodes.
        return JSONResponse(
            status_code=503,
            content={"error": "graph_unavailable", "detail": _api_error,
                     "note": "The map planes are static files and are unaffected. "
                             "Nothing is substituted for the graph plane."})
    try:
        r = g.neighborhood(entity_id, k=k)
    except Exception as exc:                  # noqa: BLE001
        raise HTTPException(status_code=500, detail=type(exc).__name__) from exc
    if r is None:
        raise HTTPException(status_code=404, detail="unknown_entity")
    return r
