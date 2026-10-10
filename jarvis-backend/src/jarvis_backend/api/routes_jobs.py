"""Background jobs: definitions, last run, history, and run-now."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException

from jarvis_backend.api.deps import get_container
from jarvis_backend.auth.deps import ADMIN, admin_only, require_role
from jarvis_backend.auth.service import Principal
from jarvis_backend.container import Container
from jarvis_backend.jobs import JOBS, JOBS_BY_ID
from jarvis_backend.schemas import JobOut, JobRunOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_role(ADMIN))])


@router.get("", response_model=List[JobOut])
async def list_jobs(c: Container = Depends(get_container)) -> List[JobOut]:
    last = await c.job_runs.last_by_job()
    return [JobOut(id=j.id, name=j.name, cron=j.cron, description=j.description, last_run=last.get(j.id))
            for j in JOBS]


@router.get("/{job_id}/runs", response_model=List[JobRunOut])
async def job_runs(job_id: str, c: Container = Depends(get_container)) -> list:
    if job_id not in JOBS_BY_ID:
        raise HTTPException(404, "Unknown job.")
    return await c.job_runs.recent(job_id, limit=25)


@router.post("/{job_id}/run", response_model=JobRunOut)
async def run_now(job_id: str, principal: Principal = admin_only,
                  c: Container = Depends(get_container)) -> JobRunOut:
    job = JOBS_BY_ID.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job.")
    await c.limiter.enforce(f"job-run:{job_id}", limit=6, window=60)
    await c.audit.record("job_run_requested", job=job_id, by=principal.subject)
    return await c.job_runner.run(job, trigger="manual")
