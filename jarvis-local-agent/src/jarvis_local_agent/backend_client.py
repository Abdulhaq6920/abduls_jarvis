"""Authenticated client for the JARVIS backend.

* Identity: a registered **device** (id + secret) exchanged for a short-lived JWT, renewed
  automatically before it expires and once more if the server answers 401.
* Transport: credentials are refused over plain ``http://`` to any non-loopback host.
* Privacy: only text crosses this boundary. Microphone audio never leaves the machine.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx
from jarvis_shared.agents import AgentId
from jarvis_shared.log import get_logger
from jarvis_shared.messages import AssistRequest, AssistResponse
from jarvis_shared.voice import AgentVoiceProfile

from jarvis_local_agent import __version__
from jarvis_local_agent.config import RuntimeConfig

log = get_logger(__name__)
_RENEW_MARGIN_SECONDS = 60


class BackendError(Exception):
    """The backend refused or could not serve a request. Message is safe to log."""


class BackendClient:
    def __init__(self, config: RuntimeConfig, timeout: float = 30.0) -> None:
        if not config.backend_is_safe_transport():
            raise BackendError(
                "Refusing to send device credentials over plain http:// to a remote host. "
                "Use an https:// JARVIS_BACKEND_URL (or set JARVIS_ALLOW_INSECURE_BACKEND=true "
                "for a network you fully trust)."
            )
        self._config = config
        self._http = httpx.AsyncClient(base_url=config.backend_url.rstrip("/"), timeout=timeout,
                                       headers={"User-Agent": f"jarvis-local-agent/{__version__}"})
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._login_lock = asyncio.Lock()
        self._voice_cache: Dict[str, AgentVoiceProfile] = {}

    @property
    def has_credentials(self) -> bool:
        return bool(self._config.device_id and self._config.secret())

    # ------------------------------------------------------------------------- auth
    async def _login(self) -> None:
        async with self._login_lock:
            if self._token and time.monotonic() < self._expires_at - _RENEW_MARGIN_SECONDS:
                return  # another task just renewed it
            if not self.has_credentials:
                raise BackendError("No device credentials configured (see credentials.py).")
            response = await self._http.post("/api/auth/device-token", json={
                "device_id": self._config.device_id, "secret": self._config.secret()})
            if response.status_code != 200:
                self._token = None
                raise BackendError(f"Device login rejected ({response.status_code}).")
            body = response.json()
            self._token = body["access_token"]
            self._expires_at = time.monotonic() + int(body.get("expires_in", 600))
            log.info("device authenticated")

    async def _request(self, method: str, path: str, *, json: Any = None, retry: bool = True) -> httpx.Response:
        if not self._token or time.monotonic() >= self._expires_at - _RENEW_MARGIN_SECONDS:
            await self._login()
        response = await self._http.request(method, path, json=json,
                                            headers={"Authorization": f"Bearer {self._token}"})
        if response.status_code == 401 and retry:
            self._token = None  # revoked or expired early: one fresh login, then give up
            return await self._request(method, path, json=json, retry=False)
        return response

    async def _ok(self, method: str, path: str, *, json: Any = None) -> Any:
        response = await self._request(method, path, json=json)
        if response.status_code >= 400:
            raise BackendError(f"{method} {path} -> {response.status_code}")
        return None if response.status_code == 204 else response.json()

    # --------------------------------------------------------------------- endpoints
    async def ping(self) -> bool:
        try:
            return (await self._http.get("/healthz")).status_code == 200
        except httpx.HTTPError:
            return False

    async def assist(self, req: AssistRequest) -> AssistResponse:
        return AssistResponse.model_validate(
            await self._ok("POST", "/api/assist", json=req.model_dump(mode="json")))

    async def voice_profile(self, agent_id: AgentId) -> AgentVoiceProfile:
        key = agent_id.value
        if key not in self._voice_cache:
            self._voice_cache[key] = AgentVoiceProfile.model_validate(
                await self._ok("GET", f"/api/voices/{key}"))
        return self._voice_cache[key]

    def forget_voices(self) -> None:
        self._voice_cache.clear()

    async def granted_approvals(self) -> List[Dict[str, Any]]:
        return await self._ok("GET", "/api/approvals/granted")

    async def claim(self, approval_id: str) -> bool:
        response = await self._request("POST", f"/api/approvals/{approval_id}/claim")
        return response.status_code == 200

    async def report(self, approval_id: str, *, ok: bool, output: str, error: str) -> None:
        await self._ok("POST", f"/api/approvals/{approval_id}/result",
                       json={"ok": ok, "output": output[:8000], "error": error[:2000]})

    async def heartbeat(self, *, state: str, mic_paused: bool, wake_word: str) -> None:
        await self._ok("POST", "/api/runtime/heartbeat", json={
            "conversation_state": state, "microphone_paused": mic_paused,
            "wake_word": wake_word, "version": __version__})

    async def close(self) -> None:
        await self._http.aclose()
