"""Approval workflow.

Separation of duties is the point of this module:

* **admin** (a human, logged in) is the only role that can GRANT or DENY;
* **agent** (a registered local device) is the only role that can CLAIM a granted request
  and report its result. It can never approve its own work.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from jarvis_backend.api.deps import get_container
from jarvis_backend.auth.deps import ADMIN, AGENT, admin_only, agent_only, any_role, require_role
from jarvis_backend.auth.service import Principal
from jarvis_backend.container import Container
from jarvis_backend.schemas import ApprovalDecision, ApprovalOut, ApprovalResult
from jarvis_shared.security import RiskLevel

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("", response_model=List[ApprovalOut], dependencies=[Depends(require_role(ADMIN))])
async def list_approvals(status: Optional[str] = Query(default=None, pattern="^[A-Z]{3,10}$"),
                         limit: int = Query(default=100, ge=1, le=500),
                         c: Container = Depends(get_container)) -> list:
    return await c.approvals.list(status=status, limit=limit)


@router.get("/granted", response_model=List[ApprovalOut], dependencies=[Depends(require_role(AGENT))])
async def granted(c: Container = Depends(get_container)) -> list:
    """What the local agent should now execute (after re-validating it locally)."""
    return await c.approvals.list(status="GRANTED", limit=50)


# Risk levels that force a step-up on a GRANT, keyed by the configured threshold. SAFE is
# never gated; DANGEROUS always is; RESTRICTED only when the threshold is lowered to it.
_STEP_UP_RISK = {"DANGEROUS": {"DANGEROUS"}, "RESTRICTED": {"DANGEROUS", "RESTRICTED"}}
# The complete set of risk values the gate recognizes; anything else is ambiguous and denied.
_KNOWN_RISKS = {r.value for r in RiskLevel}


async def _enforce_high_risk_step_up(approval_id: str, body: ApprovalDecision,
                                     principal: Principal, c: Container) -> None:
    """A GRANT of a high-risk action needs a fresh second factor. Fail closed.

    * MFA admin: require an inline code OR a recent step-up on the session, else 401.
    * Non-MFA admin: allowed with an audited warning, unless JARVIS_APPROVAL_REQUIRE_MFA
      hardens it to a hard denial. A grant never unlocks a block -- it only adds this gate,
      and separation of duties (agent/device 403) is untouched by `admin_only`.
    """
    gated = _STEP_UP_RISK[c.settings.approval_step_up_risk]
    pending = await c.approvals.get(approval_id)
    if pending is None or pending.status != "PENDING":
        return
    # Fail closed on an ambiguous risk: a value outside the known enum (corrupted row,
    # version skew, unexpected producer) must NOT be treated as "below threshold" and
    # waved through. Validate against the full RiskLevel set first, then gate.
    if pending.risk not in _KNOWN_RISKS:
        await c.audit.record("approval_step_up_denied", approval_id=approval_id,
                             by=principal.subject, risk=pending.risk, reason="unknown_risk")
        raise HTTPException(403, "This approval has an unrecognized risk level and cannot be "
                                 "granted. Review it manually.")
    if pending.risk not in gated:
        return
    if await c.auth.mfa_enabled_for(principal.subject):
        try:
            await c.auth.ensure_step_up(principal, body.code)
        except HTTPException:
            await c.audit.record("approval_step_up_denied", approval_id=approval_id,
                                 by=principal.subject, risk=pending.risk)
            raise
        return
    if c.settings.approval_require_mfa:
        await c.audit.record("approval_step_up_denied", approval_id=approval_id,
                             by=principal.subject, risk=pending.risk, reason="no_mfa")
        raise HTTPException(403, "This high-risk action requires MFA. Enroll a second factor "
                                 "before approving it.")
    await c.audit.record("approval_high_risk_no_mfa", approval_id=approval_id,
                         by=principal.subject, risk=pending.risk)


@router.post("/{approval_id}/decision", response_model=ApprovalOut)
async def decide(approval_id: str, body: ApprovalDecision,
                 principal: Principal = admin_only,
                 c: Container = Depends(get_container)) -> ApprovalOut:
    await c.limiter.enforce(f"approve:{principal.subject}", limit=60, window=60)
    if body.granted:
        await _enforce_high_risk_step_up(approval_id, body, principal, c)
    resolved = await c.approvals.resolve(approval_id, body.granted, by=principal.subject)
    if resolved is None:
        raise HTTPException(409, "This approval is no longer pending (already decided or expired).")
    await c.audit.record("approval_granted" if body.granted else "approval_denied",
                         agent_id=resolved.agent_id, approval_id=approval_id, by=principal.subject,
                         risk=resolved.risk)
    await c.bus.publish({"type": "approval", "id": approval_id, "status": resolved.status})
    return resolved


@router.post("/{approval_id}/claim", response_model=ApprovalOut, dependencies=[Depends(require_role(AGENT))])
async def claim(approval_id: str, c: Container = Depends(get_container)) -> ApprovalOut:
    claimed = await c.approvals.claim(approval_id)
    if claimed is None:
        raise HTTPException(409, "Not claimable (not granted, already claimed, or expired).")
    await c.audit.record("approval_claimed", agent_id=claimed.agent_id, approval_id=approval_id)
    return claimed


@router.post("/{approval_id}/result", response_model=ApprovalOut)
async def report_result(approval_id: str, body: ApprovalResult,
                        principal: Principal = agent_only,
                        c: Container = Depends(get_container)) -> ApprovalOut:
    done = await c.approvals.complete(approval_id, body.ok, body.model_dump())
    if done is None:
        raise HTTPException(409, "Nothing to complete (not currently executing).")
    await c.audit.record("approval_executed", agent_id=done.agent_id, approval_id=approval_id,
                         ok=body.ok, by=principal.subject)
    await c.bus.publish({"type": "approval", "id": approval_id, "status": done.status})
    return done


@router.get("/{approval_id}", response_model=ApprovalOut, dependencies=[any_role])
async def get_approval(approval_id: str, c: Container = Depends(get_container)) -> ApprovalOut:
    item = await c.approvals.get(approval_id)
    if item is None:
        raise HTTPException(404, "Approval not found.")
    return item
