"""
A minimal in-memory rate limiter — no new dependency, because none of the
endpoints that need one run in more than a single Railway container today, and
a real distributed limiter (Redis-backed) would be scope creep for that.

Nothing in this codebase imposed any cap on how often a caller could hit any
endpoint before this — most concretely, `/chat` (api/routers/chat.py) makes a
real, billed Groq API call per request with zero limit of Scope's own, and
`/admin/refresh` / `/admin/upload-db` (api/main.py) had no backoff on repeated
wrong-key guesses.

Caveat, stated plainly rather than assumed away: the key is derived from
`X-Forwarded-For` (Railway's edge sets it; falls back to the raw connection IP
otherwise), which a caller could in principle forge to spread requests across
many apparent "clients" and evade the limit — this stops casual/accidental
abuse and simple scripted hammering, not a determined, spoofing-aware attacker.
That is judged an acceptable bound for a single-container deployment with no
other IP-trust infrastructure; tightening it further (e.g. trusting only
Railway's actual edge IP) would need real platform-specific verification this
module doesn't have.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

# "route-template:ip" -> deque of request timestamps within the current window.
_buckets: dict[str, deque] = defaultdict(deque)

# `{name:convertor}` -> `{name}`, so a template rebuilt from a request compares
# equal to the one the router declares.
_CONVERTOR = re.compile(r"\{([^:}]+)(?::[^}]+)?\}")


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _route_template(request: Request) -> str:
    """The ROUTE the request matched, not the path it arrived on.

    🔴 THE BUG THIS EXISTS TO FIX.  The key was `request.url.path`, so a route
    with a path parameter got one bucket PER PARAMETER VALUE.  Measured on
    `/osint-map/api/graph/{entity_id}`: a single IP rotating 40 entities took
    **1,200 requests before its first 429** — 40 buckets x 30 — where the
    configured limit is 30.  With 236,420 entities in that graph the limit was
    effectively unbounded.  `/tickers/watchlist/{symbol}` has the same shape and
    the same gap, against the ticker universe.

    ⚠️ AND THE OBVIOUS FIX IS WRONG.  `request.scope["route"].path` looks like the
    answer and is not: it is the route's path WITHIN ITS ROUTER, with the include
    prefix stripped.  Measured on this app — `/chat` reports `''`, and
    `/tickers/watchlist/AAPL` reports `/watchlist/{symbol}`.  Keying on that would
    merge `/chat` with every other router's root route into ONE bucket, which is a
    far worse failure than the one being fixed.

    So the template is rebuilt from the resolved path by putting each matched
    parameter back:

        /tickers/watchlist/AAPL          {symbol: AAPL}   -> /tickers/watchlist/{symbol}
        /osint-map/api/graph/<uuid>      {entity_id: ...} -> /osint-map/api/graph/{entity_id}
        /admin/refresh                   {}               -> /admin/refresh   (unchanged)

    ⭐ A ROUTE WITH NO PATH PARAMETER RETURNS EARLY AND IS BYTE-IDENTICAL TO THE
    OLD KEY, by construction rather than by testing — which is what keeps `/chat`,
    both admin routes and `/api/watchlist-rules` exactly as they are today.

    Values are substituted rightmost-first and longest-first so a value that also
    occurs earlier in the path cannot be replaced in the wrong place, and the
    result is cross-checked against the router's own declared suffix.  If anything
    does not line up the function returns the resolved path — the behaviour before
    this change — because failing back to a limit that is too loose is bad, and
    failing into a bucket shared with an unrelated route is worse.
    """
    path = request.url.path
    params = request.scope.get("path_params") or {}
    if not params:
        return path

    # ⭐ PRIMARY: ask the ROUTER what the template is, then find where it starts.
    # `route.path` is the template MINUS the include prefix, so rendering it with
    # the request's own parameter values reproduces the tail of the resolved path
    # exactly — and whatever precedes that tail is the prefix.  This is positional
    # by construction, so it is right even when two parameters share a value.
    suffix = getattr(request.scope.get("route"), "path", None)
    if suffix:
        clean = _CONVERTOR.sub(r"{\1}", suffix)
        try:
            rendered = clean.format(**{k: str(v) for k, v in params.items()})
        except (KeyError, IndexError, ValueError):
            rendered = None
        if rendered and path.endswith(rendered):
            return path[: len(path) - len(rendered)] + clean

    # FALLBACK: no usable route object (a differently-wired framework version, a
    # sub-application).  Put each value back rightmost-first and longest-first so a
    # value that also occurs in a static segment cannot rewrite the wrong one.
    # ⚠️ This cannot tell two parameters apart when their values are equal, so the
    # result is cross-checked and abandoned if it does not line up.
    tmpl = path
    for name, value in sorted(params.items(), key=lambda kv: -len(str(kv[1]))):
        v = str(value)
        if not v:
            continue
        i = tmpl.rfind(v)
        if i == -1:
            return path
        tmpl = tmpl[:i] + "{" + name + "}" + tmpl[i + len(v):]
    if suffix and not tmpl.endswith(_CONVERTOR.sub(r"{\1}", suffix)):
        return path
    return tmpl


def rate_limit(max_requests: int, window_seconds: int):
    """FastAPI dependency factory: `Depends(rate_limit(10, 60))` allows at most
    10 requests per client IP per rolling 60s window on the ROUTE it's attached
    to. Raises 429 over the limit; never blocks a request that's under it.

    ⚠️ "on the route" is load-bearing and used to be false — see
    `_route_template`. A route with a path parameter was limited per parameter
    value, which on `/osint-map/api/graph/{entity_id}` meant 30 per entity rather
    than 30 per caller."""
    def _dep(request: Request) -> None:
        key = f"{_route_template(request)}:{_client_ip(request)}"
        now = time.monotonic()
        bucket = _buckets[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_requests:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded — max {max_requests} requests per {window_seconds}s.",
            )
        bucket.append(now)
    return _dep
