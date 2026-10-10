"""ASGI middleware: request context, access logging, metrics, security headers, body cap.

Pure ASGI (not ``BaseHTTPMiddleware``) so streaming and WebSockets are untouched and no
request body is buffered.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Tuple

from jarvis_shared.log import get_logger, request_id_var

from jarvis_backend.observability import Metrics

log = get_logger("jarvis.access")
Scope, Receive, Send = Dict[str, Any], Callable[[], Awaitable[Dict]], Callable[[Dict], Awaitable[None]]
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# The dashboard is served by this app and loads nothing from elsewhere. No inline script or
# style is allowed, so an injected <script> or event-handler attribute cannot run.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def security_headers(prod: bool) -> List[Tuple[bytes, bytes]]:
    headers = [
        (b"content-security-policy", CSP.encode()),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"geolocation=(), camera=(), microphone=(), payment=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"cross-origin-resource-policy", b"same-origin"),
    ]
    if prod:
        headers.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
    return headers


class RequestContextMiddleware:
    def __init__(self, app: Any, *, metrics: Metrics, prod: bool) -> None:
        self.app, self.metrics = app, metrics
        self._static_headers = security_headers(prod)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_holder = {"code": 500}
        is_api = scope["path"].startswith("/api")
        self.metrics.in_flight.inc()

        async def send_wrapper(message: Dict) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                headers = list(message.get("headers", []))
                headers += self._static_headers
                headers.append((b"x-request-id", request_id.encode()))
                if is_api:  # API responses carry personal data and tokens: never cache them
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            route = getattr(scope.get("route"), "path", "unmatched")
            code = status_holder["code"]
            self.metrics.in_flight.dec()
            self.metrics.requests.labels(scope["method"], route, str(code)).inc()
            self.metrics.latency.labels(scope["method"], route).observe(elapsed)
            if code == 429:
                self.metrics.rate_limited.labels(route).inc()
            if route.startswith("/api/auth"):
                self.metrics.auth_events.labels(f"{route.rsplit('/', 1)[-1]}_{code}").inc()
            if route not in ("/healthz", "/readyz", "/metrics"):  # keep probes out of the logs
                log.info("request", extra={
                    "method": scope["method"], "route": route, "status": code,
                    "duration_ms": round(elapsed * 1000, 1),
                })
            request_id_var.reset(token)


class BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Reject oversized request bodies before they are read into memory (DoS guard).

    Checks Content-Length up front and also counts streamed (chunked) bytes, because a
    client can lie about, or omit, Content-Length.
    """

    def __init__(self, app: Any, *, max_bytes: int) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        received = 0
        started = False

        async def counting_receive() -> Dict:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge
            return message

        async def tracking_send(message: Dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if not started:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"detail":"Request body too large."}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
