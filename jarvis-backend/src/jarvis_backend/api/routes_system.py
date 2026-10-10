"""Health, readiness, status, audit, and the local runtime's heartbeat.

* ``/healthz``  liveness: the process is up. No dependencies, always cheap, unauthenticated.
* ``/readyz``   readiness: DB (and Redis if configured) reachable. Load balancers use this.
* ``/api/system/status``  rich runtime state for the dashboard (admin).
* ``/metrics``  Prometheus (separate bearer token, not a user JWT).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, Response

from jarvis_backend.api.deps import get_container
from jarvis_backend.auth.deps import admin_only, agent_only, verify_metrics_token
from jarvis_backend.auth.service import Principal
from jarvis_backend.container import Container
from jarvis_backend.observability import runtime_snapshot
from jarvis_backend.schemas import AuditOut, AuditVerification, RuntimeState, TurnOut

router = APIRouter(tags=["system"])
_admin = admin_only
RUNTIME_KEY = "runtime"
RUNTIME_STALE_SECONDS = 45


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(response: Response, c: Container = Depends(get_container)) -> dict:
    db_ok, cache_ok = await c.db.ping(), await c.cache.healthy()
    # A Redis outage degrades us (fallbacks exist) but does not make us unready.
    ready = db_ok
    response.status_code = 200 if ready else 503
    return {"ready": ready, "database": db_ok, "redis": cache_ok if c.cache.backend == "redis" else "n/a"}


@router.get("/metrics", dependencies=[Depends(verify_metrics_token)], include_in_schema=False)
async def metrics(request: Request, c: Container = Depends(get_container)) -> Response:
    m = request.app.state.metrics
    stats = c.cache.stats
    m.cache_hits.set(stats.hits)
    m.cache_misses.set(stats.misses)
    m.cache_errors.set(stats.errors)
    m.cache_fallback.set(1 if stats.fallback_active else 0)
    m.cache_local_items.set(c.cache.local_items)
    m.pending_approvals.set(len(await c.approvals.list(status="PENDING", limit=500)))
    pool = getattr(c.db.engine.pool, "checkedout", None)
    if pool:
        m.db_pool_checked_out.set(pool())
    return Response(m.render(), media_type="text/plain; version=0.0.4")


@router.get("/api/system/status", dependencies=[_admin])
async def status(request: Request, c: Container = Depends(get_container)) -> dict:
    pending = len(await c.approvals.list(status="PENDING", limit=500))
    snapshot = runtime_snapshot(request.app.state.metrics, c, request.app.state.ws_count())
    return {
        "version": c.settings.version, "env": c.settings.env,
        "database_ok": await c.db.ping(), "cache_ok": await c.cache.healthy(),
        "scheduler_running": bool(c.scheduler and c.scheduler.running),
        "agents": len(c.registry.all()), "pending_approvals": pending,
        "runtime": snapshot, "config_findings": [
            {"level": f.level, "code": f.code, "message": f.message} for f in c.settings.findings()],
    }


@router.get("/api/audit", response_model=List[AuditOut], dependencies=[_admin])
async def audit(limit: int = Query(default=100, ge=1, le=500), event: Optional[str] = Query(default=None, max_length=64),
                c: Container = Depends(get_container)) -> list:
    return await c.audit.recent(limit=limit, event=event)


@router.get("/api/audit/verify", response_model=AuditVerification, dependencies=[_admin])
async def verify_audit(c: Container = Depends(get_container)) -> AuditVerification:
    return await c.audit.verify()


@router.get("/api/turns", response_model=List[TurnOut], dependencies=[_admin])
async def turns(limit: int = Query(default=50, ge=1, le=200), c: Container = Depends(get_container)) -> list:
    return await c.turns.recent(limit=limit)


# ----------------------------------------------------------------- local runtime heartbeat
@router.post("/api/runtime/heartbeat", status_code=204)
async def heartbeat(state: RuntimeState, principal: Principal = agent_only,
                    c: Container = Depends(get_container)) -> None:
    """The local agent reports its real state (listening, mic paused, ...) every few seconds."""
    await c.limiter.enforce(f"heartbeat:{principal.subject}", limit=120, window=60)
    payload = state.model_dump() | {"device": principal.subject}
    await c.kv.put(RUNTIME_KEY, payload)
    await c.bus.publish({"type": "runtime", **payload})


@router.get("/api/runtime", dependencies=[_admin])
async def runtime(c: Container = Depends(get_container)) -> dict:
    stored = await c.kv.get(RUNTIME_KEY)
    if stored is None:
        return {"connected": False}
    value, updated = stored
    age = (datetime.now(UTC) - updated).total_seconds()
    return {**value, "connected": age < RUNTIME_STALE_SECONDS, "last_seen_seconds": int(age)}
