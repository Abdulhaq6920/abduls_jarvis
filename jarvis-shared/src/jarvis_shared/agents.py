"""The JARVIS agent roster and their communication personalities.

Personalities affect *communication style only*. They must never override security
policy (see jarvis_backend.policy). Adding a new agent is additive: register a spec
here and a voice profile in `voice.py`; the supervisor and router pick it up without
code changes.
"""
from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class AgentId(str, Enum):
    """Stable identifiers used across every boundary."""

    JARVIS = "JARVIS"
    FRIDAY = "FRIDAY"
    VISION = "VISION"
    NATASHA = "NATASHA"
    WONG = "WONG"
    TONY = "TONY"
    SHURI = "SHURI"
    QUICKSILVER = "QUICKSILVER"
    NICK_FURY = "NICK_FURY"
    DR_STRANGE = "DR_STRANGE"


AGENT_IDS: List[str] = [a.value for a in AgentId]


class AgentSpec(BaseModel):
    """Declarative description of an agent.

    `capabilities` are coarse routing hints; `tools` are the tool *categories* the
    agent is permitted to request (the local validator enforces the fine-grained
    policy regardless of what is listed here).
    """

    id: AgentId
    display_name: str
    role: str
    personality: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    # Keywords that bias the router toward this agent (cheap heuristic layer; the
    # LangGraph/LLM router is the real decision-maker, this is a deterministic fallback).
    routing_keywords: List[str] = Field(default_factory=list)


def default_agents() -> List[AgentSpec]:
    """The initial roster. Extend freely; nothing else must change."""
    return [
        AgentSpec(
            id=AgentId.JARVIS,
            display_name="JARVIS",
            role="Supervisor / orchestrator. Owns the mic + wake word lifecycle.",
            personality=["calm", "sophisticated", "concise", "authoritative"],
            capabilities=["route", "delegate", "summarize", "converse"],
            tools=[],
            routing_keywords=["jarvis", "help", "status", "who"],
        ),
        AgentSpec(
            id=AgentId.FRIDAY,
            display_name="FRIDAY",
            role="Local computer operator (filesystem, terminal, apps).",
            personality=["efficient", "friendly", "supportive"],
            capabilities=["computer-control", "automation"],
            tools=["filesystem", "terminal", "application", "screen", "keyboard", "mouse"],
            routing_keywords=["open", "run", "launch", "type", "click", "computer", "file"],
        ),
        AgentSpec(
            id=AgentId.VISION,
            display_name="VISION",
            role="Analysis and reasoning over data and documents.",
            personality=["analytical", "calm", "precise"],
            capabilities=["analysis", "reasoning"],
            tools=[],
            routing_keywords=["analyze", "explain", "reason", "evaluate", "compare"],
        ),
        AgentSpec(
            id=AgentId.NATASHA,
            display_name="NATASHA",
            role="Communications: email, calendar, messaging (Google via OAuth).",
            personality=["professional", "confident", "concise"],
            capabilities=["email", "calendar", "messaging"],
            tools=["gmail", "calendar"],
            routing_keywords=["email", "gmail", "inbox", "calendar", "reply", "message", "meeting"],
        ),
        AgentSpec(
            id=AgentId.WONG,
            display_name="WONG",
            role="Knowledge base and documentation keeper.",
            personality=["knowledgeable", "calm", "deliberate"],
            capabilities=["knowledge", "retrieval", "documentation"],
            tools=[],
            routing_keywords=["remember", "recall", "note", "document", "knowledge", "look up"],
        ),
        AgentSpec(
            id=AgentId.TONY,
            display_name="TONY",
            role="Developer / engineering: projects, terminal, GitHub.",
            personality=["technical", "confident", "energetic"],
            capabilities=["development", "build", "repo"],
            tools=["filesystem", "terminal", "application"],
            routing_keywords=["project", "code", "build", "git", "github", "repo", "deploy", "test"],
        ),
        AgentSpec(
            id=AgentId.SHURI,
            display_name="SHURI",
            role="Research and web investigation.",
            personality=["intelligent", "curious", "energetic", "analytical"],
            capabilities=["research", "web", "synthesis"],
            tools=["browser"],
            routing_keywords=["research", "find out", "investigate", "latest", "search", "web"],
        ),
        AgentSpec(
            id=AgentId.QUICKSILVER,
            display_name="QUICKSILVER",
            role="Background / scheduled jobs and automations.",
            personality=["concise", "energetic", "fast-paced"],
            capabilities=["scheduling", "background-jobs"],
            tools=[],
            routing_keywords=["schedule", "every", "background", "recurring", "cron", "daily"],
        ),
        AgentSpec(
            id=AgentId.NICK_FURY,
            display_name="NICK FURY",
            role="Security gatekeeper. Intercepts risky actions and requires approval.",
            personality=["serious", "authoritative", "security-focused"],
            capabilities=["security", "approval", "policy"],
            tools=[],
            routing_keywords=["approve", "permission", "security", "danger"],
        ),
        AgentSpec(
            id=AgentId.DR_STRANGE,
            display_name="DR. STRANGE",
            role="Strategy and multi-step planning.",
            personality=["strategic", "calm", "thoughtful"],
            capabilities=["planning", "strategy"],
            tools=[],
            routing_keywords=["plan", "strategy", "roadmap", "steps", "scenario"],
        ),
    ]
