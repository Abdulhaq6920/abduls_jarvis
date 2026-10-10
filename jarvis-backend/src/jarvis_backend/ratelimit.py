"""Rate limiting on top of the shared cache counters.

Used for login (brute force), token refresh, and the assist endpoint (runaway clients and
abuse of the approval gate). Limits are keyed by an opaque string chosen by the caller,
e.g. ``login:ip:203.0.113.5`` or ``assist:user:<id>``.

When Redis is down the limit degrades to per-process (still bounded, still enforced, but
N replicas now allow N times the rate). That is logged and counted via cache stats.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status

from jarvis_backend.cache import Cache


@dataclass(frozen=True)
class LimitResult:
    allowed: bool
    count: int
    limit: int
    retry_after: int


class RateLimiter:
    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    async def hit(self, key: str, *, limit: int, window: int) -> LimitResult:
        count, retry_after = await self._cache.incr_window("rl:" + key, window)
        return LimitResult(count <= limit, count, limit, retry_after)

    async def enforce(self, key: str, *, limit: int, window: int) -> None:
        """Raise 429 (with Retry-After) when the caller is over its limit."""
        result = await self.hit(key, limit=limit, window=window)
        if not result.allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Slow down.",
                headers={"Retry-After": str(result.retry_after)},
            )
