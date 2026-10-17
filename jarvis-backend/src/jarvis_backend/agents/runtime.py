"""Agent behaviour.

Each agent turns a task into (a) text to speak and (b) optional :class:`ToolRequest`s
that must clear the policy gate before the local runtime runs them. Agents receive only
the text/context they need -- they never touch the microphone or wake word.

Honesty rule: an agent must never claim work it did not do. Where an integration (web
search, Gmail, an LLM) is not connected yet, the agent says so. The seams for those
integrations are the ``# SEAM`` markers below.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

from jarvis_shared.agents import AgentId, AgentSpec
from jarvis_shared.security import RiskLevel, ToolRequest

from jarvis_backend.core.llm import LLMMessage, LLMProvider
from jarvis_backend.storage.memory import MemoryFull, MemoryStore


@dataclass
class AgentOutcome:
    speech: str
    tool_requests: List[ToolRequest] = field(default_factory=list)


@dataclass
class AgentServices:
    """Everything an agent may use. Passed in, so agents stay easy to test."""

    memory: MemoryStore
    llm: LLMProvider


class BaseAgent:
    def __init__(self, spec: AgentSpec, services: AgentServices) -> None:
        self.spec = spec
        self.services = services

    @property
    def id(self) -> AgentId:
        return self.spec.id

    async def handle(self, task: str, context: Dict[str, Any]) -> AgentOutcome:
        raise NotImplementedError


def _topic_after(task: str, verbs: List[str]) -> str:
    text = task.strip()
    for verb in verbs:
        idx = text.lower().find(verb)
        if idx != -1:
            text = text[idx + len(verb):].strip()
            break
    text = re.sub(r"^(?:about|on|into)\s+", "", text, flags=re.IGNORECASE)
    text = text.removeprefix("the ").strip().rstrip(".")
    return text or "that topic"


class JarvisAgent(BaseAgent):
    async def handle(self, task: str, context: Dict[str, Any]) -> AgentOutcome:
        low = task.strip().lower()
        if low in {"", "yes", "hello", "hi", "hey"}:
            return AgentOutcome("Yes. I'm listening.")
        if "status" in low or "who are you" in low:
            return AgentOutcome("I'm JARVIS. I route your requests to SHURI, TONY, NATASHA and the "
                                "rest of the team, and I keep every action behind an approval gate.")
        return AgentOutcome("I'm not sure which specialist should take that. Try naming one, for "
                            "example: ask SHURI to research a topic, or ask TONY to check my project.")


class ShuriAgent(BaseAgent):
    async def handle(self, task: str, context: Dict[str, Any]) -> AgentOutcome:
        topic = _topic_after(task, ["research", "investigate", "find out", "look into"])
        # SEAM: call a web-search tool + LLM synthesis here and speak the real findings.
        return AgentOutcome(f"I'd research {topic}, but web search isn't connected yet, so I "
                            f"have no findings to report.")


class TonyAgent(BaseAgent):
    async def handle(self, task: str, context: Dict[str, Any]) -> AgentOutcome:
        allowed = context.get("allowed_paths") or ["."]
        project = str(allowed[0])
        request = ToolRequest(
            request_id=str(uuid.uuid4()), agent_id=self.id.value, category="terminal",
            action="run_command", args={"command": "git status --short", "cwd": project},
            declared_risk=RiskLevel.SAFE, allowed_paths=[str(p) for p in allowed],
            reason="Inspect the approved project directory.",
        )
        return AgentOutcome("Checking the repository status.", [request])


class NatashaAgent(BaseAgent):
    async def handle(self, task: str, context: Dict[str, Any]) -> AgentOutcome:
        if re.search(r"\b(send|reply|respond|compose)\b", task.lower()):
            request = ToolRequest(
                request_id=str(uuid.uuid4()), agent_id=self.id.value, category="gmail",
                action="send_email", args={"intent": task[:500]},
                declared_risk=RiskLevel.RESTRICTED, requires_approval=True,
                reason="Outbound email on the user's behalf.",
            )
            return AgentOutcome("I've prepared the reply. This action requires your approval "
                                "before I send it.", [request])
        # SEAM: read the inbox through the Google OAuth integration.
        return AgentOutcome("Gmail isn't connected yet, so I can't read your inbox. Once Google "
                            "sign-in is set up I'll be able to summarise it.")


class FridayAgent(BaseAgent):
    _OPEN = re.compile(r"\b(?:open|launch|start)\s+(?P<app>[a-z0-9][a-z0-9 _-]{0,30})", re.I)

    async def handle(self, task: str, context: Dict[str, Any]) -> AgentOutcome:
        match = self._OPEN.search(task)
        if match:
            app = match.group("app").strip().split()[0].lower()
            request = ToolRequest(
                request_id=str(uuid.uuid4()), agent_id=self.id.value, category="application",
                action="open_app", args={"app": app}, declared_risk=RiskLevel.RESTRICTED,
                allowed_apps=[str(a) for a in context.get("allowed_apps") or []],
                reason=f"Launch {app}.",
            )
            return AgentOutcome(f"I'll open {app} once you approve it.", [request])
        return AgentOutcome("I can open allow-listed applications. Try: ask FRIDAY to open notepad.")


class WongAgent(BaseAgent):
    """Keeper of long-term memory: remembers facts and recalls them later."""

    _REMEMBER = re.compile(r"^(?:please\s+)?(?:remember|note|save|store)(?:\s+that)?\s+(?P<fact>.+)$",
                           re.IGNORECASE | re.DOTALL)
    _RECALL = re.compile(r"(?:what do you (?:remember|know)|do you remember|recall)\s*(?:about|that)?\s*(?P<q>.*)",
                         re.IGNORECASE)

    async def handle(self, task: str, context: Dict[str, Any]) -> AgentOutcome:
        text = task.strip().rstrip(".")
        remember = self._REMEMBER.match(text)
        if remember:
            fact = remember.group("fact").strip()
            try:
                await self.services.memory.add(fact, kind="fact", agent_id=self.id.value, source="voice")
            except MemoryFull as exc:
                return AgentOutcome(str(exc))
            return AgentOutcome(f"Noted. I'll remember that {fact[:160]}.")

        recall = self._RECALL.search(text)
        if recall:
            query = recall.group("q").strip() or text
            hits = await self.services.memory.search(query, limit=3)
            if not hits:
                return AgentOutcome("I don't have anything stored about that.")
            lead = f"You told me: {hits[0].text[:200]}."
            extra = f" I have {len(hits) - 1} more related note(s)." if len(hits) > 1 else ""
            return AgentOutcome(lead + extra)

        return AgentOutcome("I keep your long-term notes. Say 'remember that' followed by a fact, "
                            "or ask what I remember about a topic.")


class LLMAgent(BaseAgent):
    """Pure-reasoning agents (VISION / DR_STRANGE / QUICKSILVER / NICK_FURY).

    They answer by asking the configured :class:`LLMProvider` to reason, speaking only
    text. They NEVER emit a ``ToolRequest``: the LLM only produces words, so it can never
    bypass the policy gate or the local validator. With the default ``EchoLLM`` the answer
    is the deterministic echo, which keeps dev and tests offline and reproducible.
    """

    # A one-line intent hint folded into the system prompt per persona.
    _INTENT = {
        AgentId.VISION: "Analyse what the user shares and reason it through clearly.",
        AgentId.DR_STRANGE: "Lay out a concise, ordered plan or strategy.",
        AgentId.QUICKSILVER: "Reason about the background/scheduled work the user describes; "
                             "you cannot actually create jobs yet, so describe the plan, do not "
                             "claim you scheduled anything.",
        AgentId.NICK_FURY: "Reason about the security or risk angle; you cannot grant or deny "
                           "approvals yourself, so advise, do not claim to have approved anything.",
    }

    async def handle(self, task: str, context: Dict[str, Any]) -> AgentOutcome:
        # SEAM (now wired): reason with the configured LLMProvider. Reasoning only -- no
        # ToolRequests, so the two security gates are never bypassed.
        messages = [LLMMessage(role="system", content=self._system_prompt(context))]
        messages.append(LLMMessage(role="user", content=task.strip()))
        reply = await self.services.llm.complete(messages)
        return AgentOutcome(reply.strip())

    def _system_prompt(self, context: Dict[str, Any]) -> str:
        personality = ", ".join(self.spec.personality) or "helpful"
        lines = [
            f"You are {self.spec.display_name}, a {personality} assistant.",
            f"Your role: {self.spec.role}",
            self._INTENT.get(self.id, "Help the user by reasoning carefully."),
            "Keep answers concise and spoken-friendly (a few sentences, no markdown).",
            "Never claim to have taken an external action you cannot perform; only reason "
            "and explain.",
        ]
        lines.extend(self._context_lines(context))
        return "\n".join(lines)

    @staticmethod
    def _context_lines(context: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        paths = context.get("allowed_paths") or []
        if paths:
            out.append("Allowed project paths: " + ", ".join(str(p) for p in paths[:5]) + ".")
        apps = context.get("allowed_apps") or []
        if apps:
            out.append("Allowed applications: " + ", ".join(str(a) for a in apps[:10]) + ".")
        memories = context.get("memories") or []
        if memories:
            out.append("Relevant notes you remember: "
                       + " | ".join(str(m)[:160] for m in memories[:3]) + ".")
        history = context.get("history") or []
        if history:
            recent = history[-3:]
            turns = "; ".join(
                f"user said '{str(t.get('user', ''))[:80]}', you replied '{str(t.get('reply', ''))[:80]}'"
                for t in recent
            )
            out.append("Recent conversation: " + turns + ".")
        return out


_AGENT_CLASSES = {
    AgentId.JARVIS: JarvisAgent, AgentId.SHURI: ShuriAgent, AgentId.TONY: TonyAgent,
    AgentId.NATASHA: NatashaAgent, AgentId.FRIDAY: FridayAgent, AgentId.WONG: WongAgent,
}


def build_agent(spec: AgentSpec, services: AgentServices) -> BaseAgent:
    return _AGENT_CLASSES.get(spec.id, LLMAgent)(spec, services)
