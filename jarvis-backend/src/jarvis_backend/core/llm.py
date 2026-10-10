"""LLM provider abstraction.

Agents depend only on :class:`LLMProvider`, so OpenAI / Anthropic / a local model is a
configuration swap. :class:`EchoLLM` keeps the whole pipeline runnable offline. The
current template agents do not call an LLM yet; this is the seam where they will.
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from pydantic import BaseModel


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
