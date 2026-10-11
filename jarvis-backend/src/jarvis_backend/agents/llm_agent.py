"""An agent whose reasoning is done by a language model, inside hard limits.

The loop: ask the model -> if it calls tools, run them (internal ones now, device ones become
requests for the policy gate) -> give it the results -> repeat until it answers. It always ends:
by a final answer, the step limit, the time budget, or an error that falls back to the
rule-based agent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from jarvis_shared.agents import AgentId, AgentSpec
from jarvis_shared.log import get_logger
from jarvis_shared.security import ToolRequest

from jarvis_backend.agents.prompts import AGENT_ROLE, system_prompt, wrap_untrusted
from jarvis_backend.agents.runtime import (
    AgentOutcome,
    AgentServices,
    BaseAgent,
    RunState,
    UnconnectedAgent,
    WongAgent,
)
from jarvis_backend.agents.tools import AgentTool, build_tool_request, tools_for, validate_arguments
from jarvis_backend.llm.types import ChatMessage, LLMError, ToolCall
from jarvis_backend.storage.memory import MemoryFull

log = get_logger(__name__)

_MAX_CALLS_PER_STEP = 3
_MAX_TOOL_RESULT = 1500
_MAX_SPEECH = 600
_GREETING = {"", "yes", "hello", "hi", "hey"}
_MARKDOWN = re.compile(r"[*_`#>~]|^\s*[-•]\s+", re.MULTILINE)


@dataclass
class _Run:
    """Mutable results of one agent run."""

    requests: List[ToolRequest] = field(default_factory=list)
    hops: List[Tuple[str, str]] = field(default_factory=list)
    tokens: int = 0


def speakable(text: str, limit: int = _MAX_SPEECH) -> str:
    """Strip markdown/markup and trim at a sentence boundary: the reply is read aloud."""
    text = _MARKDOWN.sub("", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > limit // 2 else cut.rstrip() + "...").strip()


class LLMAgent(BaseAgent):
    def __init__(self, spec: AgentSpec, services: AgentServices, fallback: BaseAgent) -> None:
        super().__init__(spec, services)
        self.fallback = fallback
        self._tools = {t.spec.name: t for t in tools_for(spec.id)}

    # ------------------------------------------------------------------ entry points
    async def handle(self, task: str, context: Dict[str, Any]) -> AgentOutcome:
        return await self.run(task, context, RunState.start(self.services.limits.budget_seconds))

    async def run(self, task: str, context: Dict[str, Any], state: RunState) -> AgentOutcome:
        if self._use_rules(task):
            return await self.fallback.handle(task, context)
        try:
            return await self._loop(task, context, state)
        except LLMError as exc:
            log.warning("agent fell back to rules", extra={"agent": self.id.value, "reason": exc.kind})
            return await self._degraded(task, context, exc.spoken)
        except Exception:  # a bug here must never take the request down
            log.exception("agent loop crashed", extra={"agent": self.id.value})
            return await self._degraded(task, context, "something went wrong inside my language model")

    def _use_rules(self, task: str) -> bool:
        """Deterministic, cheaper and safer than a model for these exact cases."""
        if isinstance(self.fallback, WongAgent):
            return WongAgent._REMEMBER.match(task.strip().rstrip(".")) is not None
        if self.id == AgentId.JARVIS:
            return task.strip().lower() in _GREETING
        return False

    async def _degraded(self, task: str, context: Dict[str, Any], reason: str) -> AgentOutcome:
        outcome = await self.fallback.handle(task, context)
        if isinstance(self.fallback, UnconnectedAgent):
            outcome.speech = f"I can't do that right now because {reason}."
        else:
            outcome.speech = f"Basic mode, because {reason}. {outcome.speech}"
        return outcome

    # ------------------------------------------------------------------------ the loop
    async def _loop(self, task: str, context: Dict[str, Any], state: RunState) -> AgentOutcome:
        llm, limits = self.services.llm, self.services.limits
        assert llm is not None
        tools = self._available_tools(state)
        messages = self._opening_messages(task, context, tools)
        run = _Run()
        final = ""

        for _ in range(limits.max_steps):
            if state.remaining <= 1:
                break
            response = await llm.complete(
                role=AGENT_ROLE.get(self.id, "default"), messages=messages, tools=[t.spec for t in tools],
                max_tokens=limits.max_tokens, temperature=0.2 if AGENT_ROLE.get(self.id) == "reasoning" else 0.3,
                timeout=max(5.0, state.remaining), agent=self.id.value)
            run.tokens += response.usage.total
            if not response.tool_calls:
                final = response.content
                break

            messages.append(ChatMessage("assistant", response.content, response.tool_calls))
            calls = response.tool_calls
            for call in calls[:_MAX_CALLS_PER_STEP]:
                result = await self._run_tool(call, task, context, state, run)
                messages.append(ChatMessage("tool", result[:_MAX_TOOL_RESULT], tool_call_id=call.id))
            for call in calls[_MAX_CALLS_PER_STEP:]:  # keep the transcript well-formed
                messages.append(ChatMessage("tool", "Skipped: too many tool calls in one step.", tool_call_id=call.id))

        if not final:
            final = await self._final_answer(messages, run, state)
        speech = speakable(final) or ("I've sent that request." if run.requests else "I'm not sure how to help with that.")
        return AgentOutcome(speech, run.requests, run.hops, run.tokens)

    async def _final_answer(self, messages: List[ChatMessage], run: _Run, state: RunState) -> str:
        """The loop ended without a spoken answer: ask once more, with tools switched off."""
        llm = self.services.llm
        assert llm is not None
        messages.append(ChatMessage("user", "Now give your final spoken answer in one or two short sentences. Do not use tools."))
        try:
            response = await llm.complete(
                role=AGENT_ROLE.get(self.id, "default"), messages=messages, tools=(),
                max_tokens=min(300, self.services.limits.max_tokens), timeout=max(5.0, state.remaining),
                agent=self.id.value)
        except LLMError:
            if run.requests:
                return ""  # actions are queued; the caller supplies a safe default sentence
            raise
        run.tokens += response.usage.total
        return response.content

    def _available_tools(self, state: RunState) -> List[AgentTool]:
        limits = self.services.limits
        can_delegate = (self.services.delegate is not None and state.depth < limits.max_depth
                        and state.counters["delegations"] < limits.max_delegations)
        return [t for t in self._tools.values() if t.spec.name != "delegate" or can_delegate]

    def _opening_messages(self, task: str, context: Dict[str, Any], tools: List[AgentTool]) -> List[ChatMessage]:
        prompt = system_prompt(self.spec, memories=context.get("memories") or [], context=context,
                               tool_names=[t.spec.name for t in tools])
        messages = [ChatMessage("system", prompt)]
        for turn in (context.get("history") or [])[-4:]:  # oldest first; keeps the conversation coherent
            if turn.get("user") and turn.get("reply"):
                messages.append(ChatMessage("user", str(turn["user"])[:400]))
                messages.append(ChatMessage("assistant", str(turn["reply"])[:400]))
        messages.append(ChatMessage("user", task.strip()[:2000]))
        return messages

    # ---------------------------------------------------------------------- tool calls
    async def _run_tool(self, call: ToolCall, task: str, context: Dict[str, Any], state: RunState, run: _Run) -> str:
        tool = self._tools.get(call.name)
        if tool is None:
            return f"Error: you do not have a tool named '{call.name}'. Your tools: {', '.join(self._tools) or 'none'}."
        if not call.valid:
            return "Error: tool arguments were not a valid JSON object. Try again."
        problem = validate_arguments(tool.spec.parameters, call.arguments)
        if problem:
            return f"Error: {problem}."

        if tool.kind == "device":
            if len(run.requests) >= self.services.limits.max_device_requests:
                return "Error: too many device actions requested in one turn."
            run.requests.append(build_tool_request(tool, call.arguments, self.id, context))
            log.info("device action requested", extra={"agent": self.id.value, "tool": tool.spec.name})
            return ("Queued. It will run on the user's PC only after a security check (and approval if needed). "
                    "You will not see its output.")

        if call.name == "recall":
            hits = await self.services.memory.search(call.arguments["query"], limit=5)
            if not hits:
                return "No matching memories."
            return wrap_untrusted("\n".join(f"- {h.text[:300]}" for h in hits))
        if call.name == "remember":
            try:
                await self.services.memory.add(call.arguments["fact"], kind="fact", agent_id=self.id.value, source="agent")
            except MemoryFull as exc:
                return f"Error: {exc}"
            return "Saved."
        if call.name == "delegate":
            return await self._delegate(call.arguments, context, state, run)
        return "Error: tool not implemented."

    async def _delegate(self, args: Dict[str, Any], context: Dict[str, Any], state: RunState, run: _Run) -> str:
        limits, delegate = self.services.limits, self.services.delegate
        target = AgentId(args["agent"])
        if delegate is None or state.depth >= limits.max_depth:
            return "Error: delegation is not available here."
        if state.counters["delegations"] >= limits.max_delegations:
            return "Error: you have already delegated the maximum number of times. Answer with what you have."
        if target is self.id or target.value in state.trail:
            return "Error: that agent is already part of this hand-off chain. Answer yourself."
        state.counters["delegations"] += 1
        outcome = await delegate(target, args["task"], context, state.child(self.id))
        run.requests.extend(outcome.tool_requests)
        run.hops.append((target.value, f"{self.spec.display_name} delegated to {target.value}."))
        run.hops.extend(outcome.hops)
        run.tokens += outcome.tokens
        return wrap_untrusted(f"{target.value} answered: {outcome.speech}")


__all__ = ["LLMAgent", "speakable"]
