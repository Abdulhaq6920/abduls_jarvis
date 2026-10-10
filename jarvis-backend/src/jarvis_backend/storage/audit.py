"""Tamper-evident audit log.

Every entry stores ``prev_hash`` (the previous entry's hash) and ``hash`` (SHA-256 over
the previous hash plus the canonical JSON of this entry). Editing or deleting a row in
the middle breaks every hash after it, which :meth:`AuditLog.verify` detects.

Limits (be honest about them): the chain proves *internal* consistency. Someone with
full database write access can recompute the whole chain, and deleting the newest
entries is undetectable without an external anchor. For stronger guarantees, ship the
log to write-once storage (the JSON log stream is designed for that).

Writers are serialised -- an in-process lock plus a PostgreSQL advisory lock -- so
concurrent API replicas cannot both read the same "previous hash" and fork the chain.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text

from jarvis_backend.schemas import AuditOut, AuditVerification
from jarvis_backend.storage.db import Database, utcnow
from jarvis_backend.storage.models import AuditEntry

GENESIS_HASH = "0" * 64
_ADVISORY_LOCK_KEY = 746_100_001


def _canonical(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _compute_hash(prev_hash: str, entry: AuditEntry) -> str:
    body = _canonical({
        "ts": entry.ts.isoformat(),
        "event": entry.event,
        "agent_id": entry.agent_id,
        "session_id": entry.session_id,
        "data": entry.data,
    })
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = asyncio.Lock()

    async def record(
        self,
        event: str,
        *,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        **data: Any,
    ) -> int:
        """Append an entry. Raises on failure: an unauditable action should not proceed."""
        # Round-trip through JSON so what we hash is exactly what will be read back.
        clean = json.loads(json.dumps(data, default=str))
        async with self._lock:
            async with self._db.session() as session:
                if self._db.engine.dialect.name == "postgresql":
                    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                                          {"k": _ADVISORY_LOCK_KEY})
                last = (await session.execute(
                    select(AuditEntry.hash).order_by(AuditEntry.id.desc()).limit(1)
                )).scalar_one_or_none()
                prev = last or GENESIS_HASH
                entry = AuditEntry(ts=utcnow(), event=event, agent_id=agent_id,
                                   session_id=session_id, data=clean, prev_hash=prev, hash="")
                entry.hash = _compute_hash(prev, entry)
                session.add(entry)
                await session.flush()
                return entry.id

    async def recent(self, limit: int = 100, event: Optional[str] = None) -> List[AuditOut]:
        stmt = select(AuditEntry).order_by(AuditEntry.id.desc()).limit(min(limit, 500))
        if event:
            stmt = stmt.where(AuditEntry.event == event)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [AuditOut.model_validate(r) for r in rows]

    async def verify(self, batch: int = 500) -> AuditVerification:
        """Walk the whole chain in id order and check every link and hash."""
        prev = GENESIS_HASH
        checked = 0
        last_id = 0
        while True:
            async with self._db.session() as session:
                rows = (await session.execute(
                    select(AuditEntry).where(AuditEntry.id > last_id)
                    .order_by(AuditEntry.id).limit(batch)
                )).scalars().all()
            if not rows:
                return AuditVerification(ok=True, checked=checked)
            for row in rows:
                if row.prev_hash != prev:
                    return AuditVerification(ok=False, checked=checked, first_bad_id=row.id,
                                             reason="Chain link broken (entry removed or reordered).")
                if _compute_hash(prev, row) != row.hash:
                    return AuditVerification(ok=False, checked=checked, first_bad_id=row.id,
                                             reason="Entry contents do not match their hash.")
                prev = row.hash
                last_id = row.id
                checked += 1
