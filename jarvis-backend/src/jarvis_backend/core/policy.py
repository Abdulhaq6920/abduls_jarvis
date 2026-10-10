"""Policy engine (backend side): the FIRST security gate.

Decides whether a :class:`ToolRequest` may run, needs human approval, or is blocked. The
local agent applies an independent SECOND gate and may only ever tighten the outcome.
"""
from __future__ import annotations

from dataclasses import dataclass

from jarvis_shared.commands import classify_command
from jarvis_shared.security import CommandCategory, RiskLevel, ToolRequest

_RISK_ORDER = {RiskLevel.SAFE: 0, RiskLevel.RESTRICTED: 1, RiskLevel.DANGEROUS: 2}
_CATEGORY_RISK = {
    CommandCategory.SAFE: RiskLevel.SAFE,
    CommandCategory.RESTRICTED: RiskLevel.RESTRICTED,
    CommandCategory.DANGEROUS: RiskLevel.DANGEROUS,
    CommandCategory.UNKNOWN: RiskLevel.RESTRICTED,
}
_MUTATING_ACTIONS = {"write_file", "delete_file", "move_file", "send_email", "post", "delete"}


def max_risk(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    return a if _RISK_ORDER[a] >= _RISK_ORDER[b] else b


@dataclass(frozen=True)
class PolicyDecision:
    risk: RiskLevel
    allow: bool
    requires_approval: bool
    reason: str


def describe_request(req: ToolRequest) -> str:
    """Human-readable summary shown on the approval card: say exactly what will happen."""
    args = req.args
    if req.category == "terminal":
        where = f" in {args['cwd']}" if args.get("cwd") else ""
        return f"{req.agent_id} wants to run `{args.get('command', '')}`{where}"
    if req.category == "filesystem":
        return f"{req.agent_id} wants to {req.action.replace('_', ' ')} {args.get('path', '')}"
    if req.category == "application":
        return f"{req.agent_id} wants to launch {args.get('app', '')}"
    detail = f": {req.reason}" if req.reason else ""
    return f"{req.agent_id} wants to {req.action.replace('_', ' ')} ({req.category}){detail}"


class PolicyEngine:
    def __init__(self, block_dangerous: bool = True, approve_restricted: bool = True) -> None:
        self.block_dangerous = block_dangerous
        self.approve_restricted = approve_restricted

    def evaluate(self, req: ToolRequest) -> PolicyDecision:
        risk = self._assess_risk(req)

        if risk == RiskLevel.DANGEROUS:
            if self.block_dangerous:
                return PolicyDecision(risk, allow=False, requires_approval=False,
                                      reason="Dangerous operation blocked by policy.")
            return PolicyDecision(risk, allow=False, requires_approval=True,
                                  reason="Dangerous operation requires explicit approval.")
        if risk == RiskLevel.RESTRICTED:
            needs = self.approve_restricted or req.requires_approval
            return PolicyDecision(risk, allow=not needs, requires_approval=needs,
                                  reason="Restricted operation." + (" Approval required." if needs else ""))
        # Even a SAFE-looking request is held if the agent itself flagged it.
        if req.requires_approval:
            return PolicyDecision(risk, allow=False, requires_approval=True,
                                  reason="Approval requested by the agent.")
        return PolicyDecision(RiskLevel.SAFE, allow=True, requires_approval=False,
                              reason="Safe operation.")

    @staticmethod
    def _assess_risk(req: ToolRequest) -> RiskLevel:
        if req.category == "terminal" and req.action == "run_command":
            assessment = classify_command(str(req.args.get("command", "")))
            return _CATEGORY_RISK[assessment.category]

        risk = req.declared_risk
        if req.category in {"keyboard", "mouse", "screen"} or req.action in _MUTATING_ACTIONS:
            risk = max_risk(risk, RiskLevel.RESTRICTED)
        if req.action == "delete_file":
            path = str(req.args.get("path", "")).lower()
            if path.endswith(("/", "\\")) or "*" in path or path.rstrip("\\/") in {"c:", ""}:
                risk = RiskLevel.DANGEROUS
        return risk
