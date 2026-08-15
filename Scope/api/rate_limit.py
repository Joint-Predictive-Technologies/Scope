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

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

# "path:ip" -> deque of request timestamps within the current window.
_buckets: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(max_requests: int, window_seconds: int):
    """FastAPI dependency factory: `Depends(rate_limit(10, 60))` allows at most
    10 requests per client IP per rolling 60s window on the route it's attached
    to. Raises 429 over the limit; never blocks a request that's under it."""
    def _dep(request: Request) -> None:
        key = f"{request.url.path}:{_client_ip(request)}"
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
