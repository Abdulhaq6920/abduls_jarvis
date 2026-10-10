"""Background jobs (QUICKSILVER).

Job *definitions* live in code; job *history* lives in the database. Because the
schedule is static there is nothing to persist in a scheduler job store: every process
that runs the scheduler rebuilds the same cron triggers at start-up.

Run the scheduler in exactly ONE process (the dedicated ``worker``). Running it in
several would fire every job several times.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from jarvis_shared.log import get_logger
from jarvis_shared.messages import AssistRequest

from jarvis_backend.core.supervisor import Supervisor
from jarvis_backend.events import EventBus
from jarvis_backend.schemas import JobRunOut
from jarvis_backend.storage import AuditLog, JobRunStore

log = get_logger(__name__)


@dataclass(frozen=True)
class JobSpec:
    id: str
    name: str
    cron: str  # standard 5-field crontab
    description: str
    utterance: str  # what the job asks the supervisor to do


JOBS: List[JobSpec] = [
    JobSpec("morning-email", "Morning email summary", "0 8 * * *",
            "Summarise important emails every morning.",
            "ask NATASHA to summarise my important emails"),
    JobSpec("evening-dev", "Evening dev summary", "0 19 * * *",
            "Summarise development activity every evening.",
            "ask TONY to check my project"),
    JobSpec("weekly-summary", "Weekly work summary", "0 9 * * 1",
            "Prepare the weekly work summary every Monday.",
            "ask DR STRANGE to plan my weekly work summary"),
    JobSpec("hourly-health", "Project health check", "0 * * * *",
            "Check configured project health every hour.",
            "ask TONY to check my project"),
]
JOBS_BY_ID = {j.id: j for j in JOBS}


class JobRunner:
    """Executes one job through the normal supervisor path, so jobs obey the same
    policy gate and audit trail as spoken requests."""

    def __init__(self, supervisor: Supervisor, runs: JobRunStore, audit: AuditLog, bus: EventBus) -> None:
        self._supervisor, self._runs, self._audit, self._bus = supervisor, runs, audit, bus

    async def run(self, job: JobSpec, trigger: str = "cron") -> JobRunOut:
        run_id = await self._runs.start(job.id, trigger)
        ok, output, error = False, "", ""
        try:
            resp = await self._supervisor.handle(AssistRequest(
                session_id=f"job:{job.id}", utterance=job.utterance, woke_from_wake_word=False))
            ok, output = True, resp.speech
        except Exception as exc:  # a failing job must not take the scheduler down
            log.exception("job failed", extra={"job": job.id})
            error = f"{type(exc).__name__}: {exc}"
        await self._runs.finish(run_id, ok=ok, output=output, error=error)
        await self._audit.record("job_run", job=job.id, trigger=trigger, ok=ok)
        await self._bus.publish({"type": "job", "job": job.id, "ok": ok})
        log.info("job finished", extra={"job": job.id, "ok": ok, "trigger": trigger})
        return (await self._runs.recent(job.id, limit=1))[0]


class JobScheduler:
    def __init__(self, runner: JobRunner, timezone: str = "",
                 maintenance: Optional[Callable[[], Awaitable[object]]] = None) -> None:
        self._runner = runner
        self._maintenance = maintenance
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._timezone = timezone or None

    @property
    def running(self) -> bool:
        return bool(self._scheduler and self._scheduler.running)

    def start(self) -> None:
        scheduler = AsyncIOScheduler(timezone=self._timezone)
        for job in JOBS:
            scheduler.add_job(
                self._runner.run, CronTrigger.from_crontab(job.cron, timezone=self._timezone),
                args=[job, "cron"], id=job.id, replace_existing=True,
                max_instances=1, coalesce=True, misfire_grace_time=300,
            )
        if self._maintenance is not None:
            scheduler.add_job(self._maintenance, CronTrigger(hour=3, minute=30, timezone=self._timezone),
                              id="maintenance", replace_existing=True, max_instances=1, coalesce=True)
        scheduler.start()
        self._scheduler = scheduler
        log.info("scheduler started", extra={"jobs": [j.id for j in JOBS]})

    def shutdown(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
