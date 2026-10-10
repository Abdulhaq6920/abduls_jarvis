"""Shared contracts for the JARVIS runtime.

Everything that crosses a process or network boundary (local agent <-> backend,
dashboard <-> backend) is defined here as a Pydantic model so both sides agree on the
wire format and so each implementation layer stays replaceable. Cross-cutting helpers
used by every process (command classification, logging) live here too.
"""
from jarvis_shared.agents import AGENT_IDS, AgentId, AgentSpec, default_agents
from jarvis_shared.audio import AudioInput, AudioResult, Transcript
from jarvis_shared.commands import CommandAssessment, classify_command
from jarvis_shared.conversation import ConversationState
from jarvis_shared.messages import (
    AgentPresence,
    AssistRequest,
    AssistResponse,
    RuntimeStatus,
    StageTrace,
)
from jarvis_shared.security import CommandCategory, RiskLevel, ToolRequest, ToolResult
from jarvis_shared.supervisor import SupervisorStage
from jarvis_shared.voice import AgentVoiceProfile, VoiceSettings, default_voice_profiles

__all__ = [
    "AGENT_IDS",
    "AgentId",
    "AgentSpec",
    "default_agents",
    "AudioInput",
    "AudioResult",
    "Transcript",
    "CommandAssessment",
    "classify_command",
    "ConversationState",
    "AgentPresence",
    "AssistRequest",
    "AssistResponse",
    "RuntimeStatus",
    "StageTrace",
    "CommandCategory",
    "RiskLevel",
    "ToolRequest",
    "ToolResult",
    "SupervisorStage",
    "AgentVoiceProfile",
    "VoiceSettings",
    "default_voice_profiles",
]

__version__ = "0.1.0"
