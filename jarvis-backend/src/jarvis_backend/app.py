"""FastAPI application factory.

``create_app()`` builds the app; ``lifespan`` owns start-up and graceful shutdown of
every resource (database pool, Redis, event bus, scheduler, background tasks).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jarvis_shared.log import get_logger, request_id_var
from starlette.middleware.trustedhost import TrustedHostMiddleware

from jarvis_backend.api import (
    routes_agents,
    routes_approvals,
    routes_assist,
    routes_auth,
    routes_devices,
    routes_jobs,
    routes_memory,
    routes_system,
    ws,
)
from jarvis_backend.config import Settings, get_settings
from jarvis_backend.container import Container
from jarvis_backend.jobs import JobScheduler
from jarvis_backend.maintenance import run_maintenance
from jarvis_backend.middleware import BodyLimitMiddleware, RequestContextMiddleware
from jarvis_backend.observability import Metrics, monitor_event_loop

log = get_logger(__name__)
WEB_DIR = Path(__file__).parent / "web"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    metrics = Metrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await Container.create(settings)
        app.state.container = container
        background = [asyncio.create_task(monitor_event_loop(metrics), name="loop-monitor")]
        if settings.run_scheduler:
            container.scheduler = JobScheduler(container.job_runner, settings.timezone,
                                               maintenance=lambda: run_maintenance(container))
            container.scheduler.start()
        await container.audit.record("backend_started", version=settings.version, env=settings.env)
        log.info("backend started")
        try:
            yield
        finally:
            log.info("backend stopping")
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await container.audit.record("backend_stopped")
            await container.close()

    app = FastAPI(
        title="JARVIS Backend", version=settings.version, lifespan=lifespan,
        # The interactive docs list every endpoint: useful in dev, needless exposure in prod.
        docs_url=None if settings.is_prod else "/api/docs",
        redoc_url=None, openapi_url=None if settings.is_prod else "/api/openapi.json",
    )
    app.state.metrics = metrics
    app.state.ws_registry = {}
    app.state.ws_count = lambda: sum(app.state.ws_registry.values())

    # Added last = runs first. Order: host check -> request context -> body cap -> routes.
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(RequestContextMiddleware, metrics=metrics, prod=settings.is_prod)
    # Loopback names are always accepted: the container health check and local tooling use
    # them, and they cannot be abused for DNS rebinding (a hostile page sends ITS hostname).
    allowed_hosts = list(dict.fromkeys(settings.host_list + ["localhost", "127.0.0.1"]))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    # Deliberately NO CORS middleware: the dashboard is same-origin, so no other website
    # is ever granted permission to call this API from a browser.

    for module in (routes_system, routes_auth, routes_devices, routes_assist, routes_agents,
                   routes_memory, routes_approvals, routes_jobs, ws):
        app.include_router(module.router)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the detail server-side; the client only gets an opaque id to quote to you.
        log.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse({"detail": "Internal server error.", "request_id": request_id_var.get()},
                            status_code=500)

    if WEB_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=WEB_DIR), name="ui")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app
