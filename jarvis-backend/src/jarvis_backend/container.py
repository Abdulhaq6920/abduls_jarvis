"""Dependency container: builds every service once and wires them together.

The HTTP app and the background worker both start from here, so they share exactly the
same construction logic. Nothing else in the codebase instantiates infrastructure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from jarvis_shared.log import get_logger

from jarvis_backend.agents.registry import AgentConfig, AgentRegistry
from jarvis_backend.auth.service import AuthService
from jarvis_backend.auth.tokens import TokenCodec, build_codec
from jarvis_backend.cache import Cache, build_cache
from jarvis_backend.config import Settings
from jarvis_backend.core.llm import build_llm
from jarvis_backend.core.policy import PolicyEngine
from jarvis_backend.core.search import build_search
from jarvis_backend.core.router import KeywordRouter
from jarvis_backend.core.supervisor import Supervisor
from jarvis_backend.crypto import FieldCipher, set_cipher
from jarvis_backend.events import EventBus, build_bus
from jarvis_backend.jobs import JobRunner, JobScheduler
from jarvis_backend.ratelimit import RateLimiter
from jarvis_backend.storage import (
    AgentSettingsStore,
    ApprovalStore,
    AuditLog,
    Database,
    DeviceStore,
    JobRunStore,
    KeyValueStore,
    MemoryStore,
    SessionStore,
    TurnStore,
    UserStore,
)

log = get_logger(__name__)


@dataclass
class Container:
    settings: Settings
    db: Database
    cache: Cache
    limiter: RateLimiter
    bus: EventBus
    codec: TokenCodec
    auth: AuthService
    audit: AuditLog
    approvals: ApprovalStore
    memory: MemoryStore
    turns: TurnStore
    job_runs: JobRunStore
    kv: KeyValueStore
    registry: AgentRegistry
    agent_config: AgentConfig
    policy: PolicyEngine
    supervisor: Supervisor
    job_runner: JobRunner
    scheduler: Optional[JobScheduler] = None

    @classmethod
    async def create(cls, settings: Settings, *, create_schema: Optional[bool] = None) -> Container:
        for finding in settings.findings():  # prod errors already aborted in Settings validation
            log.warning("config: %s", finding.message, extra={"code": finding.code})

        # The cipher must be installed before any store touches an encrypted column.
        set_cipher(FieldCipher.from_setting(settings.secret("data_key")))

        db = Database(settings)
        # Dev installs create tables automatically. Production runs the explicit
        # `migrate` step once, so replicas never race to create the schema.
        if create_schema if create_schema is not None else settings.env == "dev":
            await db.create_schema()

        cache = build_cache(settings.redis_url_value, settings.cache_max_local_items)
        limiter = RateLimiter(cache)
        bus = build_bus(settings.redis_url_value)
        audit = AuditLog(db)
        codec = build_codec(settings)
        auth = AuthService(settings=settings, users=UserStore(db), sessions=SessionStore(db),
                           devices=DeviceStore(db), codec=codec, cache=cache, limiter=limiter,
                           audit=audit)

        approvals = ApprovalStore(db, ttl_seconds=settings.approval_ttl_seconds)
        memory, turns = MemoryStore(db), TurnStore(db)
        job_runs, kv = JobRunStore(db), KeyValueStore(db)
        registry = AgentRegistry()
        agent_config = AgentConfig(AgentSettingsStore(db), cache)
        policy = PolicyEngine()
        supervisor = Supervisor(
            registry=registry, config=agent_config, router=KeywordRouter(), policy=policy,
            approvals=approvals, audit=audit, turns=turns, memory=memory, bus=bus,
            llm=build_llm(settings), search=build_search(settings),
            history_turns=settings.history_turns,
        )
        runner = JobRunner(supervisor, job_runs, audit, bus)
        log.info("container ready", extra=settings.describe() | {"cache": cache.backend, "bus": bus.kind})
        return cls(settings=settings, db=db, cache=cache, limiter=limiter, bus=bus, codec=codec,
                   auth=auth, audit=audit, approvals=approvals, memory=memory, turns=turns,
                   job_runs=job_runs, kv=kv, registry=registry, agent_config=agent_config,
                   policy=policy, supervisor=supervisor, job_runner=runner)

    async def close(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown()
        await self.bus.close()
        await self.cache.close()
        await self.db.dispose()
