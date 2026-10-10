"""Cache degradation, bounded memory, and rate limiting."""
import asyncio

import fakeredis
import pytest
from fastapi import HTTPException
from jarvis_backend.cache import Cache, LocalCache, RedisCache
from jarvis_backend.ratelimit import RateLimiter


class FlakyBackend:
    """A primary that always fails, like a Redis that has gone away."""

    async def get(self, key): raise ConnectionError("redis down")
    async def set(self, key, value, ttl): raise ConnectionError("redis down")
    async def delete(self, key): raise ConnectionError("redis down")
    async def incr_window(self, key, window): raise ConnectionError("redis down")
    async def ping(self): raise ConnectionError("redis down")
    async def close(self): pass


def fake_redis_cache() -> RedisCache:
    cache = RedisCache.__new__(RedisCache)
    cache._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return cache


# ---------------------------------------------------------------------- local cache
async def test_local_cache_expires():
    c = LocalCache()
    await c.set("k", "v", ttl=1)
    assert await c.get("k") == "v"
    await asyncio.sleep(1.1)
    assert await c.get("k") is None


async def test_local_cache_is_bounded():
    c = LocalCache(max_items=100)
    for i in range(1000):
        await c.set(f"k{i}", "v", ttl=60)
    assert len(c) <= 100
    assert await c.get("k999") == "v" and await c.get("k0") is None  # LRU: oldest evicted


async def test_local_counters_are_bounded():
    c = LocalCache(max_items=50)
    for i in range(500):  # an attacker spraying distinct keys must not grow memory
        await c.incr_window(f"ip:{i}", 60)
    assert len(c) <= 50


# ---------------------------------------------------------------------------- redis
async def test_redis_cache_roundtrip_and_namespace():
    r = fake_redis_cache()
    await r.set("a", "1", ttl=30)
    assert await r.get("a") == "1"
    assert await r._redis.get("jarvis:a") == "1"  # namespaced


async def test_redis_window_counter_is_atomic_under_concurrency():
    r = fake_redis_cache()
    results = await asyncio.gather(*[r.incr_window("burst", 60) for _ in range(50)])
    assert sorted(count for count, _ in results) == list(range(1, 51))


# ------------------------------------------------------------------------- fallback
async def test_redis_outage_degrades_instead_of_failing():
    cache = Cache(FlakyBackend())
    await cache.set("k", "v", ttl=30)           # must not raise
    assert await cache.get("k") == "v"          # served from the local fallback
    assert cache.stats.fallback_active and cache.stats.errors >= 2
    assert await cache.healthy() is False


async def test_delete_invalidates_both_tiers():
    cache = Cache(fake_redis_cache())
    await cache._local.set("k", "stale", ttl=60)
    await cache._primary.set("k", "stale", ttl=60)
    await cache.delete("k")
    assert await cache.get("k") is None


# ------------------------------------------------------------------------ limiter
async def test_rate_limiter_blocks_over_limit_with_retry_after():
    limiter = RateLimiter(Cache(None))
    for _ in range(3):
        await limiter.enforce("login:x", limit=3, window=60)
    with pytest.raises(HTTPException) as exc:
        await limiter.enforce("login:x", limit=3, window=60)
    assert exc.value.status_code == 429 and int(exc.value.headers["Retry-After"]) > 0


async def test_rate_limiter_keys_are_independent():
    limiter = RateLimiter(Cache(None))
    for _ in range(3):
        await limiter.enforce("a", limit=3, window=60)
    await limiter.enforce("b", limit=3, window=60)  # a different caller is unaffected


async def test_rate_limiter_still_enforces_when_redis_is_down():
    limiter = RateLimiter(Cache(FlakyBackend()))
    for _ in range(2):
        await limiter.enforce("k", limit=2, window=60)
    with pytest.raises(HTTPException):
        await limiter.enforce("k", limit=2, window=60)
