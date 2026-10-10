"""Live event stream (WebSocket).

Security / resource rules:

* The token is NEVER in the URL (URLs end up in logs and browser history). The client
  must send ``{"token": "<access jwt>"}`` as its first message within 5 seconds.
* The Origin header is checked, so another website cannot open this socket with the
  user's browser (cross-site WebSocket hijacking).
* Global and per-principal connection caps bound memory; excess connections are refused.
* The token is re-validated periodically, so a logged-out session's socket is closed.
* Only admins receive events: they include approvals and conversation snippets.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jarvis_shared.log import get_logger

from jarvis_backend.auth.service import ADMIN

router = APIRouter()
log = get_logger(__name__)
AUTH_TIMEOUT_SECONDS = 5
REVALIDATE_SECONDS = 60
CLOSE_POLICY, CLOSE_TRY_LATER = 1008, 1013


def _origin_ok(ws: WebSocket) -> bool:
    c = ws.app.state.container
    origin = ws.headers.get("origin")
    if origin is None:
        return True  # non-browser client; it cannot be hijacked cross-site
    expected = c.settings.public_origin.rstrip("/")
    if expected:
        return origin.rstrip("/") == expected
    host = ws.headers.get("host", "")
    return origin.rstrip("/") in {f"http://{host}", f"https://{host}"}


@router.websocket("/ws/events")
async def events(ws: WebSocket) -> None:
    app = ws.app
    c = app.state.container
    registry: dict = app.state.ws_registry  # principal -> open count

    if not _origin_ok(ws):
        await ws.close(code=CLOSE_POLICY)
        return
    if app.state.ws_count() >= c.settings.max_ws_connections:
        await ws.close(code=CLOSE_TRY_LATER)
        return
    await ws.accept()

    principal = None
    sub = None
    try:
        try:
            first = json.loads(await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT_SECONDS))
            principal = await c.auth.authenticate(str(first.get("token", "")))
        except Exception:
            await ws.close(code=CLOSE_POLICY)
            return
        if principal.role != ADMIN:
            await ws.close(code=CLOSE_POLICY)
            return
        if registry.get(principal.subject, 0) >= c.settings.max_ws_per_principal:
            await ws.close(code=CLOSE_TRY_LATER)
            return

        registry[principal.subject] = registry.get(principal.subject, 0) + 1
        app.state.metrics.ws_connections.set(app.state.ws_count())
        await ws.send_json({"type": "hello"})
        sub = await c.bus.subscribe()

        last_check = asyncio.get_running_loop().time()
        while True:
            event = await sub.get(timeout=15)
            if event is not None:
                await ws.send_json(event)
            else:
                await ws.send_json({"type": "ping"})
            now = asyncio.get_running_loop().time()
            if now - last_check > REVALIDATE_SECONDS:
                last_check = now
                if not await c.auth._session_active(principal.sid):
                    await ws.close(code=CLOSE_POLICY)
                    return
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if sub is not None:
            await sub.close()
        if principal is not None and registry.get(principal.subject):
            registry[principal.subject] -= 1
            if registry[principal.subject] <= 0:
                registry.pop(principal.subject, None)
        app.state.metrics.ws_connections.set(app.state.ws_count())
