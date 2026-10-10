"""POST /api/assist: run an utterance through the supervisor."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from jarvis_shared.messages import AssistRequest, AssistResponse

from jarvis_backend.api.deps import get_container
from jarvis_backend.auth.deps import any_role, current_principal
from jarvis_backend.auth.service import Principal
from jarvis_backend.container import Container

router = APIRouter(prefix="/api", tags=["assist"], dependencies=[any_role])


@router.post("/assist", response_model=AssistResponse)
async def assist(req: AssistRequest, request: Request, principal: Principal = Depends(current_principal),
                 c: Container = Depends(get_container)) -> AssistResponse:
    await c.limiter.enforce(f"assist:{principal.subject}", limit=c.settings.assist_per_minute, window=60)
    started = request.app.state.metrics.assist_latency
    with started.labels(agent="all").time():
        response = await c.supervisor.handle(req)
    return response
