"""OpenAI-compatible chat provider (Hugging Face Inference Providers by default).

Hardening, in the order a request meets it:

* a **circuit breaker** stops hammering a service that is down, out of credits or rejecting
  our key (those trip it for longer, because retrying cannot help);
* a **concurrency cap** bounds simultaneous calls;
* **retries with jittered exponential backoff** for transient failures only (429, 5xx,
  timeouts, network), honouring ``Retry-After``;
* redirects are **never followed**, so the API key cannot be forwarded to another host;
* error bodies are scrubbed and truncated before they can reach a log.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

import httpx
from jarvis_shared.log import get_logger, scrub

from jarvis_backend.llm.types import (
    TOOL_NAME,
    ChatMessage,
    ErrorKind,
    LLMError,
    LLMResponse,
    ToolCall,
    ToolSpec,
    Usage,
)

log = get_logger(__name__)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_RETRYABLE: frozenset[ErrorKind] = frozenset({"rate_limited", "timeout", "unavailable"})
_MAX_DETAIL = 240


class LLMProvider(Protocol):
    name: str

    async def chat(
        self, *, model: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
        max_tokens: int = 512, temperature: float = 0.3, timeout: Optional[float] = None,
    ) -> LLMResponse: ...

    async def aclose(self) -> None: ...


class CircuitBreaker:
    """closed -> (N failures) -> open -> (cooldown) -> half-open -> (success) -> closed."""

    def __init__(self, threshold: int = 4, cooldown: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._threshold, self._cooldown, self._clock = threshold, cooldown, clock
        self._failures = 0
        self._open_until = 0.0
        self._trial_in_flight = False
        self.cause: Optional[ErrorKind] = None  # what last opened the circuit

    @property
    def state(self) -> str:
        if self._open_until == 0.0:
            return "closed"
        return "open" if self._clock() < self._open_until else "half_open"

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self._trial_in_flight:
            self._trial_in_flight = True  # exactly one probe request goes through
            return True
        return False

    def success(self) -> None:
        self._failures, self._open_until, self._trial_in_flight = 0, 0.0, False

    def release(self) -> None:
        self._trial_in_flight = False

    def failure(self, cooldown: Optional[float] = None, cause: Optional[ErrorKind] = None) -> None:
        self._trial_in_flight = False
        self._failures += 1
        self.cause = cause
        if cooldown is not None or self._failures >= self._threshold:
            self._open_until = self._clock() + (cooldown if cooldown is not None else self._cooldown)

    def retry_in(self) -> float:
        return max(0.0, self._open_until - self._clock())


def _classify(status: int) -> ErrorKind:
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "credits"
    if status == 404:
        return "model"
    if status in (408, 504):
        return "timeout"
    if status == 429:
        return "rate_limited"
    if status in (400, 413, 422):
        return "bad_request"
    return "unavailable"


def _error_detail(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
        err = body.get("error", body) if isinstance(body, dict) else body
        text = err.get("message", json.dumps(err)) if isinstance(err, dict) else str(err)
    except (ValueError, AttributeError):
        text = response.text
    return scrub(str(text))[:_MAX_DETAIL].replace("\n", " ")


def parse_completion(data: Any, model: str, latency_ms: int) -> LLMResponse:
    """Validate and convert a chat-completions body. Anything unexpected is an error, not a guess."""
    try:
        choice = data["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise TypeError("message is not an object")
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("invalid_response", "missing choices[0].message") from exc

    content = _THINK.sub("", str(message.get("content") or "")).strip()
    calls: List[ToolCall] = []
    for index, raw in enumerate(message.get("tool_calls") or []):
        fn = raw.get("function") if isinstance(raw, dict) else None
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "")
        if not TOOL_NAME.match(name):
            continue
        arguments, valid = fn.get("arguments"), True
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments, valid = {}, False
        if not isinstance(arguments, dict):
            arguments, valid = {}, False
        calls.append(ToolCall(id=str(raw.get("id") or f"call_{index}")[:64], name=name,
                              arguments=arguments, valid=valid))

    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    return LLMResponse(
        content=content, tool_calls=tuple(calls), model=str(data.get("model") or model),
        usage=Usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)),
        finish_reason=str(choice.get("finish_reason") or ""), latency_ms=latency_ms,
    )


class OpenAICompatProvider:
    name = "openai-compatible"

    def __init__(
        self, *, base_url: str, api_key: str, timeout: float = 40.0, max_concurrency: int = 4,
        max_retries: int = 2, breaker: Optional[CircuitBreaker] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._timeout, self._max_retries, self._sleep = timeout, max_retries, sleep
        self._slots = asyncio.Semaphore(max(1, max_concurrency))
        self.breaker = breaker or CircuitBreaker()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), transport=transport, follow_redirects=False,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                     "User-Agent": "jarvis-backend"},
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    async def chat(
        self, *, model: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = (),
        max_tokens: int = 512, temperature: float = 0.3, timeout: Optional[float] = None,
    ) -> LLMResponse:
        body: Dict[str, Any] = {
            "model": model, "messages": [m.to_wire() for m in messages],
            "max_tokens": max_tokens, "temperature": temperature, "stream": False,
        }
        if tools:
            body["tools"] = [t.to_wire() for t in tools]
            body["tool_choice"] = "auto"

        if not self.breaker.allow():
            raise LLMError("circuit_open", f"retry in {self.breaker.retry_in():.0f}s", cause=self.breaker.cause)

        last: Optional[LLMError] = None
        for attempt in range(self._max_retries + 1):
            try:
                async with self._slots:
                    response = await self._send(body, timeout)
                self.breaker.success()
                return response
            except asyncio.CancelledError:
                self.breaker.release()  # never leave a half-open probe marked "in flight"
                raise
            except LLMError as exc:
                last = exc
                if exc.kind in ("auth", "credits"):
                    self.breaker.failure(cooldown=60.0, cause=exc.kind)  # retrying cannot help
                    raise
                if exc.kind not in _RETRYABLE:
                    # A bad request is our bug, not the service's: it must not trip the breaker.
                    if exc.kind in ("bad_request", "model", "invalid_response"):
                        self.breaker.success()
                    raise
                if attempt == self._max_retries:
                    self.breaker.failure(cause=exc.kind)
                    raise
                await self._sleep(self._backoff(attempt, exc.retry_after))
        assert last is not None  # pragma: no cover - the loop always returns or raises
        raise last

    async def _send(self, body: Dict[str, Any], timeout: Optional[float]) -> LLMResponse:
        started = time.perf_counter()
        try:
            reply = await self._client.post(
                "/chat/completions", json=body,
                timeout=httpx.Timeout(timeout or self._timeout, connect=10.0))
        except httpx.TimeoutException as exc:
            raise LLMError("timeout", type(exc).__name__) from None
        except httpx.HTTPError as exc:
            raise LLMError("unavailable", type(exc).__name__) from None

        latency_ms = int((time.perf_counter() - started) * 1000)
        if reply.is_success:
            try:
                data = reply.json()
            except ValueError:
                raise LLMError("invalid_response", "body is not JSON") from None
            return parse_completion(data, body["model"], latency_ms)

        retry_after: Optional[float] = None
        try:
            retry_after = float(reply.headers.get("Retry-After", ""))
        except ValueError:
            pass
        raise LLMError(_classify(reply.status_code), _error_detail(reply),
                       status=reply.status_code, retry_after=retry_after)

    @staticmethod
    def _backoff(attempt: int, retry_after: Optional[float]) -> float:
        delay = min(5.0, 0.5 * (2 ** attempt))
        if retry_after is not None:
            delay = min(5.0, max(delay, retry_after))
        return delay * random.uniform(0.75, 1.25)  # noqa: S311 - jitter, not security

    async def aclose(self) -> None:
        await self._client.aclose()
