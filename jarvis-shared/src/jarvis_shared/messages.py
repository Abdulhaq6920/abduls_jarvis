"""Messages exchanged between the local runtime, backend, and dashboard."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from jarvis_shared.agents import AgentId
from jarvis_shared.security import ToolRequest
from jarvis_shared.supervisor import SupervisorStage


class AssistRequest(BaseModel):
    """What the local runtime sends to the backend after STT."""

    session_id: str = Field(min_length=1, max_length=128)
    utterance: str = Field(max_length=2000)
    # Whether this came right after a wake word (true) or inside an active
    # conversation (false). Helps the supervisor craft acknowledgements.
    woke_from_wake_word: bool = True
    # Opaque context the local agent attaches (active app, allowed paths, etc.).
    client_context: dict = Field(default_factory=dict)


class StageTrace(BaseModel):
    stage: SupervisorStage
    detail: str = ""
    agent_id: Optional[AgentId] = None


class AssistResponse(BaseModel):
    """What the backend returns for an utterance."""

    session_id: str
    # The agent that owns the spoken response (its voice is used).
    responding_agent: AgentId
    # Natural-language text to synthesize and speak.
    speech: str
    # True if JARSIS should keep the conversation open (expects a follow-up).
    keep_listening: bool = True
    # Full supervisor trace for observability.
    trace: List[StageTrace] = Field(default_factory=list)
    # If the action was gated, the approval that must be granted first.
    pending_approval_id: Optional[str] = None
    # Tool requests the backend policy allowed without human approval. The local agent
    # STILL re-validates each one before executing (it is the security boundary).
    tool_requests: List[ToolRequest] = Field(default_factory=list)
    # A short pre-amble JARVIS speaks when delegating (optional).
    delegation_note: Optional[str] = None


class AgentPresence(BaseModel):
    """Observability snapshot of the active agent chain for the dashboard."""

    chain: List[AgentId] = Field(default_factory=list)
    activity: str = ""


class RuntimeStatus(BaseModel):
    """Local runtime health, surfaced in the tray and dashboard."""

    online: bool
    microphone_paused: bool
    conversation_state: str
    wake_word: str
    backend_connected: bool
    detail: str = ""
