"""Conversation turns: short-term memory, activity feed, and per-agent statistics."""
from __future__ import annotations

import uuid
from datetime import UTC, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select

from jarvis_backend.schemas import AgentStats, TurnOut
from jarvis_backend.storage.db import Database, utcnow
from jarvis_backend.storage.models import TurnRow


class TurnStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, *, session_id: str, agent_id: str, utterance: str, speech: str,
                  latency_ms: int, trace: List[Dict[str, Any]],
                  pending_approval_id: Optional[str] = None) -> TurnOut:
        row = TurnRow(id=str(uuid.uuid4()), session_id=session_id, agent_id=agent_id,
                      utterance=utterance, speech=speech, latency_ms=latency_ms,
                      trace=trace, pending_approval_id=pending_approval_id, created_at=utcnow())
        async with self._db.session() as session:
            session.add(row)
        return TurnOut.model_validate(row)

    async def history(self, session_id: str, limit: int = 6) -> List[TurnOut]:
        """Most recent turns of one conversation, oldest first (reading order)."""
        stmt = (select(TurnRow).where(TurnRow.session_id == session_id)
                .order_by(TurnRow.created_at.desc()).limit(limit))
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [TurnOut.model_validate(r) for r in reversed(rows)]

    async def recent(self, *, agent_id: Optional[str] = None, limit: int = 50) -> List[TurnOut]:
        stmt = select(TurnRow).order_by(TurnRow.created_at.desc()).limit(min(limit, 200))
        if agent_id:
            stmt = stmt.where(TurnRow.agent_id == agent_id)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [TurnOut.model_validate(r) for r in rows]

    async def stats(self) -> Dict[str, AgentStats]:
        stmt = select(TurnRow.agent_id, func.count(), func.avg(TurnRow.latency_ms),
                      func.max(TurnRow.created_at)).group_by(TurnRow.agent_id)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).all()
        out: Dict[str, AgentStats] = {}
        for agent_id, count, avg, last in rows:
            last_active = last
            if isinstance(last, str):  # SQLite aggregates lose the column type
                from datetime import datetime
                last_active = datetime.fromisoformat(last).replace(tzinfo=UTC)
            out[agent_id] = AgentStats(turns=count, avg_latency_ms=round(float(avg), 1) if avg is not None else None,
                                       last_active=last_active)
        return out

    async def purge_older_than(self, days: int) -> int:
        """Retention: conversation text is personal data and must not live forever."""
        cutoff = utcnow() - timedelta(days=days)
        async with self._db.session() as session:
            res = await session.execute(delete(TurnRow).where(TurnRow.created_at < cutoff))
            return res.rowcount or 0
