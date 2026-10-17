"""The LLM provider factory and the OpenAI-compatible provider's request contract + safe-fallback policy."""
import httpx
import pytest
from jarvis_backend.config import Settings
from jarvis_backend.core.llm import EchoLLM, LLMMessage, OpenAICompatibleLLM, build_llm


def test_build_llm_defaults_to_echo():
    assert isinstance(build_llm(Settings(env="dev")), EchoLLM)


def test_build_llm_falls_back_to_echo_without_base_url():
    # A real provider selected but half-configured (no base URL) degrades to the safe default.
    assert isinstance(build_llm(Settings(env="dev", llm_provider="openai")), EchoLLM)


def test_build_llm_builds_real_provider_when_configured():
    s = Settings(env="dev", llm_provider="ollama", llm_base_url="http://localhost:11434/v1")
    assert isinstance(build_llm(s), OpenAICompatibleLLM)


async def test_echo_llm_echoes_the_last_user_message():
    reply = await EchoLLM().complete([LLMMessage(role="user", content="analyse this")])
    assert "analyse this" in reply


async def test_openai_provider_returns_fallback_on_network_error_and_never_raises():
    # Unroutable base URL -> httpx raises internally -> provider returns the honest fallback.
    llm = OpenAICompatibleLLM(model="x", base_url="http://127.0.0.1:1/v1", timeout=0.5)
    reply = await llm.complete([LLMMessage(role="user", content="hi")])
    assert "couldn't reach" in reply.lower()


# --- deterministic transport contract (no real network) ------------------------------------
def _mock_client(monkeypatch, handler):
    """Make OpenAICompatibleLLM use an httpx.MockTransport driven by ``handler``.

    ``handler`` receives the outgoing ``httpx.Request`` and returns an ``httpx.Response``.
    """
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return handler(request)

    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("jarvis_backend.core.llm.httpx.AsyncClient", _factory)
    return captured


async def test_provider_posts_openai_shape_with_bearer_and_parses_content(monkeypatch):
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "  hello world  "}}]})

    captured = _mock_client(monkeypatch, handler)
    llm = OpenAICompatibleLLM(model="gpt-4o-mini", base_url="https://api.openai.com/v1",
                              api_key="secret-key", max_tokens=256)
    reply = await llm.complete([
        LLMMessage(role="system", content="be brief"),
        LLMMessage(role="user", content="hi"),
    ])

    assert reply == "hello world"  # parsed from choices[0].message.content and stripped
    req = captured["request"]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer secret-key"
    body = json.loads(req.content)
    assert body["model"] == "gpt-4o-mini" and body["max_tokens"] == 256
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]


async def test_provider_omits_authorization_header_without_a_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    captured = _mock_client(monkeypatch, handler)
    llm = OpenAICompatibleLLM(model="llama3.1", base_url="http://localhost:11434/v1")
    reply = await llm.complete([LLMMessage(role="user", content="hi")])

    assert reply == "ok"
    assert "authorization" not in {k.lower() for k in captured["request"].headers}


async def test_build_llm_budget_overrides_apply(monkeypatch):
    # The router's classification is serialized AHEAD of the agent's own call, so it is
    # built with a short timeout and a tiny token cap instead of the agent allowance.
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "VISION"}}]})

    captured = _mock_client(monkeypatch, handler)
    s = Settings(env="dev", llm_provider="ollama", llm_base_url="http://localhost:11434/v1")
    llm = build_llm(s, timeout=s.router_timeout_seconds, max_tokens=s.router_max_tokens)
    await llm.complete([LLMMessage(role="user", content="hi")])

    assert s.router_max_tokens < s.llm_max_tokens
    assert s.router_timeout_seconds < s.llm_timeout_seconds
    assert json.loads(captured["request"].content)["max_tokens"] == s.router_max_tokens
    assert captured["request"].extensions["timeout"]["connect"] == s.router_timeout_seconds


async def test_provider_falls_back_on_http_status_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    _mock_client(monkeypatch, handler)
    llm = OpenAICompatibleLLM(model="x", base_url="https://api.openai.com/v1", api_key="k")
    reply = await llm.complete([LLMMessage(role="user", content="hi")])
    assert "couldn't reach" in reply.lower()


@pytest.mark.parametrize("content", [["unexpected"], {"text": "x"}, 42, True])
async def test_provider_falls_back_on_non_string_content_and_never_raises(monkeypatch, content):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    _mock_client(monkeypatch, handler)
    llm = OpenAICompatibleLLM(model="x", base_url="https://api.openai.com/v1", api_key="k")
    reply = await llm.complete([LLMMessage(role="user", content="hi")])
    assert "couldn't reach" in reply.lower()


async def test_provider_falls_back_on_empty_string_content(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]})

    _mock_client(monkeypatch, handler)
    llm = OpenAICompatibleLLM(model="x", base_url="https://api.openai.com/v1")
    reply = await llm.complete([LLMMessage(role="user", content="hi")])
    assert "couldn't reach" in reply.lower()


