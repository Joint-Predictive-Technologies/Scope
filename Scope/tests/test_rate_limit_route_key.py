#!/usr/bin/env python3
"""The rate limiter must bound a ROUTE, not a path.

🔴 THE DEFECT THIS FILE EXISTS FOR, AND IT SHIPPED.  `api/rate_limit.py` keyed its
bucket on `request.url.path`, so any route with a path parameter got one bucket
PER PARAMETER VALUE.  Measured on the live app: a single IP rotating 40 entities
against `/osint-map/api/graph/{entity_id}` took **1,200 requests before its first
429**, against a configured limit of 30. Two other routes had the same shape —
`/tickers/watchlist/{symbol}`, live and pre-existing, and the map's static export.

⚠️ AND THE LIMITER HAD NO TEST AT ALL until this file, which is how a security
control protecting `/chat` (a billed API call), both admin routes and the map went
years without anyone noticing it was inert on a third of the routes using it.

The three properties below are what "bounded" actually means here.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, Depends                      # noqa: E402
from fastapi.testclient import TestClient                 # noqa: E402

from api.rate_limit import rate_limit, _buckets, _route_template   # noqa: E402


@pytest.fixture(autouse=True)
def _clean_buckets():
    """`_buckets` is module-global and shared; a leaked bucket makes the next
    test's threshold arrive early and look like a defect that is not there."""
    _buckets.clear()
    yield
    _buckets.clear()


def _app():
    app = FastAPI()

    @app.get("/plain", dependencies=[Depends(rate_limit(3, 60))])
    def plain():
        return {"ok": True}

    @app.get("/thing/{thing_id}", dependencies=[Depends(rate_limit(3, 60))])
    def thing(thing_id: str):
        return {"id": thing_id}

    @app.get("/blob/{path:path}", dependencies=[Depends(rate_limit(3, 60))])
    def blob(path: str):
        return {"path": path}

    return TestClient(app)


