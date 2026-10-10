"""Cache and counters, backed by Redis with a bounded in-process fallback.

Design rules:

* **Redis is an accelerator, never the source of truth.** Anything cached can be
  rebuilt from the database, so a Redis outage degrades speed, not correctness.
* **Bounded memory.** The in-process store is an LRU with a hard item cap and lazy +
  periodic expiry, so it cannot grow without limit under load or attack.
* **Failures are visible.** Every fallback is counted (``cache_errors``) and logged with
  throttling, so a silently dead Redis shows up in metrics instead of hiding.

Keys are namespaced (``jarvis:<name>``). Values are strings; callers serialise.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Tuple

from jarvis_shared.log import get_logger

log = get_logger(__name__)
NAMESPACE = "jarvis:"
_LOG_THROTTLE_SECONDS = 30.0


class CacheBackend(Protocol):
    async def get(self, key: str) -> Optional[str]: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def incr_window(self, key: str, window: int) -> Tuple[int, int]: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


# ------------------------------------------------------------------------- in-process
class LocalCache:
    """Thread-unsafe by design: only ever touched from the event loop."""

    def __init__(self, max_items: int = 5000) -> None:
        self._max = max_items
        self._data: OrderedDict[str, Tuple[float, str]] = OrderedDict()
        self._counters: Dict[str, Tuple[float, int]] = {}

    def __len__(self) -> int:
        return len(self._data) + len(self._counters)

    def _evict(self) -> None:
        while len(self._data) > self._max:
            self._data.popitem(last=False)  # least recently used
        if len(self._counters) > self._max:
            now = time.monotonic()
            self._counters = {k: v for k, v in self._counters.items() if v[0] > now}
            while len(self._counters) > self._max:  # still too many live windows: drop oldest
                self._counters.pop(next(iter(self._counters)))

    async def get(self, key: str) -> Optional[str]:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= time.monotonic():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    async def set(self, key: str, value: str, ttl: int) -> None:
        self._data[key] = (time.monotonic() + ttl, value)
        self._data.move_to_end(key)
        self._evict()

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._counters.pop(key, None)

    async def incr_window(self, key: str, window: int) -> Tuple[int, int]:
        """Fixed-window counter. Returns (count_in_window, seconds_until_reset)."""
        now = time.monotonic()
        expires, count = self._counters.get(key, (0.0, 0))
        if expires <= now:
            expires, count = now + window, 0
        count += 1
        self._counters[key] = (expires, count)
        self._evict()
        return count, max(1, int(expires - now))

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._data.clear()
        self._counters.clear()


# ------------------------------------------------------------------------------ redis
class RedisCache:
    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(
            url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2,
            health_check_interval=30,
        )

    async def get(self, key: str) -> Optional[str]:
        return await self._redis.get(NAMESPACE + key)

    async def set(self, key: str, value: str, ttl: int) -> None:
        await self._redis.set(NAMESPACE + key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        await self._redis.delete(NAMESPACE + key)

    async def incr_window(self, key: str, window: int) -> Tuple[int, int]:
        full = NAMESPACE + key
        async with self._redis.pipeline(transaction=True) as pipe:  # MULTI/EXEC: atomic
            pipe.set(full, 0, ex=window, nx=True)
            pipe.incr(full)
            pipe.ttl(full)
            _, count, ttl = await pipe.execute()
        return int(count), max(1, int(ttl))

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def close(self) -> None:
        await self._redis.aclose()


# ------------------------------------------------------------------- resilient facade
@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    errors: int = 0
    fallback_active: bool = False
    last_error: str = ""
    _last_logged: float = field(default=0.0, repr=False)


class Cache:
    """What the application uses: Redis when healthy, bounded local fallback otherwise."""

    def __init__(self, primary: Optional[CacheBackend], max_local_items: int = 5000) -> None:
        self._primary = primary
        self._local = LocalCache(max_local_items)
        self.stats = CacheStats()

    @property
    def backend(self) -> str:
        return "redis" if self._primary is not None else "local"

    @property
    def local_items(self) -> int:
        return len(self._local)

    def _note_failure(self, exc: Exception) -> None:
        self.stats.errors += 1
        self.stats.fallback_active = True
        self.stats.last_error = type(exc).__name__
        now = time.monotonic()
        if now - self.stats._last_logged > _LOG_THROTTLE_SECONDS:
            self.stats._last_logged = now
            log.warning("redis unavailable; using in-process fallback",
                        extra={"error": type(exc).__name__})

    async def _run(self, op: str, *args: Any) -> Any:
        if self._primary is not None:
            try:
                result = await getattr(self._primary, op)(*args)
                self.stats.fallback_active = False
                return result
            except Exception as exc:  # network, timeout, auth, protocol: all degrade the same way
                self._note_failure(exc)
        return await getattr(self._local, op)(*args)

    async def get(self, key: str) -> Optional[str]:
        value = await self._run("get", key)
        if value is None:
            self.stats.misses += 1
        else:
            self.stats.hits += 1
        return value

    async def set(self, key: str, value: str, ttl: int = 60) -> None:
        await self._run("set", key, value, ttl)

    async def delete(self, key: str) -> None:
        # Invalidate BOTH tiers so a stale fallback copy can never resurface.
        await self._local.delete(key)
        if self._primary is not None:
            try:
                await self._primary.delete(key)
            except Exception as exc:
                self._note_failure(exc)

    async def incr_window(self, key: str, window: int) -> Tuple[int, int]:
        return await self._run("incr_window", key, window)

    async def healthy(self) -> bool:
        if self._primary is None:
            return True
        try:
            return await self._primary.ping()
        except Exception as exc:
            self._note_failure(exc)
            return False

    async def close(self) -> None:
        await self._local.close()
        if self._primary is not None:
            await self._primary.close()


def build_cache(redis_url: str, max_local_items: int) -> Cache:
    if redis_url:
        log.info("cache: redis")
        return Cache(RedisCache(redis_url), max_local_items)
    log.info("cache: in-process (single replica only)")
    return Cache(None, max_local_items)
