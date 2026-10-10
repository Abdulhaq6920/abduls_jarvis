"""Shared FastAPI dependencies and small helpers for the routers."""
from __future__ import annotations

from fastapi import HTTPException, Request
from jarvis_shared.agents import AgentId

from jarvis_backend.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


def parse_agent(agent_id: str) -> AgentId:
    """Path parameter -> AgentId, answering 404 (not 422) for an unknown agent."""
    try:
        return AgentId(agent_id.upper())
    except ValueError:
        raise HTTPException(404, f"Unknown agent '{agent_id}'.") from None
