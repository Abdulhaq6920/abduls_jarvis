"""Long-term memory API. Reading and writing personal data is admin-only."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from jarvis_backend.api.deps import get_container
from jarvis_backend.auth.deps import admin_only
from jarvis_backend.auth.service import Principal
from jarvis_backend.container import Container
from jarvis_backend.schemas import MemoryCreate, MemoryOut
from jarvis_backend.storage.memory import MemoryFull

router = APIRouter(prefix="/api/memory", tags=["memory"])
_admin = admin_only


@router.get("", response_model=List[MemoryOut], dependencies=[_admin])
async def list_memories(q: Optional[str] = Query(default=None, max_length=200),
                        kind: Optional[str] = Query(default=None, pattern="^(fact|note)$"),
                        limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                        c: Container = Depends(get_container)) -> list:
    if q:
        return await c.memory.search(q, limit=limit)
    return await c.memory.list(kind=kind, limit=limit, offset=offset)


@router.post("", response_model=MemoryOut, status_code=201)
async def add_memory(body: MemoryCreate, principal: Principal = admin_only,
                     c: Container = Depends(get_container)) -> MemoryOut:
    try:
        item = await c.memory.add(body.text, kind=body.kind, tags=body.tags, source="ui")
    except MemoryFull as exc:
        raise HTTPException(409, str(exc)) from None
    await c.audit.record("memory_added", by=principal.subject, memory_id=item.id)
    return item


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: str, principal: Principal = admin_only,
                        c: Container = Depends(get_container)) -> None:
    if not await c.memory.delete(memory_id):
        raise HTTPException(404, "Memory not found.")
    await c.audit.record("memory_deleted", by=principal.subject, memory_id=memory_id)


@router.delete("", dependencies=[_admin])
async def delete_all_memories(confirm: str = Query(default=""), principal: Principal = admin_only,
                              c: Container = Depends(get_container)) -> dict:
    """Wipes all memory. Requires ``?confirm=DELETE-ALL`` so a stray call cannot do it."""
    if confirm != "DELETE-ALL":
        raise HTTPException(400, "Pass confirm=DELETE-ALL to erase all memories.")
    removed = await c.memory.delete_all()
    await c.audit.record("memory_wiped", by=principal.subject, removed=removed)
    return {"removed": removed}
