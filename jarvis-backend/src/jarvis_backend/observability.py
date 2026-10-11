"""Metrics and runtime-state introspection.

Everything an operator needs to answer "is it healthy, and is it about to run out of
something?" without attaching a debugger:

* request count / latency / in-flight, per route template (not raw path: bounded labels)
* assist latency per agent, auth outcomes, rate-limit rejections, cache hits/misses/errors
* DB pool occupancy, WebSocket connections, event-loop lag, process memory (RSS) & CPU
* bounded-structure sizes (local cache items) so unbounded growth is visible early

Label values are never user input (that would let a client create unbounded series).
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict

import psutil
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

_PROCESS = psutil.Process(os.getpid())
_STARTED = time.time()
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        self.requests = Counter("jarvis_http_requests_total", "HTTP requests",
                                ["method", "route", "status"], registry=r)
        self.latency = Histogram("jarvis_http_request_seconds", "HTTP latency",
                                 ["method", "route"], buckets=_BUCKETS, registry=r)
        self.in_flight = Gauge("jarvis_http_in_flight", "Requests being served", registry=r)
        self.assist_latency = Histogram("jarvis_assist_seconds", "Supervisor latency",
                                        ["agent"], buckets=_BUCKETS, registry=r)
        self.auth_events = Counter("jarvis_auth_events_total", "Auth outcomes", ["event"], registry=r)
        self.rate_limited = Counter("jarvis_rate_limited_total", "429 responses", ["route"], registry=r)
        self.ws_connections = Gauge("jarvis_ws_connections", "Open WebSockets", registry=r)
        self.loop_lag = Gauge("jarvis_event_loop_lag_seconds", "Event-loop scheduling delay", registry=r)
        self.rss = Gauge("jarvis_process_rss_bytes", "Resident memory", registry=r)
        self.cpu = Gauge("jarvis_process_cpu_percent", "Process CPU %", registry=r)
        self.threads = Gauge("jarvis_process_threads", "OS threads", registry=r)
        self.open_fds = Gauge("jarvis_process_open_files", "Open file handles", registry=r)
        self.uptime = Gauge("jarvis_uptime_seconds", "Process uptime", registry=r)
        self.cache_hits = Gauge("jarvis_cache_hits", "Cache hits", registry=r)
        self.cache_misses = Gauge("jarvis_cache_misses", "Cache misses", registry=r)
        self.cache_errors = Gauge("jarvis_cache_errors", "Cache backend errors", registry=r)
        self.cache_fallback = Gauge("jarvis_cache_fallback_active", "1 if Redis is down", registry=r)
        self.cache_local_items = Gauge("jarvis_cache_local_items", "In-process cache entries", registry=r)
        self.db_pool_checked_out = Gauge("jarvis_db_pool_checked_out", "DB connections in use", registry=r)
        self.pending_approvals = Gauge("jarvis_pending_approvals", "Approvals awaiting a human", registry=r)
        # Label values are config-controlled model ids, agent ids or fixed outcome names: bounded.
        self.llm_requests = Counter("jarvis_llm_requests_total", "Language-model calls",
                                    ["model", "outcome"], registry=r)
        self.llm_tokens = Counter("jarvis_llm_tokens_total", "Language-model tokens",
                                  ["model", "kind"], registry=r)
        self.llm_seconds = Histogram("jarvis_llm_seconds", "Language-model call latency", ["model"],
                                     buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64), registry=r)
        self.llm_budget_ratio = Gauge("jarvis_llm_budget_used_ratio", "Daily token budget used (0-1)", registry=r)

    def record_llm(self, call) -> None:
        """Hook for LLMService.on_call: one finished model call."""
        self.llm_requests.labels(call.model, call.outcome).inc()
        self.llm_seconds.labels(call.model).observe(call.latency_ms / 1000)
        if call.prompt_tokens:
            self.llm_tokens.labels(call.model, "prompt").inc(call.prompt_tokens)
        if call.completion_tokens:
            self.llm_tokens.labels(call.model, "completion").inc(call.completion_tokens)

    def refresh_process(self) -> None:
        with _PROCESS.oneshot():
            self.rss.set(_PROCESS.memory_info().rss)
            self.cpu.set(_PROCESS.cpu_percent(interval=None))
            self.threads.set(_PROCESS.num_threads())
            try:
                self.open_fds.set(_PROCESS.num_handles() if os.name == "nt" else _PROCESS.num_fds())
            except (psutil.Error, AttributeError):
                pass
        self.uptime.set(time.time() - _STARTED)

    def render(self) -> bytes:
        self.refresh_process()
        return generate_latest(self.registry)


async def monitor_event_loop(metrics: Metrics, interval: float = 1.0) -> None:
    """Measure how late the loop wakes us. Sustained lag means something is blocking it."""
    while True:
        before = time.perf_counter()
        await asyncio.sleep(interval)
        metrics.loop_lag.set(max(0.0, time.perf_counter() - before - interval))


def runtime_snapshot(metrics: Metrics, container: Any, ws_count: int) -> Dict[str, Any]:
    """Human/JSON version of the above for the dashboard System panel."""
    metrics.refresh_process()
    mem = _PROCESS.memory_info()
    stats = container.cache.stats
    pool = getattr(container.db.engine.pool, "checkedout", lambda: None)()
    return {
        "uptime_seconds": int(time.time() - _STARTED),
        "process": {"rss_mb": round(mem.rss / 1_048_576, 1), "threads": _PROCESS.num_threads(),
                    "cpu_percent": _PROCESS.cpu_percent(interval=None)},
        "event_loop_lag_ms": round(metrics.loop_lag._value.get() * 1000, 2),
        "websockets": ws_count,
        "cache": {"backend": container.cache.backend, "hits": stats.hits, "misses": stats.misses,
                  "errors": stats.errors, "fallback_active": stats.fallback_active,
                  "last_error": stats.last_error, "local_items": container.cache.local_items,
                  "local_item_cap": container.settings.cache_max_local_items},
        "database": {"dialect": container.db.engine.dialect.name, "pool_checked_out": pool},
        "event_bus": container.bus.kind,
    }
