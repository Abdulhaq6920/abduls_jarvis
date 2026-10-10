"""Persisted per-agent overrides (enabled flag, voice profile)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import select

from jarvis_backend.storage.db import Database, utcnow
from jarvis_backend.storage.models import AgentSettingRow


class AgentSettingsStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def all(self) -> Dict[str, AgentSettingRow]:
        async with self._db.session() as session:
            rows = (await session.execute(select(AgentSettingRow))).scalars().all()
        return {r.agent_id: r for r in rows}

    async def set_enabled(self, agent_id: str, enabled: bool) -> None:
        async with self._db.session() as session:
            row = await session.get(AgentSettingRow, agent_id)
            if row is None:
                session.add(AgentSettingRow(agent_id=agent_id, enabled=enabled, updated_at=utcnow()))
            else:
                row.enabled, row.updated_at = enabled, utcnow()

    async def set_voice(self, agent_id: str, voice: Optional[Dict[str, Any]]) -> None:
        """Store a voice override, or ``None`` to fall back to the built-in default."""
        async with self._db.session() as session:
            row = await session.get(AgentSettingRow, agent_id)
            if row is None:
                session.add(AgentSettingRow(agent_id=agent_id, enabled=True, voice=voice,
                                            updated_at=utcnow()))
            else:
                row.voice, row.updated_at = voice, utcnow()
