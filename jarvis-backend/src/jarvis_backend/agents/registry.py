"""Agent roster and per-agent configuration.

* :class:`AgentRegistry` holds the immutable specs (who exists, what they can do).
* :class:`AgentConfig` layers persisted overrides on top: whether an agent is enabled
  and its voice profile. Defaults come from ``jarvis_shared``; the database only stores
  what the user changed, so new defaults ship without migrations.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from jarvis_shared.agents import AgentId, AgentSpec, default_agents
from jarvis_shared.voice import AgentVoiceProfile, default_voice_profiles

from jarvis_backend.cache import Cache
from jarvis_backend.storage.agent_settings import AgentSettingsStore


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: Dict[AgentId, AgentSpec] = {a.id: a for a in default_agents()}

    def all(self) -> List[AgentSpec]:
        return list(self._agents.values())

    def get(self, agent_id: AgentId) -> Optional[AgentSpec]:
        return self._agents.get(agent_id)

    def register(self, spec: AgentSpec) -> None:
        """Adding an agent is additive: register a spec (and a default voice)."""
        self._agents[spec.id] = spec


class AgentConfig:
    _CACHE_KEY, _CACHE_TTL = "agentcfg", 30

    def __init__(self, store: AgentSettingsStore, cache: Optional[Cache] = None) -> None:
        self._store = store
        self._cache = cache
        self._default_voices = default_voice_profiles()

    async def _rows(self) -> Dict[str, Dict[str, Any]]:
        """Persisted overrides as plain dicts, cached (Redis) so every request does not
        hit the database. Writes invalidate the cache, so edits show up immediately."""
        if self._cache is not None:
            cached = await self._cache.get(self._CACHE_KEY)
            if cached is not None:
                return json.loads(cached)
        rows = {aid: {"enabled": r.enabled, "voice": r.voice}
                for aid, r in (await self._store.all()).items()}
        if self._cache is not None:
            await self._cache.set(self._CACHE_KEY, json.dumps(rows), self._CACHE_TTL)
        return rows

    async def _invalidate(self) -> None:
        if self._cache is not None:
            await self._cache.delete(self._CACHE_KEY)

    async def is_enabled(self, agent_id: AgentId) -> bool:
        if agent_id == AgentId.JARVIS:
            return True
        row = (await self._rows()).get(agent_id.value)
        return True if row is None else bool(row["enabled"])

    async def enabled_map(self) -> Dict[str, bool]:
        rows = await self._rows()
        return {a.value: (a == AgentId.JARVIS or rows.get(a.value) is None
                          or bool(rows[a.value]["enabled"])) for a in AgentId}

    async def set_enabled(self, agent_id: AgentId, enabled: bool) -> None:
        if agent_id == AgentId.JARVIS and not enabled:
            raise ValueError("JARVIS is the supervisor and cannot be disabled.")
        await self._store.set_enabled(agent_id.value, enabled)
        await self._invalidate()

    async def voice(self, agent_id: AgentId) -> AgentVoiceProfile:
        return (await self.voices()).get(agent_id.value) or self._default_voices[AgentId.JARVIS]

    async def voices(self) -> Dict[str, AgentVoiceProfile]:
        rows = await self._rows()
        out: Dict[str, AgentVoiceProfile] = {}
        for agent_id, default in self._default_voices.items():
            override = (rows.get(agent_id.value) or {}).get("voice")
            if override:
                merged = {**default.model_dump(mode="json"), **override, "agent_id": agent_id.value}
                out[agent_id.value] = AgentVoiceProfile.model_validate(merged)
            else:
                out[agent_id.value] = default
        return out

    async def update_voice(self, agent_id: AgentId, patch: Dict[str, Any]) -> AgentVoiceProfile:
        """Merge ``patch`` into the current profile; Pydantic re-validates the whole thing."""
        current = await self.voice(agent_id)
        merged = {**current.model_dump(mode="json"),
                  **{k: v for k, v in patch.items() if v is not None}, "agent_id": agent_id.value}
        profile = AgentVoiceProfile.model_validate(merged)  # raises on out-of-range values
        await self._store.set_voice(agent_id.value, profile.model_dump(mode="json"))
        await self._invalidate()
        return profile

    async def reset_voice(self, agent_id: AgentId) -> AgentVoiceProfile:
        await self._store.set_voice(agent_id.value, None)
        await self._invalidate()
        return self._default_voices[agent_id]
