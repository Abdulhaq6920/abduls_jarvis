"""Daily token budget: a soft spending cap shared by every replica through the KV store.

"Soft" on purpose: replicas read-modify-write without a distributed lock, so a burst across many
replicas can overshoot by a few calls. That is the right trade for a cost guard; it is not a
billing system. If the KV store is unreachable the counter keeps working in memory rather than
taking AI down with the database.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from jarvis_shared.log import get_logger

from jarvis_backend.llm.types import LLMError
from jarvis_backend.storage.db import utcnow
from jarvis_backend.storage.kv import KeyValueStore

log = get_logger(__name__)


class TokenBudget:
    def __init__(self, store: Optional[KeyValueStore], limit: int,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self._store, self._limit, self._clock = store, limit, clock
        self._day = ""
        self._used = 0
        self._lock = asyncio.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    def _key(self) -> str:
        return f"llm_usage:{self._day}"

    async def _roll(self) -> None:
        """Load today's counter the first time we see a new UTC day."""
        day = self._clock().strftime("%Y%m%d")
        if day == self._day:
            return
        self._day, self._used = day, 0
        if self._store is not None:
            try:
                stored = await self._store.get(self._key())
                self._used = int(stored[0].get("tokens", 0)) if stored else 0
            except Exception as exc:
                log.warning("token budget could not load", extra={"error": type(exc).__name__})

    async def check(self) -> None:
        if self._limit <= 0:
            return
        async with self._lock:
            await self._roll()
            if self._used >= self._limit:
                raise LLMError("budget", f"{self._used}/{self._limit} tokens used today")

    async def record(self, tokens: int) -> None:
        if tokens <= 0:
            return
        async with self._lock:
            await self._roll()
            self._used += tokens
            if self._store is not None:
                try:
                    await self._store.put(self._key(), {"tokens": self._used})
                except Exception as exc:
                    log.warning("token budget could not persist", extra={"error": type(exc).__name__})

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            await self._roll()
            ratio = round(self._used / self._limit, 4) if self._limit > 0 else 0.0
            return {"used": self._used, "limit": self._limit, "ratio": ratio, "day": self._day}
