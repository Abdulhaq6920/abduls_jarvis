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

from jarvis_backend.core.llm import FALLBACK_REPLY, EchoLLM, LLMMessage, LLMProvider

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
    ``/assist`` request. Every failure path -- an offline provider, a transport error, the
    provider's own honest fallback sentence, a reply that names no agent or names several --
    degrades to :class:`KeywordRouter`, logs a warning, and records in the PLAN reason which
    path was taken.

    ``EchoLLM`` (the dev/test default) is short-circuited BEFORE any call: it echoes the
    prompt back and could never name an agent, so dev, tests and offline installs keep
    exactly the deterministic keyword behaviour -- including the PLAN reason, which is not
    annotated for that short-circuit. An empty utterance (a bare wake word, a blank STT
    result) skips the call too: there is nothing to classify.

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
            # The dev/test/offline default. No annotation: the pure keyword path is what
            # ran, so the trace reads exactly as it did before this router existed.
            return self._fallback(utterance, agents, None)

        if not text:
            # A bare wake word ("Hey JARVIS") or an empty STT result strips to "". There is
            # nothing to classify, so skip the model call entirely: prompting it with an
            # empty request would route to an arbitrary specialist with an empty task.
            return self._fallback(utterance, agents, "nothing to classify")

        try:
            reply = await self._llm.complete(self._prompt(text, agents))
        except Exception as exc:  # noqa: BLE001 - routing must never crash a request
            # Class only: never the utterance, never provider internals.
            log.warning("llm routing failed", extra={"error": type(exc).__name__})
            return self._fallback(utterance, agents, "language model unavailable")

        if reply.strip() == FALLBACK_REPLY:
            # The shipped provider never raises either: on a transport, status or shape
            # error it logs and returns this exact sentence. Without this check a real
            # outage would be diagnosed as an unusable reply, and the only trace of a down
            # model would be a per-request PLAN reason.
            log.warning("llm routing unavailable: provider returned its offline fallback")
            return self._fallback(utterance, agents, "language model unavailable")

        agent_id = _parse_agent(reply, agents)
        if agent_id is None:
            # Logged without the reply: a model that ignores the instruction can echo the
            # utterance back, and the utterance never reaches the logs. The rate of these
            # lines is the signal that routing is degraded.
            log.warning("llm routing gave no usable agent")
            return self._fallback(utterance, agents, "language model gave no usable agent")
        return RouteDecision(agent_id, False, f"LLM routed to {agent_id.value}.", text)

    def _fallback(self, utterance: str, agents: List[AgentSpec],
                  why: Optional[str]) -> RouteDecision:
        """The keyword decision, annotated with why the model did not make it.

        ``why=None`` keeps the keyword reason verbatim. It is used for the ``EchoLLM``
        short-circuit, where a suffix would sit in the user-visible PLAN reason of every
        request a default or offline install ever makes.
        """
        decision = self._keyword.decide(utterance, agents)
        if why is None:
            return decision
        return RouteDecision(decision.agent_id, decision.explicit,
                             f"{decision.reason} (keyword fallback: {why})", decision.task)

    @staticmethod
    def _prompt(task: str, agents: List[AgentSpec]) -> List[LLMMessage]:
        # The menu is derived from the passed-in specs -- every id and EVERY routing keyword
        # they declare -- so a new agent (or a new keyword on an existing one) is routable
        # without touching this prompt. Truncating the keyword lists would hide lanes the
        # keyword fallback still honours, i.e. make the two layers disagree.
        menu = "\n".join(
            f"{spec.id.value} - {spec.role} - keywords: {', '.join(spec.routing_keywords)}"
            for spec in agents
        )
        return [
            LLMMessage(role="system", content=_ROUTER_SYSTEM),
            LLMMessage(role="user", content=f"Agents:\n{menu}\n\nRequest: {task}\n\nAgent id:"),
        ]


def _parse_agent(reply: str, agents: List[AgentSpec]) -> Optional[AgentId]:
    """Resolve a model reply to exactly one roster agent id, or None.

    The prompt asks for the bare id, so the reply is read in order of decreasing certainty:

    1. the reply STARTS WITH an id -- "VISION", "vision.", "DR_STRANGE", and also
       "VISION - best suited to analyse data" where the model answered then explained;
    2. otherwise exactly ONE distinct roster id appears anywhere -- "I'd route this to
       SHURI", "Agent: NICK FURY";
    3. anything else returns None so the caller degrades to keyword routing.

    Case 3 is the important one. Taking the earliest (or latest) id in a reply that names
    SEVERAL agents silently misroutes and still looks like a success: "The JARVIS router
    would send this to VISION" and "Not TONY, VISION should analyse it" both resolve to the
    wrong agent that way. A reply naming more than one agent is genuinely ambiguous, so we
    refuse to guess and fall back to the deterministic router instead.

    Only ids from the passed-in roster are accepted, so a hallucinated name degrades to the
    fallback rather than raising.
    """
    if not reply or not reply.strip():
        return None

    def _norm(text: str) -> str:
        # Collapse anything that is not A-Z0-9 to single spaces so "Dr. Strange",
        # "nick fury" and "DR_STRANGE" all normalise to the enum spelling.
        return re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()

    # id (normalised, e.g. "DR STRANGE") -> AgentId, longest first so "NICK FURY" is
    # preferred over a bare "FURY"-style prefix when both could match.
    by_id = {_norm(spec.id.value): spec.id for spec in agents}
    ordered = sorted(by_id, key=len, reverse=True)

    # 1. The reply leads with the id (the expected, highest-confidence shape).
    head = _norm(reply.splitlines()[0])
    for candidate in ordered:
        if head == candidate or head.startswith(f"{candidate} "):
            return by_id[candidate]

    # 2. Exactly one distinct roster id anywhere in the reply.
    padded = f" {_norm(reply)} "
    hits = {by_id[c] for c in ordered if re.search(rf"\s{re.escape(c)}\s", padded)}
    if len(hits) == 1:
        return next(iter(hits))

    # 3. Nothing, or several agents named -> ambiguous, let the keyword router decide.
    return None


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
