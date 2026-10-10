"""Tiny shared key-value store for state every replica must agree on."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from jarvis_backend.storage.db import Database, utcnow
from jarvis_backend.storage.models import KeyValueRow


class KeyValueStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def put(self, key: str, value: Dict[str, Any]) -> None:
        async with self._db.session() as session:
            row = await session.get(KeyValueRow, key)
            if row is None:
                session.add(KeyValueRow(key=key, value=value, updated_at=utcnow()))
            else:
                row.value, row.updated_at = value, utcnow()

    async def get(self, key: str) -> Optional[Tuple[Dict[str, Any], datetime]]:
        async with self._db.session() as session:
            row = await session.get(KeyValueRow, key)
            return (row.value, row.updated_at) if row else None
