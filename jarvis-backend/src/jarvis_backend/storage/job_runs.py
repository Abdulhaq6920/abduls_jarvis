"""History of background job executions."""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from sqlalchemy import func, select

from jarvis_backend.schemas import JobRunOut
from jarvis_backend.storage.db import Database, utcnow
from jarvis_backend.storage.models import JobRunRow


class JobRunStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def start(self, job_id: str, trigger: str) -> str:
        run_id = str(uuid.uuid4())
        async with self._db.session() as session:
            session.add(JobRunRow(id=run_id, job_id=job_id, trigger=trigger, started_at=utcnow()))
        return run_id

    async def finish(self, run_id: str, *, ok: bool, output: str = "", error: str = "") -> None:
        async with self._db.session() as session:
            row = await session.get(JobRunRow, run_id)
            if row is not None:
                row.finished_at, row.ok = utcnow(), ok
                row.output, row.error = output[:4000], error[:2000]

    async def recent(self, job_id: Optional[str] = None, limit: int = 50) -> List[JobRunOut]:
        stmt = select(JobRunRow).order_by(JobRunRow.started_at.desc()).limit(min(limit, 200))
        if job_id:
            stmt = stmt.where(JobRunRow.job_id == job_id)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [JobRunOut.model_validate(r) for r in rows]

    async def last_by_job(self) -> Dict[str, JobRunOut]:
        latest = (select(JobRunRow.job_id, func.max(JobRunRow.started_at).label("m"))
                  .group_by(JobRunRow.job_id).subquery())
        stmt = select(JobRunRow).join(
            latest, (JobRunRow.job_id == latest.c.job_id) & (JobRunRow.started_at == latest.c.m))
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return {r.job_id: JobRunOut.model_validate(r) for r in rows}
