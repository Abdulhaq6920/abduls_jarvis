"""Event bus: how live updates reach every WebSocket client on every replica.

With one API process an in-memory bus is enough. With several replicas behind a load
balancer a browser's WebSocket is connected to *one* of them, so events produced on
another replica must be relayed: :class:`RedisBus` does that via pub/sub. Both satisfy
the same :class:`EventBus` interface, so callers never know which is in use.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Protocol, Set

from jarvis_shared.log import get_logger

log = get_logger(__name__)
CHANNEL = "jarvis:events"
Event = Dict[str, Any]


class Subscription(Protocol):
    async def get(self, timeout: float) -> Optional[Event]: ...
    async def close(self) -> None: ...


class EventBus(Protocol):
    kind: str

    async def publish(self, event: Event) -> None: ...
    async def subscribe(self) -> Subscription: ...
    async def close(self) -> None: ...


# --------------------------------------------------------------------------- in-memory
class _QueueSubscription:
    def __init__(self, bus: InMemoryBus) -> None:
        self._bus = bus
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=100)

    async def get(self, timeout: float) -> Optional[Event]:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except TimeoutError:
            return None

    async def close(self) -> None:
        self._bus._subs.discard(self)


class InMemoryBus:
    kind = "memory"

    def __init__(self) -> None:
        self._subs: Set[_QueueSubscription] = set()

    async def publish(self, event: Event) -> None:
        for sub in list(self._subs):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("dropping event for slow subscriber")

    async def subscribe(self) -> Subscription:
        sub = _QueueSubscription(self)
        self._subs.add(sub)
        return sub

    async def close(self) -> None:
        self._subs.clear()


# --------------------------------------------------------------------------------- redis
class _RedisSubscription:
    def __init__(self, pubsub: Any) -> None:
        self._pubsub = pubsub

    async def get(self, timeout: float) -> Optional[Event]:
        msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
        if not msg:
            return None
        try:
            return json.loads(msg["data"])
        except (ValueError, TypeError):
            log.warning("discarding malformed event from redis")
            return None

    async def close(self) -> None:
        await self._pubsub.unsubscribe(CHANNEL)
        await self._pubsub.aclose()


class RedisBus:
    """Cross-replica fan-out. Requires the ``redis`` extra."""

    kind = "redis"

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, decode_responses=True)

    async def publish(self, event: Event) -> None:
        try:
            await self._redis.publish(CHANNEL, json.dumps(event, default=str))
        except Exception:
            # Live updates are best-effort; they must never fail a user request.
            log.warning("redis publish failed", exc_info=True)

    async def subscribe(self) -> Subscription:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        return _RedisSubscription(pubsub)

    async def close(self) -> None:
        await self._redis.aclose()


def build_bus(redis_url: str = "") -> EventBus:
    if redis_url:
        log.info("event bus: redis")
        return RedisBus(redis_url)
    log.info("event bus: in-memory (single replica only)")
    return InMemoryBus()