@pytest.mark.parametrize("base_url", ["http://[::1", "http://[bad", "http://][", "https://host:bad/v1"])
async def test_provider_never_raises_on_malformed_keyed_base_url(monkeypatch, base_url):
    # A malformed base URL makes urlparse raise; the keyed-plaintext preflight must swallow
    # it and return the honest fallback -- complete() NEVER raises, even before the request.
    sent = {"called": False}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        sent["called"] = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "leaked"}}]})

    _mock_client(monkeypatch, handler)
    llm = OpenAICompatibleLLM(model="x", base_url=base_url, api_key="secret")
    reply = await llm.complete([LLMMessage(role="user", content="hi")])
    assert sent["called"] is False
    assert "couldn't reach" in reply.lower()


@pytest.mark.parametrize("base_url", ["http://[::1", "http://[bad", "http://][", "https://host:bad/v1"])
async def test_provider_never_raises_on_malformed_base_url_without_a_key(monkeypatch, base_url):
    # A malformed base URL makes urlparse raise even without a key; the plaintext preflight
    # must swallow it and return the honest fallback -- complete() NEVER raises.
    sent = {"called": False}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        sent["called"] = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "leaked"}}]})

    _mock_client(monkeypatch, handler)
    llm = OpenAICompatibleLLM(model="x", base_url=base_url)
    reply = await llm.complete([LLMMessage(role="user", content="hi")])
    assert sent["called"] is False
    assert "couldn't reach" in reply.lower()


@pytest.mark.parametrize("base_url", ["http://[::1", "https://host:bad/v1"])
def test_malformed_base_url_is_flagged_not_crashing_in_config(monkeypatch, tmp_path, base_url):
    # A malformed URL (bad IPv6 literal, or an https:// URL with a non-numeric port) must not
    # abort config validation: findings() fails closed with the plaintext finding instead of
    # letting urlparse's lazy ValueError escape. No key required.
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    s = Settings(env="dev", llm_provider="openai", llm_base_url=base_url)
    assert "llm_plaintext_remote" in {f.code for f in s.findings()}


async def test_provider_refuses_to_send_prompt_over_remote_plaintext_http_without_a_key(monkeypatch):
    # The prompt leaks over plaintext even with no API key -- a remote http:// request must
    # never leave the process regardless of whether a key is set.
    sent = {"called": False}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        sent["called"] = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "leaked"}}]})

    _mock_client(monkeypatch, handler)
    llm = OpenAICompatibleLLM(model="x", base_url="http://api.example.com/v1")  # no api_key
    reply = await llm.complete([LLMMessage(role="user", content="hi")])
    assert sent["called"] is False
    assert "couldn't reach" in reply.lower()


async def test_provider_refuses_to_send_over_remote_plaintext_http_with_a_key(monkeypatch):
    # A keyed request to a remote http:// host must never leave the process either.
    sent = {"called": False}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        sent["called"] = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "leaked"}}]})

    _mock_client(monkeypatch, handler)
    llm = OpenAICompatibleLLM(model="x", base_url="http://api.example.com/v1", api_key="secret")
    reply = await llm.complete([LLMMessage(role="user", content="hi")])
    assert sent["called"] is False
    assert "couldn't reach" in reply.lower()


def test_unkeyed_remote_plaintext_http_is_a_config_error_in_prod(monkeypatch, tmp_path):
    # Remote plaintext is a prod error even with no API key set (prompt confidentiality).
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="plaintext"):
        Settings(env="prod", jwt_secret="x" * 48, data_key="y" * 48, redis_url="rediss://h/0",
                 database_url="postgresql://u:p@db/x", allowed_hosts="example.com",
                 public_origin="https://example.com", cookie_secure=True,
                 metrics_token="z" * 32, llm_provider="openai",
                 llm_base_url="http://api.example.com/v1")


def test_keyed_remote_plaintext_http_is_a_config_error_in_prod(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="plaintext"):
        Settings(env="prod", jwt_secret="x" * 48, data_key="y" * 48, redis_url="rediss://h/0",
                 database_url="postgresql://u:p@db/x", allowed_hosts="example.com",
                 public_origin="https://example.com", cookie_secure=True,
                 metrics_token="z" * 32, llm_provider="openai",
                 llm_base_url="http://api.example.com/v1", llm_api_key="k" * 20)


def test_loopback_plaintext_http_is_allowed(monkeypatch, tmp_path):
    # Local Ollama over loopback http:// (with or without a key) must not trip the finding.
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    s = Settings(env="dev", llm_provider="ollama", llm_base_url="http://localhost:11434/v1",
                 llm_api_key="k" * 20)
    assert "llm_plaintext_remote" not in {f.code for f in s.findings()}
