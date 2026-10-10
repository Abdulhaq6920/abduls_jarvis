"""Housekeeping: data retention and expiry. Runs daily from the scheduler.

Personal data must not accumulate forever, and dead credentials should not linger.
"""
from __future__ import annotations

from jarvis_shared.log import get_logger

from jarvis_backend.container import Container

log = get_logger(__name__)


async def run_maintenance(c: Container) -> dict:
    result = {
        "turns_purged": await c.turns.purge_older_than(c.settings.turn_retention_days),
        "approvals_expired": await c.approvals.expire_stale(),
        "sessions_pruned": await c.auth.sessions.prune(),
    }
    await c.audit.record("maintenance", **result)
    log.info("maintenance complete", extra=result)
    return result
