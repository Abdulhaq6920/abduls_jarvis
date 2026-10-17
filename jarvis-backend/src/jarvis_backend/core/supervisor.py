"""The JARVIS supervisor.

Runs every request through seven explicit stages and records a trace of each, so
nothing collapses into one uncontrolled LLM call:

    OBSERVE  ingest the utterance + conversation context
    PLAN     route to an agent (or fall back to JARVIS if it is disabled)
    PROPOSE  the agent returns speech and optional tool requests
    APPROVE  policy gate: allow, hold for human approval, or block
    EXECUTE  allowed requests are handed to the LOCAL runtime (the security boundary)
    VERIFY   sanity-check the outcome
    REPORT   assemble the spoken response, persist the turn, publish live updates

The supervisor never executes tools itself; it only decides.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from jarvis_shared.agents import AgentId
from jarvis_shared.log import get_logger
from jarvis_shared.messages import AssistRequest, AssistResponse, StageTrace
from jarvis_shared.security import ToolRequest
from jarvis_shared.supervisor import SupervisorStage

from jarvis_backend.agents.registry import AgentConfig, AgentRegistry
from jarvis_backend.agents.runtime import AgentOutcome, AgentServices, build_agent
from jarvis_backend.core.llm import LLMProvider
from jarvis_backend.core.policy import PolicyEngine, describe_request
from jarvis_backend.core.router import RouteDecision, Router
from jarvis_backend.events import EventBus
from jarvis_backend.storage import ApprovalStore, AuditLog, MemoryStore, TurnStore

log = get_logger(__name__)
_MAX_CONTEXT_ITEMS = 10


class Supervisor:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        config: AgentConfig,
        router: Router,
        policy: PolicyEngine,
        approvals: ApprovalStore,
        audit: AuditLog,
        turns: TurnStore,
        memory: MemoryStore,
        bus: EventBus,
        llm: LLMProvider,
        history_turns: int = 6,
    ) -> None:
        self.registry, self.config, self.router, self.policy = registry, config, router, policy
        self.approvals, self.audit, self.turns = approvals, audit, turns
        self.memory, self.bus = memory, bus
        self._history_turns = history_turns
        self._services = AgentServices(memory=memory, llm=llm)

    async def handle(self, req: AssistRequest) -> AssistResponse:
        started = time.perf_counter()
        trace: List[StageTrace] = []

        def stage(name: SupervisorStage, detail: str = "", agent: Optional[AgentId] = None) -> None:
            trace.append(StageTrace(stage=name, detail=detail, agent_id=agent))

        # OBSERVE ---------------------------------------------------------------
        context = await self._build_context(req)
        stage(SupervisorStage.OBSERVE, f"{len(req.utterance)} chars, "
                                       f"{len(context['history'])} prior turn(s)")
        await self.audit.record("observe", session_id=req.session_id,
                                utterance_chars=len(req.utterance),
                                woke_from_wake_word=req.woke_from_wake_word)

        # PLAN ------------------------------------------------------------------
        decision, fallback_note = await self._plan(req)
        stage(SupervisorStage.PLAN, decision.reason, decision.agent_id)
        await self.audit.record("plan", agent_id=decision.agent_id.value,
                                session_id=req.session_id, reason=decision.reason)

        # PROPOSE ---------------------------------------------------------------
        spec = self.registry.get(decision.agent_id)
        agent = build_agent(spec, self._services)
        outcome: AgentOutcome = await agent.handle(decision.task, context)
        stage(SupervisorStage.PROPOSE, f"{len(outcome.tool_requests)} tool request(s)", decision.agent_id)

        # APPROVE ---------------------------------------------------------------
        allowed, pending_id, blocked = await self._gate(outcome.tool_requests, req, decision, stage)

        # EXECUTE ---------------------------------------------------------------
        stage(SupervisorStage.EXECUTE, f"{len(allowed)} request(s) dispatched to the local runtime",
              decision.agent_id)

        # VERIFY ----------------------------------------------------------------
        stage(SupervisorStage.VERIFY, "blocked by policy" if blocked else "ok", decision.agent_id)

        # REPORT ----------------------------------------------------------------
        speech = self._compose_speech(outcome.speech, fallback_note, blocked, pending_id)
        stage(SupervisorStage.REPORT, "response prepared", decision.agent_id)
        latency_ms = int((time.perf_counter() - started) * 1000)

        response = AssistResponse(
            session_id=req.session_id, responding_agent=decision.agent_id, speech=speech,
            keep_listening=True, trace=trace, pending_approval_id=pending_id,
            delegation_note=self._delegation_note(decision, spec.display_name),
            tool_requests=allowed,
        )
        await self._persist_and_publish(req, response, latency_ms)
        log.info("assist handled", extra={"agent": decision.agent_id.value, "latency_ms": latency_ms,
                                          "tools": len(allowed), "gated": pending_id is not None,
                                          "blocked": blocked})
        return response

    # ------------------------------------------------------------------ stages
    async def _build_context(self, req: AssistRequest) -> Dict[str, Any]:
        raw = req.client_context or {}
        history = await self.turns.history(req.session_id, self._history_turns)
        memories = await self.memory.search(req.utterance, limit=3)
        return {
            # Only well-typed, bounded values from the (untrusted) client reach agents.
            "allowed_paths": [str(p) for p in (raw.get("allowed_paths") or [])[:_MAX_CONTEXT_ITEMS]],
            "allowed_apps": [str(a) for a in (raw.get("allowed_apps") or [])[:_MAX_CONTEXT_ITEMS]],
            "history": [{"user": t.utterance, "agent": t.agent_id, "reply": t.speech} for t in history],
            "memories": [m.text for m in memories],
        }

    async def _plan(self, req: AssistRequest) -> tuple[RouteDecision, Optional[str]]:
        decision = self.router.route(req.utterance, self.registry.all())
        if await self.config.is_enabled(decision.agent_id):
            return decision, None
        name = self.registry.get(decision.agent_id).display_name
        fallback = RouteDecision(AgentId.JARVIS, False,
                                 f"{decision.agent_id.value} is disabled; JARVIS answers.", decision.task)
        return fallback, f"{name} is currently disabled."

    async def _gate(self, requests: List[ToolRequest], req: AssistRequest, decision: RouteDecision,
                    stage) -> tuple[List[ToolRequest], Optional[str], bool]:
        allowed: List[ToolRequest] = []
        pending_id: Optional[str] = None
        blocked = False

        if not requests:
            # Still pass through the gate explicitly so the trace is complete.
            stage(SupervisorStage.APPROVE, "No actions to gate.", decision.agent_id)

        for tool in requests:
            verdict = self.policy.evaluate(tool)
            await self.audit.record("policy", agent_id=tool.agent_id, session_id=req.session_id,
                                    action=tool.action, category=tool.category,
                                    risk=verdict.risk.value, allow=verdict.allow,
                                    needs_approval=verdict.requires_approval)
            if verdict.requires_approval:
                approval = await self.approvals.create(
                    tool.agent_id, describe_request(tool), verdict.risk.value, tool.model_dump(mode="json"))
                pending_id = approval.id
                await self.audit.record("approval_requested", agent_id=tool.agent_id,
                                        session_id=req.session_id, approval_id=approval.id)
                await self.bus.publish({"type": "approval", "id": approval.id, "status": "PENDING"})
                stage(SupervisorStage.APPROVE, f"Approval required ({approval.id[:8]})", AgentId.NICK_FURY)
            elif not verdict.allow:
                blocked = True
                stage(SupervisorStage.APPROVE, f"BLOCKED: {verdict.reason}", AgentId.NICK_FURY)
            else:
                allowed.append(tool)
                stage(SupervisorStage.APPROVE, f"Auto-approved {tool.action} ({verdict.risk.value})",
                      decision.agent_id)
        return allowed, pending_id, blocked

    @staticmethod
    def _compose_speech(speech: str, fallback_note: Optional[str], blocked: bool,
                        pending_id: Optional[str]) -> str:
        if fallback_note:
            speech = f"{fallback_note} {speech}"
        if blocked:
            speech = f"I can't do that. It is blocked by security policy. {speech}"
        if pending_id and "approval" not in speech.lower():
            speech = f"{speech} This action requires your approval."
        return speech

    @staticmethod
    def _delegation_note(decision: RouteDecision, display_name: str) -> Optional[str]:
        # Announce a hand-off only when the user explicitly asked for a specialist.
        if decision.agent_id == AgentId.JARVIS or not decision.explicit:
            return None
        return f"{display_name} will handle that."

    async def _persist_and_publish(self, req: AssistRequest, resp: AssistResponse, latency_ms: int) -> None:
        await self.turns.add(
            session_id=req.session_id, agent_id=resp.responding_agent.value, utterance=req.utterance,
            speech=resp.speech, latency_ms=latency_ms,
            trace=[t.model_dump(mode="json") for t in resp.trace],
            pending_approval_id=resp.pending_approval_id,
        )
        await self.audit.record("report", agent_id=resp.responding_agent.value,
                                session_id=req.session_id, latency_ms=latency_ms,
                                approval_id=resp.pending_approval_id)
        chain: List[str] = []
        for t in resp.trace:
            if t.agent_id and t.agent_id.value not in chain:
                chain.append(t.agent_id.value)
        await self.bus.publish({
            "type": "presence", "chain": chain, "responding_agent": resp.responding_agent.value,
            "speech": resp.speech, "pending_approval_id": resp.pending_approval_id,
            "latency_ms": latency_ms,
        })
