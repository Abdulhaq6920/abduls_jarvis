"""LLM provider abstraction.

Agents depend only on :class:`LLMProvider`, so OpenAI / Ollama / a local model is a
configuration swap. :class:`EchoLLM` keeps the whole pipeline runnable offline and is the
default in dev and tests. :class:`OpenAICompatibleLLM` is the real provider: it speaks the
OpenAI ``/v1/chat/completions`` JSON shape, which both OpenAI and a local Ollama server serve.

:func:`build_llm` selects between them from :class:`~jarvis_backend.config.Settings`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx
from jarvis_shared.log import get_logger
from pydantic import BaseModel

if TYPE_CHECKING:
    from jarvis_backend.config import Settings

log = get_logger(__name__)

# Honest, spoken-friendly fallback when the model cannot be reached. Never a fabricated
# answer -- the agent says it could not reach the model rather than inventing a response.
# Public because ``complete`` never raises: callers that need to tell "the model answered"
# from "the model is down" (the router) compare the reply against this sentence.
FALLBACK_REPLY = "I couldn't reach the language model just now, so I don't have an answer for that."

# Hosts a plaintext http:// base URL may legitimately use (a local Ollama server). Any other
# host over http:// is refused: the prompt and bounded local context (and a Bearer key, if
# set) would travel in cleartext. See :func:`_plaintext_remote`.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _plaintext_remote(base_url: str) -> bool:
    """True when the base URL is a plaintext http:// endpoint on a non-loopback host.

    The prompt itself -- the user task plus bounded allow-list names, memories and recent
    history -- crosses the wire on every request, so confidentiality must not depend on
    whether an API key happens to be set. Loopback http:// (a local Ollama server) is fine;
    any hosted endpoint must use https://.
    """
    try:
        parsed = urlparse(base_url)
        scheme, hostname = parsed.scheme, (parsed.hostname or "")
        # Touch .port too: urlparse accepts a non-numeric port lazily, but reading it
        # raises ValueError for e.g. "https://host:bad/v1". httpx would otherwise raise
        # httpx.InvalidURL only at request time, outside this guard.
        _ = parsed.port
    except ValueError:
        # A malformed base URL (an unterminated IPv6 literal "http://[::1", or a
        # non-numeric port) makes urlparse raise. Treat it as unsafe: refuse to send
        # rather than letting the error escape the provider's never-raise contract.
        return True
    if scheme != "http":
        return False
    return hostname not in _LOOPBACK_HOSTS


class LLMMessage(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(self, messages: List[LLMMessage]) -> str: ...


class EchoLLM:
    """Deterministic offline provider used in development and tests."""

    async def complete(self, messages: List[LLMMessage]) -> str:
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return f"Acknowledged: {user.strip()[:160]}"


class OpenAICompatibleLLM:
    """Real provider for any OpenAI-compatible ``/v1/chat/completions`` endpoint.

    Works for OpenAI (``https://api.openai.com/v1``) and a local Ollama server
    (``http://localhost:11434/v1``). A Bearer header is sent only when an API key is set,
    so Ollama (which needs none) works out of the box.

    Transport confidentiality: a remote plaintext http:// endpoint is refused before any
    request is made (the prompt and bounded local context would otherwise cross the wire in
    cleartext), independently of whether an API key is set. Loopback http:// (local Ollama)
    stays allowed. Config validation flags the same case as a fail-closed finding.

    Error policy: ``complete`` NEVER raises. On any transport, HTTP-status, JSON or
    response-shape error it logs the failure (without the API key -- it is a ``SecretStr``
    and is only unwrapped into the Authorization header) and returns a short honest
    fallback sentence. ``Supervisor.handle`` has no try/except around ``agent.handle``, so
    a raised exception would 500 the ``/assist`` request; returning a fallback guarantees a
    provider failure never crashes a request while keeping the honesty rule.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_tokens: int = 512,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._max_tokens = max_tokens

    async def complete(self, messages: List[LLMMessage]) -> str:
        if _plaintext_remote(self._base_url):
            # Refuse to send the prompt (and any Bearer key) over plaintext to a remote
            # host (config validation flags this too). Nothing leaves the process.
            log.warning("llm request refused: plaintext http to a remote host")
            return FALLBACK_REPLY
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": self._max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers
                )
                response.raise_for_status()
                data = response.json()
            content = data["choices"][0]["message"]["content"]
            # Normalise INSIDE the protected block: a well-formed OpenAI response always
            # carries a string here, but a non-conforming server (or a proxy) can return a
            # list/dict/number. Coercing it here means a truthy non-string value can never
            # raise out of this method -- the honesty contract is "complete never raises".
            if not isinstance(content, str):
                raise TypeError("content is not a string")
            text = content.strip()
        except (httpx.HTTPError, httpx.InvalidURL, KeyError, IndexError, TypeError,
                ValueError) as exc:
            # Log the class/message only -- never the payload or the key.
            log.warning("llm request failed", extra={"error": type(exc).__name__})
            return FALLBACK_REPLY
        return text or FALLBACK_REPLY


def build_llm(
    settings: Settings,
    *,
    timeout: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> LLMProvider:
    """Return the configured provider.

    ``EchoLLM`` for the default ``provider="echo"`` and for a real provider that has no
    usable base URL (so a half-configured prod env degrades to the safe offline default
    rather than failing). Otherwise an :class:`OpenAICompatibleLLM`.

    ``timeout``/``max_tokens`` override the agent budget for a caller whose call is not an
    agent answer. The router uses them: its classification returns one agent id and is made
    *before* the agent's own call, so it must not be able to burn the full agent timeout
    ahead of the first spoken word.
    """
    if settings.llm_provider == "echo" or not settings.llm_base_url_value:
        return EchoLLM()
    return OpenAICompatibleLLM(
        model=settings.llm_model,
        base_url=settings.llm_base_url_value,
        api_key=settings.secret("llm_api_key") or None,
        timeout=settings.llm_timeout_seconds if timeout is None else timeout,
        max_tokens=settings.llm_max_tokens if max_tokens is None else max_tokens,
    )
