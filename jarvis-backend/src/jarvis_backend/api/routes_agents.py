"""Agents page API: roster, enable/disable, voice editing, per-agent activity."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from jarvis_shared.voice import AgentVoiceProfile

from jarvis_backend.api.deps import get_container, parse_agent
from jarvis_backend.auth.deps import admin_only, any_role
from jarvis_backend.auth.service import Principal
from jarvis_backend.container import Container
from jarvis_backend.schemas import AgentEnabled, AgentOut, AgentStats, TurnOut, VoiceUpdate

router = APIRouter(prefix="/api", tags=["agents"])


@router.get("/agents", response_model=List[AgentOut], dependencies=[any_role])
async def list_agents(c: Container = Depends(get_container)) -> List[AgentOut]:
    voices, enabled, stats = await c.agent_config.voices(), await c.agent_config.enabled_map(), await c.turns.stats()
    return [
        AgentOut(spec=spec, voice=voices[spec.id.value], enabled=enabled[spec.id.value],
                 can_disable=spec.id.value != "JARVIS", stats=stats.get(spec.id.value, AgentStats()))
        for spec in c.registry.all()
    ]


@router.get("/voices", response_model=List[AgentVoiceProfile], dependencies=[any_role])
async def list_voices(c: Container = Depends(get_container)) -> list:
    return list((await c.agent_config.voices()).values())


@router.get("/voices/{agent_id}", response_model=AgentVoiceProfile, dependencies=[any_role])
async def get_voice(agent_id: str, c: Container = Depends(get_container)) -> AgentVoiceProfile:
    return await c.agent_config.voice(parse_agent(agent_id))


@router.put("/agents/{agent_id}/voice", response_model=AgentVoiceProfile)
async def update_voice(agent_id: str, patch: VoiceUpdate, principal: Principal = admin_only,
                       c: Container = Depends(get_container)) -> AgentVoiceProfile:
    agent = parse_agent(agent_id)
    try:
        profile = await c.agent_config.update_voice(agent, patch.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    await c.audit.record("voice_updated", agent_id=agent.value, by=principal.subject,
                         fields=sorted(patch.model_dump(exclude_none=True)))
    return profile


@router.delete("/agents/{agent_id}/voice", response_model=AgentVoiceProfile)
async def reset_voice(agent_id: str, principal: Principal = admin_only,
                      c: Container = Depends(get_container)) -> AgentVoiceProfile:
    agent = parse_agent(agent_id)
    profile = await c.agent_config.reset_voice(agent)
    await c.audit.record("voice_reset", agent_id=agent.value, by=principal.subject)
    return profile


@router.put("/agents/{agent_id}/enabled", response_model=AgentEnabled)
async def set_enabled(agent_id: str, body: AgentEnabled, principal: Principal = admin_only,
                      c: Container = Depends(get_container)) -> AgentEnabled:
    agent = parse_agent(agent_id)
    try:
        await c.agent_config.set_enabled(agent, body.enabled)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    await c.audit.record("agent_enabled_changed", agent_id=agent.value, by=principal.subject,
                         enabled=body.enabled)
    return body


@router.get("/agents/{agent_id}/activity", response_model=List[TurnOut], dependencies=[any_role])
async def agent_activity(agent_id: str, limit: int = 25, c: Container = Depends(get_container)) -> list:
    agent = parse_agent(agent_id)
    return await c.turns.recent(agent_id=agent.value, limit=max(1, min(limit, 100)))