def _first_429(client, urls, cap=60):
    for i, u in enumerate(urls, 1):
        if client.get(u, headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 429:
            return i
        if i >= cap:
            return None
    return None


def test_A_ROUTE_WITH_A_PARAMETER_IS_LIMITED_ONCE_NOT_ONCE_PER_VALUE():
    """🔴 THE BUG. Rotating the parameter must not multiply the budget."""
    c = _app()
    rotating = [f"/thing/{i}" for i in range(60)]
    assert _first_429(c, rotating) == 4, "3/60 means the 4th request is refused"

    _buckets.clear()
    # ...and a `:path` parameter, which spans several segments, behaves the same
    c2 = _app()
    assert _first_429(c2, [f"/blob/a/b/{i}.json" for i in range(60)]) == 4


def test_A_ROUTE_WITH_NO_PARAMETER_KEEPS_EXACTLY_ITS_OLD_KEY():
    """⭐ THE PROPERTY THAT PROTECTS EVERY EXISTING ROUTE.  `/chat`, both admin
    routes and `/api/watchlist-rules` have no path parameter, so the template IS
    the resolved path and their keys are byte-identical to before the fix — by
    construction, not by testing each one."""
    c = _app()
    assert _first_429(c, ["/plain"] * 60) == 4

    class _Scope(dict):
        pass

    class _Req:
        def __init__(self, path, params, route_path=None):
            self.url = type("U", (), {"path": path})()
            self.scope = {"path_params": params,
                          "route": type("R", (), {"path": route_path})() if route_path else None}

    assert _route_template(_Req("/admin/refresh", {})) == "/admin/refresh"
    assert _route_template(_Req("/chat", {})) == "/chat"
    assert _route_template(_Req("/api/watchlist-rules", {})) == "/api/watchlist-rules"


def test_THE_TEMPLATE_KEEPS_THE_ROUTER_PREFIX():
    """🔴 THE OBVIOUS FIX WOULD HAVE BEEN A WORSE BUG.
    `request.scope["route"].path` is the route's path WITHIN ITS ROUTER — measured
    on this app, `/chat` reports `''` and `/tickers/watchlist/AAPL` reports
    `/watchlist/{symbol}`. Keying on that would merge `/chat` into one bucket with
    every other router's root route. The template must carry the full prefix."""
    class _Req:
        def __init__(self, path, params, route_path):
            self.url = type("U", (), {"path": path})()
            self.scope = {"path_params": params,
                          "route": type("R", (), {"path": route_path})()}

    assert _route_template(
        _Req("/tickers/watchlist/AAPL", {"symbol": "AAPL"}, "/watchlist/{symbol}")
    ) == "/tickers/watchlist/{symbol}"
    assert _route_template(
        _Req("/osint-map/api/graph/abc-123", {"entity_id": "abc-123"},
             "/api/graph/{entity_id}")
    ) == "/osint-map/api/graph/{entity_id}"
    # a `:path` convertor still lines up against the router's declared suffix
    assert _route_template(
        _Req("/osint-map/out/map-v1/county/04.json", {"path": "county/04.json"},
             "/out/map-v1/{path:path}")
    ) == "/osint-map/out/map-v1/{path}"


def test_AN_UNREBUILDABLE_TEMPLATE_FAILS_BACK_TO_TODAYS_BEHAVIOUR():
    """Failing into a bucket shared with an unrelated route is worse than failing
    into a limit that is merely too loose, so anything that does not line up
    returns the resolved path — which is what the limiter did before the fix."""
    class _Req:
        def __init__(self, path, params, route_path):
            self.url = type("U", (), {"path": path})()
            self.scope = {"path_params": params,
                          "route": type("R", (), {"path": route_path})()}

    # a parameter value that does not appear in the path at all
    assert _route_template(_Req("/a/b", {"x": "zzz"}, "/a/{x}")) == "/a/b"
    # a template that does not match the router's declared suffix
    assert _route_template(_Req("/a/b", {"x": "b"}, "/completely/{other}")) == "/a/b"
    # an empty parameter value is skipped rather than substituted everywhere
    assert _route_template(_Req("/a/b", {"x": ""}, None)) == "/a/b"


def test_A_VALUE_REPEATED_IN_THE_PATH_IS_SUBSTITUTED_IN_THE_RIGHT_PLACE():
    """Values go back rightmost-first and longest-first, so a parameter whose
    value also appears in a static segment cannot rewrite the wrong one."""
    class _Req:
        def __init__(self, path, params, route_path):
            self.url = type("U", (), {"path": path})()
            self.scope = {"path_params": params,
                          "route": type("R", (), {"path": route_path})()}

    assert _route_template(
        _Req("/bar/thing/bar", {"x": "bar"}, "/bar/thing/{x}")) == "/bar/thing/{x}"
    # 🔴 TWO PARAMETERS WITH THE SAME VALUE — the case that made the fallback
    # necessary AND showed it was not enough. Substituting values cannot tell `x`
    # from `y` here; rendering the ROUTER'S OWN template and locating it in the
    # path can, because it is positional. No route in this app has two path
    # parameters today, so this is the shape that would have gone wrong later.
    assert _route_template(
        _Req("/a/bar/bar", {"x": "bar", "y": "bar"}, "/a/{x}/{y}")) == "/a/{x}/{y}"


def test_EVERY_RATE_LIMITED_ROUTE_IN_THE_REAL_APP_IS_ACCOUNTED_FOR():
    """🔴 A NEW PARAMETERISED ROUTE MUST NOT ARRIVE UNNOTICED.  The directive that
    commissioned this fix asserted only ONE rate-limited route had a path
    parameter. There were three — `/tickers/watchlist/{symbol}` is live and
    pre-existing, and its budget was multiplied by the ticker universe. This pins
    the census so the next one is a test failure rather than a discovery."""
    import api.main as main
    from fastapi.routing import APIRoute

    def walk(routes, prefix=""):
        for r in routes:
            if isinstance(r, APIRoute):
                yield prefix + r.path, r
            elif type(r).__name__ == "_IncludedRouter":
                ctx = r.include_context
                yield from walk(ctx.included_router.routes, prefix + (ctx.prefix or ""))
            elif getattr(r, "routes", None):
                yield from walk(r.routes, prefix + getattr(r, "path", ""))

    def limited(dep):
        fn = getattr(dep, "call", None)
        if fn is not None and getattr(fn, "__qualname__", "").startswith("rate_limit"):
            return True
        return any(limited(s) for s in getattr(dep, "dependencies", []))

    found = {p for p, r in walk(main.app.routes) if limited(r.dependant)}
    assert found == {
        "/admin/refresh", "/admin/upload-db", "/api/watchlist-rules", "/chat",
        "/tickers/watchlist/{symbol}",
        "/osint-map/api/graph/{entity_id}", "/osint-map/out/map-v1/{path:path}",
    }, sorted(found)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
