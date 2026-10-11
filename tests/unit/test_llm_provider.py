"""The Hugging Face / OpenAI-compatible provider: parsing, retries, circuit breaker, secrecy."""
import json

import httpx
import pytest
from jarvis_backend.llm.provider import CircuitBreaker, OpenAICompatProvider, parse_completion
from jarvis_backend.llm.types import ChatMessage, LLMError, ToolSpec

from tests.llm_fakes import wire, wire_call

KEY = "hf_test_key_0123456789abcdefghijklmnop"
MESSAGES = [ChatMessage("user", "hello")]


async def _no_sleep(_: float) -> None:
    return None


def provider(handler, **kwargs) -> tuple[OpenAICompatProvider, list]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return OpenAICompatProvider(base_url="https://router.example/v1", api_key=KEY, max_retries=2,
                                transport=httpx.MockTransport(wrapped), sleep=_no_sleep, **kwargs), seen


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=wire("hi there"))


# ------------------------------------------------------------------------ parsing
def test_parses_text_usage_and_strips_reasoning_tags():
    r = parse_completion(wire("<think>secret plan</think>Hello!", usage={"prompt_tokens": 7, "completion_tokens": 3}), "m", 12)
    assert r.content == "Hello!" and r.usage.total == 10 and r.latency_ms == 12 and not r.tool_calls


def test_parses_tool_calls_and_flags_unparsable_arguments():
    body = wire("", [wire_call("recall", {"query": "car"}, "a"), wire_call("remember", "{not json", "b"),
                     wire_call("bad name!", {}, "c"), wire_call("x", "[1,2]", "d")])
    r = parse_completion(body, "m", 1)
    by_id = {c.id: c for c in r.tool_calls}
    assert set(by_id) == {"a", "b", "d"}                  # the badly named tool is dropped
    assert by_id["a"].arguments == {"query": "car"} and by_id["a"].valid
    assert not by_id["b"].valid and by_id["b"].arguments == {}
    assert not by_id["d"].valid                           # arguments must be an object


@pytest.mark.parametrize("bad", [{}, {"choices": []}, {"choices": [{"message": "str"}]}, None, "x"])
def test_malformed_bodies_are_errors_not_guesses(bad):
    with pytest.raises(LLMError) as raised:
        parse_completion(bad, "m", 1)
    assert raised.value.kind == "invalid_response"


# ------------------------------------------------------------------------ requests
async def test_request_shape_and_authentication():
    p, seen = provider(ok)
    result = await p.chat(model="openai/gpt-oss-120b:fastest", messages=MESSAGES,
                          tools=[ToolSpec("recall", "find", {"type": "object", "properties": {}})], max_tokens=50)
    assert result.content == "hi there"
    req = seen[0]
    assert req.url.path.endswith("/chat/completions") and req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["model"] == "openai/gpt-oss-120b:fastest" and body["max_tokens"] == 50 and body["stream"] is False
    assert body["tools"][0]["function"]["name"] == "recall" and body["tool_choice"] == "auto"
    await p.aclose()


async def test_redirects_are_never_followed_so_the_key_cannot_leak():
    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    p, seen = provider(redirect)
    with pytest.raises(LLMError):
        await p.chat(model="m", messages=MESSAGES)
    assert {r.url.host for r in seen} == {"router.example"}


async def test_transient_errors_are_retried_then_succeed():
    attempts = iter([httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "slow down"}),
                     httpx.Response(503, json={"error": "busy"}), httpx.Response(200, json=wire("finally"))])
    p, seen = provider(lambda r: next(attempts))
    assert (await p.chat(model="m", messages=MESSAGES)).content == "finally"
    assert len(seen) == 3 and p.breaker.state == "closed"


async def test_gives_up_after_retries_and_counts_one_failure():
    p, seen = provider(lambda r: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(LLMError) as raised:
        await p.chat(model="m", messages=MESSAGES)
    assert raised.value.kind == "unavailable" and len(seen) == 3


async def test_timeouts_are_classified():
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    p, _ = provider(slow)
    with pytest.raises(LLMError) as raised:
        await p.chat(model="m", messages=MESSAGES)
    assert raised.value.kind == "timeout"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (402, "credits"), (404, "model"), (400, "bad_request"), (422, "bad_request")])
async def test_client_errors_are_not_retried(status, kind):
    p, seen = provider(lambda r: httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(LLMError) as raised:
        await p.chat(model="m", messages=MESSAGES)
    assert raised.value.kind == kind and len(seen) == 1


async def test_out_of_credits_opens_the_breaker_so_later_calls_fail_fast():
    p, seen = provider(lambda r: httpx.Response(402, json={"error": "You have no remaining credits."}))
    with pytest.raises(LLMError) as first:
        await p.chat(model="m", messages=MESSAGES)
    assert first.value.kind == "credits"
    with pytest.raises(LLMError) as second:
        await p.chat(model="m", messages=MESSAGES)
    assert second.value.kind == "circuit_open" and len(seen) == 1   # no second network call
    assert "out of credits" in second.value.spoken                  # the user still hears the real reason


async def test_a_bad_request_does_not_trip_the_breaker():
    p, _ = provider(lambda r: httpx.Response(400, json={"error": "bad"}))
    for _ in range(6):
        with pytest.raises(LLMError) as raised:
            await p.chat(model="m", messages=MESSAGES)
        assert raised.value.kind == "bad_request"
    assert p.breaker.state == "closed"


async def test_error_details_are_scrubbed_before_they_can_reach_a_log():
    body = {"error": {"message": "Bearer abcdefghijklmnopqrstuv rejected; api_key=supersecretvalue"}}
    p, _ = provider(lambda r: httpx.Response(401, json=body))
    with pytest.raises(LLMError) as raised:
        await p.chat(model="m", messages=MESSAGES)
    assert "abcdefghijklmnopqrstuv" not in raised.value.detail and "supersecretvalue" not in raised.value.detail
    assert KEY not in str(raised.value) and KEY not in repr(raised.value)


# ------------------------------------------------------------------ circuit breaker
def test_breaker_opens_then_allows_exactly_one_probe_then_closes():
    now = [0.0]
    b = CircuitBreaker(threshold=3, cooldown=30, clock=lambda: now[0])
    for _ in range(3):
        assert b.allow()
        b.failure()
    assert b.state == "open" and not b.allow()
    now[0] = 31
    assert b.state == "half_open" and b.allow() and not b.allow()   # a single trial request
    b.success()
    assert b.state == "closed" and b.allow()


def test_failed_probe_reopens_the_breaker():
    now = [0.0]
    b = CircuitBreaker(threshold=1, cooldown=10, clock=lambda: now[0])
    b.allow()
    b.failure()
    now[0] = 11
    assert b.allow()
    b.failure()
    assert b.state == "open"
