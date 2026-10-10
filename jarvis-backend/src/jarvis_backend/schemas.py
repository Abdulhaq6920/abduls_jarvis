"""API / domain read-models (what leaves the storage layer and what the HTTP API returns)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from jarvis_shared.agents import AgentSpec
from jarvis_shared.voice import AgentVoiceProfile
from pydantic import BaseModel, ConfigDict, Field


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ApprovalOut(_ORM):
    id: str
    agent_id: str
    summary: str
    risk: str
    status: str
    request: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    executed_at: Optional[datetime] = None


class ApprovalDecision(BaseModel):
    granted: bool


class ApprovalResult(BaseModel):
    """Reported by the local agent after it ran (or refused) an approved request."""

    ok: bool
    output: str = Field(default="", max_length=8000)
    error: str = Field(default="", max_length=2000)


class TurnOut(_ORM):
    id: str
    session_id: str
    agent_id: str
    utterance: str
    speech: str
    latency_ms: int
    pending_approval_id: Optional[str] = None
    created_at: datetime


class MemoryOut(_ORM):
    id: str
    kind: str
    text: str
    tags: List[str] = Field(default_factory=list)
    agent_id: Optional[str] = None
    source: str
    created_at: datetime


class MemoryCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    tags: List[str] = Field(default_factory=list, max_length=10)
    kind: str = Field(default="fact", pattern="^(fact|note)$")


class AuditOut(_ORM):
    id: int
    ts: datetime
    event: str
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    hash: str


class AuditVerification(BaseModel):
    ok: bool
    checked: int
    first_bad_id: Optional[int] = None
    reason: str = ""


class AgentStats(BaseModel):
    turns: int = 0
    avg_latency_ms: Optional[float] = None
    last_active: Optional[datetime] = None


class AgentOut(BaseModel):
    spec: AgentSpec
    voice: AgentVoiceProfile
    enabled: bool
    # JARVIS is the supervisor and cannot be disabled.
    can_disable: bool
    stats: AgentStats


class VoiceUpdate(BaseModel):
    """Partial update of an agent's voice profile. Bounds are enforced again when the
    merged profile is validated, so an out-of-range value can never be stored."""

    voice_provider: Optional[str] = Field(default=None, pattern=r"^[a-z0-9_\-]{1,32}$")
    voice_id: Optional[str] = Field(default=None, pattern=r"^[a-z0-9_\-]{1,64}$")
    language: Optional[str] = Field(default=None, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
    speed: Optional[float] = Field(default=None, ge=0.5, le=2.0)
    pitch: Optional[float] = Field(default=None, ge=0.5, le=2.0)
    style: Optional[str] = Field(default=None, max_length=48)
    enabled: Optional[bool] = None


class AgentEnabled(BaseModel):
    enabled: bool


class JobOut(BaseModel):
    id: str
    name: str
    cron: str
    description: str
    last_run: Optional[JobRunOut] = None


class JobRunOut(_ORM):
    id: str
    job_id: str
    trigger: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    ok: bool
    output: str = ""
    error: str = ""


class RuntimeState(BaseModel):
    """Heartbeat pushed by the local agent so the dashboard can show the real mic state."""

    conversation_state: str = Field(max_length=32)
    microphone_paused: bool = False
    wake_word: str = Field(default="hey jarvis", max_length=64)
    version: str = Field(default="", max_length=32)


JobOut.model_rebuild()
