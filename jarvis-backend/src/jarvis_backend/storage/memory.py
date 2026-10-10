"""Long-term memory: facts and notes JARVIS keeps across sessions.

Search is keyword based and portable (works identically on SQLite and PostgreSQL). The
:class:`MemoryStore` surface is deliberately small so a vector-embedding implementation
can replace it later without touching callers.
"""
from __future__ import annotations

import re
import uuid
from typing import List, Optional, Sequence

from sqlalchemy import delete, func, select

from jarvis_backend.schemas import MemoryOut
from jarvis_backend.storage.db import Database, utcnow
from jarvis_backend.storage.models import MemoryRow

_WORD = re.compile(r"[a-z0-9']+")
_STOPWORDS = frozenset(
    "a an the and or but of to in on at for with about that this these those is are was were "
    "be been it its my me i you your we our do does did what who when where which how tell "
    "remember recall know please jarvis wong".split()
)
MAX_MEMORIES = 5000  # a runaway client must not be able to fill the database


def keywords(text: str) -> List[str]:
    """Significant lower-case words of ``text`` (stopwords and 1-2 letter words dropped)."""
    seen: List[str] = []
    for word in _WORD.findall(text.lower()):
        if len(word) > 2 and word not in _STOPWORDS and word not in seen:
            seen.append(word)
    return seen


class MemoryFull(Exception):
    pass


class MemoryStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, text: str, *, kind: str = "fact", agent_id: Optional[str] = None,
                  tags: Sequence[str] = (), source: str = "voice") -> MemoryOut:
        if await self.count() >= MAX_MEMORIES:
            raise MemoryFull(f"Memory limit of {MAX_MEMORIES} items reached.")
        row = MemoryRow(
            id=str(uuid.uuid4()), kind=kind, text=text.strip(), agent_id=agent_id,
            tags=[t.strip().lower()[:32] for t in tags if t.strip()][:10],
            source=source, created_at=utcnow(),
        )
        async with self._db.session() as session:
            session.add(row)
        return MemoryOut.model_validate(row)

    async def search(self, query: str, limit: int = 5) -> List[MemoryOut]:
        """Most relevant memories: more matching keywords first, newer breaks ties."""
        words = keywords(query)
        if not words:
            return []
        # The text column is encrypted at rest, so the database cannot match on it. We
        # load the (capped) set, which the ORM decrypts, and score it here.
        stmt = select(MemoryRow).order_by(MemoryRow.created_at.desc()).limit(MAX_MEMORIES)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()

        scored = []
        for row in rows:
            haystack = row.text.lower() + " " + " ".join(row.tags)
            hits = sum(1 for w in words if w in haystack)
            if hits:
                scored.append((hits, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)  # stable: newest first on ties
        return [MemoryOut.model_validate(row) for _, row in scored[:limit]]

    async def list(self, kind: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[MemoryOut]:
        stmt = select(MemoryRow).order_by(MemoryRow.created_at.desc()).limit(min(limit, 500)).offset(offset)
        if kind:
            stmt = stmt.where(MemoryRow.kind == kind)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [MemoryOut.model_validate(r) for r in rows]

    async def count(self) -> int:
        async with self._db.session() as session:
            return (await session.execute(select(func.count()).select_from(MemoryRow))).scalar_one()

    async def delete(self, memory_id: str) -> bool:
        async with self._db.session() as session:
            res = await session.execute(delete(MemoryRow).where(MemoryRow.id == memory_id))
            return res.rowcount == 1

    async def delete_all(self) -> int:
        async with self._db.session() as session:
            res = await session.execute(delete(MemoryRow))
            return res.rowcount or 0
