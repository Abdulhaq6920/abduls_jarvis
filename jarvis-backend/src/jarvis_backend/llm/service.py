"""The one door to language models.

Agents never talk to a provider directly. They call :meth:`LLMService.complete`, which adds, in
order: the on/off switch, the daily token budget, model-role selection, a fallback model for
provider-side failures, usage accounting, metrics and a prompt-free log line.

**Prompts and completions are never logged.** Only model, outcome, latency, token counts and the
calling agent are.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Literal, Optional, Sequence

from jarvis_shared.log import get_logger, scrub

from jarvis_backend.config import Settings
from jarvis_backend.llm.budget import TokenBudget
from jarvis_backend.llm.provider import LLMProvider, OpenAICompatProvider
from jarvis_backend.llm.types import ChatMessage, ErrorKind, LLMError, LLMResponse, ToolSpec
from jarvis_backend.storage.kv import KeyValueStore

log = get_logger("jarvis.llm")

ModelRole = Literal["fast", "default", "reasoning"]
# Failures of the *model or its host*: worth trying the fallback model. Auth, credits, budget and
# bad requests would fail identically on any model, so they are not retried elsewhere.
_FALLBACK_KINDS: frozenset[ErrorKind] = frozenset(
    {"rate_limited", "timeout", "unavailable", "model", "invalid_response"})


@dataclass(frozen=True)
class CallRecord:
    model: str
    outcome: str  # "ok" or an ErrorKind
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    agent: str


class LLMService:
    def __init__(
        self, *, provider: Optional[LLMProvider], models: Dict[str, str], fallback_model: str = "",
        budget: Optional[TokenBudget] = None, max_tokens: int = 800, timeout: float = 40.0,
    ) -> None:
        self._provider = provider
        self._models = models
        self._fallback = fallback_model
        self._budget = budget
        self._max_tokens = max_tokens
        self._timeout = timeout
        # Set by the app so metrics stay out of this module's imports.
        self.on_call: Optional[Callable[[CallRecord], None]] = None
        self.calls = 0
        self.failures = 0
        self.last_error: Optional[Dict[str, Any]] = None

    @property
    def enabled(self) -> bool:
        return self._provider is not None

    def model_for(self, role: ModelRole) -> str:
        return self._models.get(role) or self._models["default"]

    async def complete(
        self, *, role: ModelRole = "default", messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (), max_tokens: Optional[int] = None, temperature: float = 0.3,
        timeout: Optional[float] = None, agent: str = "-",
    ) -> LLMResponse:
        if self._provider is None:
            raise LLMError("disabled")
        if self._budget is not None:
            await self._budget.check()

        primary = self.model_for(role)
        candidates = [primary] + ([self._fallback] if self._fallback and self._fallback != primary else [])
        last: Optional[LLMError] = None
        for index, model in enumerate(candidates):
            started = time.perf_counter()
            try:
                response = await self._provider.chat(
                    model=model, messages=messages, tools=tools, temperature=temperature,
                    max_tokens=min(max_tokens or self._max_tokens, self._max_tokens),
                    timeout=timeout or self._timeout)
            except LLMError as exc:
                last = exc
                self._observe(model, exc.kind, int((time.perf_counter() - started) * 1000), 0, 0, agent, exc)
                if exc.kind in _FALLBACK_KINDS and index + 1 < len(candidates):
                    continue
                raise
            self._observe(response.model or model, "ok", response.latency_ms, response.usage.prompt_tokens,
                          response.usage.completion_tokens, agent, None)
            if self._budget is not None:
                await self._budget.record(response.usage.total)
            return response
        assert last is not None  # pragma: no cover - candidates is never empty
        raise last

    def _observe(self, model: str, outcome: str, latency_ms: int, prompt: int, completion: int,
                 agent: str, error: Optional[LLMError]) -> None:
        self.calls += 1
        record = CallRecord(model, outcome, latency_ms, prompt, completion, agent)
        if error is None:
            log.info("llm call", extra={"model": model, "outcome": outcome, "latency_ms": latency_ms,
                                        "prompt_tokens": prompt, "completion_tokens": completion, "agent": agent})
        else:
            self.failures += 1
            self.last_error = {"kind": error.kind, "model": model, "at": time.time()}
            log.warning("llm call failed", extra={"model": model, "outcome": outcome, "latency_ms": latency_ms,
                                                  "agent": agent, "detail": scrub(error.detail)})
        if self.on_call is not None:
            try:
                self.on_call(record)
            except Exception:  # metrics must never break a request
                log.exception("llm metrics hook failed")

    async def status(self) -> Dict[str, Any]:
        budget = await self._budget.snapshot() if self._budget else None
        provider_state = getattr(getattr(self._provider, "breaker", None), "state", None)
        return {
            "enabled": self.enabled,
            "provider": getattr(self._provider, "name", None),
            "models": {role: self.model_for(role) for role in ("fast", "default", "reasoning")} if self.enabled else {},
            "fallback_model": self._fallback or None,
            "circuit": provider_state,
            "calls": self.calls, "failures": self.failures,
            "last_error": self.last_error,
            "budget": budget,
        }

    async def aclose(self) -> None:
        if self._provider is not None:
            await self._provider.aclose()


def build_llm_service(settings: Settings, kv: Optional[KeyValueStore]) -> LLMService:
    """AI on when a key is configured and not switched off; otherwise an inert service."""
    models = {"fast": settings.llm_model_fast, "default": settings.llm_model_default,
              "reasoning": settings.llm_model_reasoning}
    if not settings.llm_active:
        log.info("AI models disabled", extra={"reason": "switched off" if not settings.llm_enabled else "no key"})
        return LLMService(provider=None, models=models)
    provider = OpenAICompatProvider(
        base_url=settings.llm_base_url, api_key=settings.llm_key, timeout=settings.llm_timeout_seconds,
        max_concurrency=settings.llm_max_concurrency)
    return LLMService(
        provider=provider, models=models, fallback_model=settings.llm_model_fallback,
        budget=TokenBudget(kv, settings.llm_daily_token_limit), max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout_seconds)
