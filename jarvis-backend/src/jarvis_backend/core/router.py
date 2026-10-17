"""Request router.

JARVIS decides which agent handles a request. Two implementations ship:

* :class:`KeywordRouter` -- deterministic, offline, no I/O. Fast and predictable, but it
  only knows the exact ``routing_keywords`` of each spec, so synonyms ("analyse" vs
  "analyze"), paraphrases and spelling variants slip through to JARVIS.
* :class:`LLMRouter` -- asks the configured :class:`~jarvis_backend.core.llm.LLMProvider`
  to pick the best agent for a natural phrasing, so the user never has to say
  "ask TONY to ...". It wraps a ``KeywordRouter`` and falls back to it whenever the model
  is offline, unavailable or unsure.

Precedence is the same for both, and the fallback chain is strict:

    explicit "ask <agent> to ..."  ->  LLM classification  ->  keyword scoring  ->  JARVIS

An explicitly named agent ALWAYS wins; the LLM never overrides it. ``route`` is async so a
model-backed router fits the Protocol; ``KeywordRouter.decide`` keeps the synchronous,
deterministic core available on its own (it is also the LLM router's fallback).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Protocol

from jarvis_shared.agents import AgentId, AgentSpec
from jarvis_shared.log import get_logger

from jarvis_backend.core.llm import EchoLLM, LLMMessage, LLMProvider

if TYPE_CHECKING:
    from jarvis_backend.config import Settings

log = get_logger(__name__)

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

# How many keywords per agent go into the LLM's menu -- enough to describe the lane, short
# enough to keep the prompt small and the decision fast.
_MENU_KEYWORDS = 6
_ROUTER_SYSTEM = (
    "You are the request router of the JARVIS assistant. Pick the ONE agent best suited to "
    "handle the user's request. Answer with that agent's id and nothing else. If no "
    "specialist fits, or it is small talk or a general question, answer JARVIS."
)


@dataclass(frozen=True)
class RouteDecision:
    agent_id: AgentId
    explicit: bool
    reason: str
    task: str  # the request with the wake phrase / "ask X to" preamble removed


class Router(Protocol):
    async def route(self, utterance: str, agents: List[AgentSpec]) -> RouteDecision: ...


def strip_wake_word(utterance: str) -> str:
    return _WAKE_PREFIX.sub("", utterance).strip()


def match_explicit(text: str) -> Optional[RouteDecision]:
    """The "ask/tell/get/have <agent> to <task>" decision, or None.

    Shared by both routers: an explicitly named agent is a user instruction, not a guess,
    so it is resolved identically and never handed to the model.
    """
    match = _EXPLICIT.search(text)
    if not match:
        return None
    agent_id = _ALIASES.get(match.group("name").lower(), AgentId.JARVIS)
    task = match.group("task").strip() or text
    return RouteDecision(agent_id, True, f"User explicitly named {agent_id.value}.", task)


def _has_word(text: str, keyword: str) -> bool:
    """Whole-word match that tolerates simple endings ("email" matches "emails")."""
    return re.search(rf"\b{re.escape(keyword)}(?:s|es|ing|ed)?\b", text) is not None


class KeywordRouter:
    """Deterministic offline router: explicit name, then keyword score, then JARVIS."""

    async def route(self, utterance: str, agents: List[AgentSpec]) -> RouteDecision:
        return self.decide(utterance, agents)

    def decide(self, utterance: str, agents: List[AgentSpec]) -> RouteDecision:
        """The synchronous core. No I/O, so it is also :class:`LLMRouter`'s fallback."""
        text = strip_wake_word(utterance)

        explicit = match_explicit(text)
        if explicit:
            return explicit

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


