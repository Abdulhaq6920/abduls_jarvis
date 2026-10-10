"""Approval queue (NICK FURY's gate), backed by the database.

Every state change is a single conditional ``UPDATE ... WHERE status = <expected>`` so
it is atomic across API replicas and local agents: a request can be granted once and
claimed for execution once, even if two processes race.

    PENDING --grant--> GRANTED --claim--> EXECUTING --complete--> EXECUTED | FAILED
       |--deny--> DENIED          (stale PENDING/GRANTED become EXPIRED)
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from jarvis_backend.schemas import ApprovalOut
from jarvis_backend.storage.db import Database, utcnow
from jarvis_backend.storage.models import ApprovalRow

PENDING, GRANTED, DENIED = "PENDING", "GRANTED", "DENIED"
EXECUTING, EXECUTED, FAILED, EXPIRED = "EXECUTING", "EXECUTED", "FAILED", "EXPIRED"


class ApprovalStore:
    def __init__(self, db: Database, ttl_seconds: int = 900) -> None:
        self._db = db
        self._ttl = timedelta(seconds=ttl_seconds)

    async def create(self, agent_id: str, summary: str, risk: str,
                     request: Optional[Dict[str, Any]]) -> ApprovalOut:
        row = ApprovalRow(id=str(uuid.uuid4()), agent_id=agent_id, summary=summary[:500],
                          risk=risk, status=PENDING, request=request, created_at=utcnow())
        async with self._db.session() as session:
            session.add(row)
        return ApprovalOut.model_validate(row)

    async def get(self, approval_id: str) -> Optional[ApprovalOut]:
        async with self._db.session() as session:
            row = await session.get(ApprovalRow, approval_id)
            return ApprovalOut.model_validate(row) if row else None

    async def list(self, status: Optional[str] = None, limit: int = 100) -> List[ApprovalOut]:
        await self.expire_stale()
        stmt = select(ApprovalRow).order_by(ApprovalRow.created_at.desc()).limit(min(limit, 500))
        if status:
            stmt = stmt.where(ApprovalRow.status == status)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [ApprovalOut.model_validate(r) for r in rows]

    async def resolve(self, approval_id: str, granted: bool, by: str) -> Optional[ApprovalOut]:
        """Grant or deny. Returns the approval if *this call* changed it, else None."""
        await self.expire_stale()
        return await self._transition(
            approval_id, expected=PENDING, new=GRANTED if granted else DENIED,
            resolved_at=utcnow(), resolved_by=by,
        )

    async def claim(self, approval_id: str) -> Optional[ApprovalOut]:
        """Atomically take a granted approval for execution (at most one claimant wins)."""
        await self.expire_stale()
        return await self._transition(approval_id, expected=GRANTED, new=EXECUTING)

    async def complete(self, approval_id: str, ok: bool, result: Dict[str, Any]) -> Optional[ApprovalOut]:
        return await self._transition(
            approval_id, expected=EXECUTING, new=EXECUTED if ok else FAILED,
            executed_at=utcnow(), result=result,
        )

    async def expire_stale(self) -> int:
        """Stale approvals must not be actionable later (a grant is only good for a while)."""
        cutoff = utcnow() - self._ttl
        async with self._db.session() as session:
            a = await session.execute(
                update(ApprovalRow).where(ApprovalRow.status == PENDING,
                                          ApprovalRow.created_at < cutoff).values(status=EXPIRED))
            b = await session.execute(
                update(ApprovalRow).where(ApprovalRow.status == GRANTED,
                                          ApprovalRow.resolved_at < cutoff).values(status=EXPIRED))
            return (a.rowcount or 0) + (b.rowcount or 0)

    async def _transition(self, approval_id: str, *, expected: str, new: str,
                          **values: Any) -> Optional[ApprovalOut]:
        async with self._db.session() as session:
            res = await session.execute(
                update(ApprovalRow)
                .where(ApprovalRow.id == approval_id, ApprovalRow.status == expected)
                .values(status=new, **values)
            )
            if res.rowcount != 1:
                return None
            row = await session.get(ApprovalRow, approval_id)
            return ApprovalOut.model_validate(row)
