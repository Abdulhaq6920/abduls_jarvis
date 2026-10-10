"""Request router (replaceable by a LangGraph/LLM router).

JARVIS decides which agent handles a request. :class:`KeywordRouter` is deterministic
and works offline; any object with the same ``route`` method can replace it.

Precedence: (1) an explicit "ask <agent> to ..." wins; (2) otherwise score agents by
keyword; (3) otherwise JARVIS answers itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Protocol

from jarvis_shared.agents import AgentId, AgentSpec

_ALIASES = {
    "jarvis": AgentId.JARVIS, "friday": AgentId.FRIDAY, "vision": AgentId.VISION,
    "natasha": AgentId.NATASHA, "wong": AgentId.WONG, "tony": AgentId.TONY,
    "shuri": AgentId.SHURI, "quicksilver": AgentId.QUICKSILVER,
    "fury": AgentId.NICK_FURY, "nick fury": AgentId.NICK_FURY,
    "strange": AgentId.DR_STRANGE, "doctor strange": AgentId.DR_STRANGE,
    "dr strange": AgentId.DR_STRANGE, "dr. strange": AgentId.DR_STRANGE,
}
_EXPLICIT = re.compile(
    r"\b(?:ask|tell|get|have)\s+(?P<name>nick fury|dr\.? strange|doctor strange|jarvis|friday|"
    r"vision|natasha|wong|tony|shuri|quicksilver|fury|strange)\b\s*(?:to\s+)?(?P<task>.*)$",
    re.IGNORECASE,
)
_WAKE_PREFIX = re.compile(r"^\s*(hey|hi|ok|okay)\s+jarvis[,.!:]?\s*", re.IGNORECASE)


@dataclass(frozen=True)
class RouteDecision:
    agent_id: AgentId
    explicit: bool
    reason: str
    task: str  # the request with the wake phrase / "ask X to" preamble removed


class Router(Protocol):
    def route(self, utterance: str, agents: List[AgentSpec]) -> RouteDecision: ...


def strip_wake_word(utterance: str) -> str:
    return _WAKE_PREFIX.sub("", utterance).strip()


def _has_word(text: str, keyword: str) -> bool:
    """Whole-word match that tolerates simple endings ("email" matches "emails")."""
    return re.search(rf"\b{re.escape(keyword)}(?:s|es|ing|ed)?\b", text) is not None


class KeywordRouter:
    def route(self, utterance: str, agents: List[AgentSpec]) -> RouteDecision:
        text = strip_wake_word(utterance)

        match = _EXPLICIT.search(text)
        if match:
            agent_id = _ALIASES.get(match.group("name").lower(), AgentId.JARVIS)
            task = match.group("task").strip() or text
            return RouteDecision(agent_id, True, f"User explicitly named {agent_id.value}.", task)

        lowered = text.lower()
        best: Optional[AgentSpec] = None
        best_score = 0
        for spec in agents:
            if spec.id == AgentId.JARVIS:
                continue
            score = sum(1 for kw in spec.routing_keywords if _has_word(lowered, kw))
            if score > best_score:
                best, best_score = spec, score

        if best is not None:
            return RouteDecision(best.id, False,
                                 f"Matched {best_score} keyword(s) for {best.id.value}.", text)
        return RouteDecision(AgentId.JARVIS, False,
                             "No specialist matched; JARVIS answers directly.", text)
