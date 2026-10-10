"""Voice profiles and TTS settings.

Each agent has exactly one AgentVoiceProfile. Voices are *original synthetic* voices
with distinct characteristics -- we never imitate real actors or copyrighted
performances. The `voice_id` values are logical names; a TTSProvider adapter maps
them to whatever the underlying engine understands.
"""
from __future__ import annotations

from typing import Dict, List

from pydantic import BaseModel, Field

from jarvis_shared.agents import AgentId


class VoiceSettings(BaseModel):
    """Per-utterance synthesis parameters a provider should honor when it can."""

    speed: float = Field(1.0, ge=0.5, le=2.0)
    pitch: float = Field(1.0, ge=0.5, le=2.0)
    style: str = "neutral"
    language: str = "en-US"


class AgentVoiceProfile(BaseModel):
    """A replaceable mapping from an agent to a synthetic voice.

    `voice_provider` names the adapter (e.g. "dev", "piper", "azure", "elevenlabs").
    `voice_id` is interpreted by that adapter. Nothing here is provider-specific so a
    deployment can switch providers by changing config, not agent logic.
    """

    agent_id: AgentId
    voice_provider: str = "dev"
    voice_id: str
    language: str = "en-US"
    speed: float = Field(1.0, ge=0.5, le=2.0)
    pitch: float = Field(1.0, ge=0.5, le=2.0)
    style: str = "neutral"
    enabled: bool = True

    def settings(self) -> VoiceSettings:
        return VoiceSettings(
            speed=self.speed, pitch=self.pitch, style=self.style, language=self.language
        )


def default_voice_profiles() -> Dict[AgentId, AgentVoiceProfile]:
    """Distinct synthetic voice characteristics per agent.

    Characteristics (pitch/speed/style) are chosen to make each agent audibly
    distinct even on a basic/dev TTS engine. These are *not* impersonations.
    """
    raw: List[AgentVoiceProfile] = [
        AgentVoiceProfile(agent_id=AgentId.JARVIS, voice_id="jarvis_default",
                          pitch=0.95, speed=0.98, style="calm-authoritative"),
        AgentVoiceProfile(agent_id=AgentId.FRIDAY, voice_id="friday_default",
                          pitch=1.08, speed=1.05, style="friendly-efficient"),
        AgentVoiceProfile(agent_id=AgentId.VISION, voice_id="vision_default",
                          pitch=1.0, speed=0.95, style="precise-calm"),
        AgentVoiceProfile(agent_id=AgentId.NATASHA, voice_id="natasha_default",
                          pitch=1.02, speed=1.0, style="professional-confident"),
        AgentVoiceProfile(agent_id=AgentId.WONG, voice_id="wong_default",
                          pitch=0.9, speed=0.9, style="deliberate-calm"),
        AgentVoiceProfile(agent_id=AgentId.TONY, voice_id="tony_default",
                          pitch=1.05, speed=1.12, style="energetic-technical"),
        AgentVoiceProfile(agent_id=AgentId.SHURI, voice_id="shuri_default",
                          pitch=1.12, speed=1.1, style="curious-energetic"),
        AgentVoiceProfile(agent_id=AgentId.QUICKSILVER, voice_id="quicksilver_default",
                          pitch=1.1, speed=1.25, style="fast-concise"),
        AgentVoiceProfile(agent_id=AgentId.NICK_FURY, voice_id="fury_default",
                          pitch=0.85, speed=0.95, style="serious-authoritative"),
        AgentVoiceProfile(agent_id=AgentId.DR_STRANGE, voice_id="strange_default",
                          pitch=0.95, speed=0.92, style="thoughtful-strategic"),
    ]
    return {p.agent_id: p for p in raw}
