"""Provider-neutral types for chat models with tool calling.

Nothing here knows about Hugging Face or HTTP. Agents talk in these types; a provider
translates them to and from a wire format.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Tuple

Role = Literal["system", "user", "assistant", "tool"]
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ToolSpec:
    """A function the model may ask us to call. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})

    def to_wire(self) -> Dict[str, Any]:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]
    # False when the model sent arguments that were not a JSON object. The agent loop reports
    # that back to the model instead of executing anything.
    valid: bool = True


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str = ""
    tool_calls: Tuple[ToolCall, ...] = ()
    tool_call_id: str = ""

    def to_wire(self) -> Dict[str, Any]:
        if self.role == "tool":
            return {"role": "tool", "tool_call_id": self.tool_call_id, "content": self.content}
        wire: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            wire["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in self.tool_calls
            ]
        return wire


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: Tuple[ToolCall, ...]
    usage: Usage
    model: str
    finish_reason: str = ""
    latency_ms: int = 0


# What went wrong, in terms an agent can act on. Never contains secrets or prompt text.
ErrorKind = Literal[
    "disabled", "budget", "auth", "credits", "rate_limited", "timeout",
    "unavailable", "model", "bad_request", "invalid_response", "circuit_open",
]

_SPOKEN = {
    "disabled": "my language model is switched off",
    "budget": "today's AI usage limit has been reached",
    "auth": "the language model service rejected my credentials",
    "credits": "the language model account is out of credits",
    "rate_limited": "the language model service is busy",
    "timeout": "the language model took too long to answer",
    "unavailable": "the language model service is unavailable",
    "model": "the selected language model is not available",
    "bad_request": "the language model could not process that request",
    "invalid_response": "the language model sent an unusable answer",
    "circuit_open": "the language model service is temporarily paused after repeated errors",
}


class LLMError(Exception):
    """A model call failed. ``spoken`` is safe to read aloud; ``detail`` is for logs only."""

    def __init__(self, kind: ErrorKind, detail: str = "", *, status: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind: ErrorKind = kind
        self.detail = detail
        self.status = status
        self.retry_after = retry_after

    @property
    def spoken(self) -> str:
        return _SPOKEN.get(self.kind, "the language model is unavailable")