class LLMRouter:
    """Routes natural phrasings with the configured LLM, keyword routing as the safety net.

    Error policy mirrors :mod:`jarvis_backend.core.llm`: ``route`` NEVER raises.
    ``Supervisor._plan`` has no try/except around it, so a raised exception would 500 the
    ``/assist`` request. Every failure path -- an offline provider, a transport error, a
    reply that names no known agent -- degrades to :class:`KeywordRouter`, and the reason
    recorded in the PLAN stage says which path was taken.

    ``EchoLLM`` (the dev/test default) is short-circuited BEFORE any call: it echoes the
    prompt back and could never name an agent, so dev, tests and offline installs keep
    exactly the deterministic keyword behaviour.

    Privacy: the utterance is sent to the configured provider (that is the point) but is
    never logged. Failures log the exception class only.
    """

    def __init__(self, llm: LLMProvider, fallback: Optional[KeywordRouter] = None) -> None:
        self._llm = llm
        self._keyword = fallback or KeywordRouter()

    async def route(self, utterance: str, agents: List[AgentSpec]) -> RouteDecision:
        text = strip_wake_word(utterance)

        explicit = match_explicit(text)
        if explicit:  # a named agent is an instruction; the model never overrides it
            return explicit

        if isinstance(self._llm, EchoLLM):
            return self._fallback(utterance, agents, "no language model configured")

        try:
            reply = await self._llm.complete(self._prompt(text, agents))
        except Exception as exc:  # noqa: BLE001 - routing must never crash a request
            # Class only: never the utterance, never provider internals.
            log.warning("llm routing failed", extra={"error": type(exc).__name__})
            return self._fallback(utterance, agents, "language model unavailable")

        agent_id = _parse_agent(reply, agents)
        if agent_id is None:
            return self._fallback(utterance, agents, "language model gave no usable agent")
        return RouteDecision(agent_id, False, f"LLM routed to {agent_id.value}.", text)

    def _fallback(self, utterance: str, agents: List[AgentSpec], why: str) -> RouteDecision:
        decision = self._keyword.decide(utterance, agents)
        return RouteDecision(decision.agent_id, decision.explicit,
                             f"{decision.reason} (keyword fallback: {why})", decision.task)

    @staticmethod
    def _prompt(task: str, agents: List[AgentSpec]) -> List[LLMMessage]:
        # The menu is derived from the passed-in specs, so a newly registered agent is
        # routable without touching this prompt.
        menu = "\n".join(
            f"{spec.id.value} - {spec.role} - keywords: "
            f"{', '.join(spec.routing_keywords[:_MENU_KEYWORDS])}"
            for spec in agents
        )
        return [
            LLMMessage(role="system", content=_ROUTER_SYSTEM),
            LLMMessage(role="user", content=f"Agents:\n{menu}\n\nRequest: {task}\n\nAgent id:"),
        ]


def _parse_agent(reply: str, agents: List[AgentSpec]) -> Optional[AgentId]:
    """Find the first known agent id in a model reply, or None.

    Tolerant on purpose: a small model answers "VISION", "vision.", "Agent: NICK FURY" or
    "I'd route this to SHURI". Only ids from the passed-in roster are accepted, so a
    hallucinated name degrades to the keyword fallback instead of a KeyError.
    """
    if not reply or not reply.strip():
        return None
    # Collapse punctuation to single spaces (and pad the ends) so a whole-token match is a
    # plain " TOKEN " search: "Dr. Strange", "nick fury" and "DR_STRANGE" all normalise to
    # the enum spelling, while "ULTRONS" or "VISIONARY" cannot match.
    normalised = f" {re.sub(r'[^A-Z0-9]+', ' ', reply.upper()).strip()} "
    found: Optional[tuple[int, AgentId]] = None
    for spec in agents:
        token = r"\s+".join(re.escape(part) for part in spec.id.value.split("_"))
        hit = re.search(rf"\s{token}\s", normalised)
        if hit and (found is None or hit.start() < found[0]):
            found = (hit.start(), spec.id)
    return found[1] if found else None


def build_router(settings: Settings, llm: LLMProvider) -> Router:
    """Return the configured router.

    ``JARVIS_ROUTER=keyword`` pins the deterministic router. The default ``"llm"`` enables
    natural-language routing, but because the default provider is ``EchoLLM`` the LLM
    router short-circuits to keyword routing until a real model is configured -- so the
    out-of-the-box behaviour is unchanged.
    """
    if settings.router == "keyword":
        return KeywordRouter()
    return LLMRouter(llm, KeywordRouter())
