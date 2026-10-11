"""Test doubles for the language-model layer. No network, no real provider."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Union

from jarvis_backend.llm.types import LLMError, LLMResponse, ToolCall, Usage


def resp(content: str = "", calls: Optional[List[ToolCall]] = None, tokens: int = 15, model: str = "test/model") -> LLMResponse:
    return LLMResponse(content=content, tool_calls=tuple(calls or ()), usage=Usage(tokens - 5, 5), model=model)


def call(name: str, args: Optional[Dict[str, Any]] = None, id: str = "call_1", valid: bool = True) -> ToolCall:
    return ToolCall(id=id, name=name, arguments=args or {}, valid=valid)


def wire(content: str = "", tool_calls: Optional[list] = None, usage: Optional[dict] = None, model: str = "m") -> dict:
    """An OpenAI-style chat-completions body, as the HTTP provider receives it."""
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"model": model, "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 11, "completion_tokens": 4}}


def wire_call(name: str, args: Union[dict, str], id: str = "call_1") -> dict:
    return {"id": id, "type": "function",
            "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}


Step = Union[LLMResponse, Exception, Callable[[Dict[str, Any]], LLMResponse]]


class ScriptedLLM:
    """Stands in for LLMService: returns scripted responses in order and records every call."""

    enabled = True

    def __init__(self, *script: Step) -> None:
        self.script: List[Step] = list(script)
        self.calls: List[Dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("ScriptedLLM ran out of responses")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(kwargs) if callable(step) else step

    def model_for(self, role: str) -> str:
        return f"test/{role}"


class FakeMemory:
    def __init__(self, items: Optional[List[str]] = None) -> None:
        self.items = list(items or [])
        self.added: List[str] = []

    async def search(self, query: str, limit: int = 5) -> List[Any]:
        return [SimpleNamespace(text=t) for t in self.items[:limit]]

    async def add(self, text: str, **kwargs: Any) -> None:
        self.added.append(text)
        self.items.append(text)


def llm_error(kind: str = "unavailable") -> LLMError:
    return LLMError(kind, "test")  # type: ignore[arg-type]
