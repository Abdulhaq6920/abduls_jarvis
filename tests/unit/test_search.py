"""The search provider factory, the generic JSON provider's request/parse contract, and its
never-raise + transport-safety policy. Mirrors tests/unit/test_llm.py."""
import httpx
import pytest
from jarvis_backend.config import Settings
from jarvis_backend.core.search import (
    DisabledSearch,
    GenericJsonSearch,
    SearchResult,
    build_search,
)

# A DuckDuckGo-shaped body: an abstract (instant answer) plus related topics, one grouped.
_DDG_BODY = {
    "Heading": "Python",
    "AbstractText": "Python is a programming language.",
    "AbstractURL": "https://duckduckgo.com/Python",
    "RelatedTopics": [
        {"Text": "Python docs - official documentation", "FirstURL": "https://docs.python.org"},
        {"Topics": [
            {"Text": "Python package index", "FirstURL": "https://pypi.org"},
            {"Text": "Python tutorial", "FirstURL": "https://example.com/tut"},
        ]},
    ],
}


def test_build_search_defaults_to_disabled():
    assert isinstance(build_search(Settings(env="dev")), DisabledSearch)


def test_build_search_falls_back_to_disabled_without_base_url():
    # A real provider selected but half-configured (no base URL) degrades to the safe default.
    assert isinstance(build_search(Settings(env="dev", search_provider="duckduckgo")), DisabledSearch)


def test_build_search_builds_real_provider_when_configured():
    s = Settings(env="dev", search_provider="duckduckgo", search_base_url="https://api.duckduckgo.com/")
    assert isinstance(build_search(s), GenericJsonSearch)


async def test_disabled_search_returns_no_results():
    assert await DisabledSearch().search("anything") == []


# --- deterministic transport contract (no real network) ------------------------------------
def _mock_client(monkeypatch, handler):
    """Make GenericJsonSearch use an httpx.MockTransport driven by ``handler``."""
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return handler(request)

    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("jarvis_backend.core.search.httpx.AsyncClient", _factory)
    return captured


async def test_provider_parses_ddg_body_into_capped_results(monkeypatch):

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_DDG_BODY)

    captured = _mock_client(monkeypatch, handler)
    provider = GenericJsonSearch(base_url="https://api.duckduckgo.com/", max_results=3)
    results = await provider.search("python", limit=3)

    assert len(results) == 3  # capped at limit (abstract + 2 topics)
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].title == "Python"
    assert results[0].url == "https://duckduckgo.com/Python"
    assert "programming language" in results[0].snippet
    assert results[1].url == "https://docs.python.org"
    # Grouped RelatedTopics[].Topics are flattened one level in.
    assert results[2].url == "https://pypi.org"
    # The request carries the query + json format, keyless (no Authorization header).
    req = captured["request"]
    assert req.url.params["q"] == "python" and req.url.params["format"] == "json"
    assert "authorization" not in {k.lower() for k in req.headers}


async def test_provider_sends_bearer_header_when_keyed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    captured = _mock_client(monkeypatch, handler)
    provider = GenericJsonSearch(base_url="https://search.example.com/", api_key="secret-key")
    await provider.search("x")
    assert captured["request"].headers["Authorization"] == "Bearer secret-key"


async def test_provider_returns_empty_on_network_error_and_never_raises():
    # Unroutable base URL -> httpx raises internally -> provider returns [].
    provider = GenericJsonSearch(base_url="http://127.0.0.1:1/", timeout=0.5)
    assert await provider.search("hi") == []


@pytest.mark.parametrize("body", [{}, {"RelatedTopics": "oops"}, [1, 2, 3], 42, "nope"])
async def test_provider_returns_empty_on_malformed_body_and_never_raises(monkeypatch, body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    _mock_client(monkeypatch, handler)
    provider = GenericJsonSearch(base_url="https://api.duckduckgo.com/")
    assert await provider.search("hi") == []


async def test_provider_returns_empty_on_http_status_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    _mock_client(monkeypatch, handler)
    provider = GenericJsonSearch(base_url="https://api.duckduckgo.com/", api_key="k")
    assert await provider.search("hi") == []


@pytest.mark.parametrize("base_url", ["http://[::1", "http://[bad", "http://][", "https://host:bad/"])
async def test_provider_never_raises_on_malformed_base_url(monkeypatch, base_url):
    # A malformed base URL makes urlparse raise; the plaintext preflight must swallow it and
    # return [] -- search() NEVER raises, even before the request, and nothing is sent.
    sent = {"called": False}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        sent["called"] = True
        return httpx.Response(200, json=_DDG_BODY)

    _mock_client(monkeypatch, handler)
    provider = GenericJsonSearch(base_url=base_url, api_key="secret")
    assert await provider.search("hi") == []
    assert sent["called"] is False


@pytest.mark.parametrize("api_key", [None, "secret"])
async def test_provider_refuses_remote_plaintext_http(monkeypatch, api_key):
    # A remote http:// request must never leave the process whether or not a key is set --
    # the query would otherwise travel in cleartext.
    sent = {"called": False}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        sent["called"] = True
        return httpx.Response(200, json=_DDG_BODY)

    _mock_client(monkeypatch, handler)
    provider = GenericJsonSearch(base_url="http://api.example.com/", api_key=api_key)
    assert await provider.search("hi") == []
    assert sent["called"] is False


# --- config fail-closed (mirrors the llm plaintext-remote tests) ----------------------------
def test_remote_plaintext_http_is_flagged_in_dev(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    s = Settings(env="dev", search_provider="duckduckgo", search_base_url="http://api.example.com/")
    assert "search_plaintext_remote" in {f.code for f in s.findings()}


@pytest.mark.parametrize("base_url", ["http://[::1", "https://host:bad/"])
def test_malformed_search_base_url_is_flagged_not_crashing_in_config(monkeypatch, tmp_path, base_url):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    s = Settings(env="dev", search_provider="generic", search_base_url=base_url)
    assert "search_plaintext_remote" in {f.code for f in s.findings()}


def test_disabled_search_yields_no_finding(monkeypatch, tmp_path):
    # provider="none" is a valid state and must NOT trip any plaintext finding, even if a
    # base URL happens to be set.
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    s = Settings(env="dev", search_provider="none", search_base_url="http://api.example.com/")
    assert "search_plaintext_remote" not in {f.code for f in s.findings()}


def test_loopback_plaintext_http_is_allowed(monkeypatch, tmp_path):
    # Local SearxNG over loopback http:// must not trip the finding.
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    s = Settings(env="dev", search_provider="generic", search_base_url="http://localhost:8888/search")
    assert "search_plaintext_remote" not in {f.code for f in s.findings()}


def test_remote_plaintext_http_is_a_config_error_in_prod(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="plaintext"):
        Settings(env="prod", jwt_secret="x" * 48, data_key="y" * 48, redis_url="rediss://h/0",
                 database_url="postgresql://u:p@db/x", allowed_hosts="example.com",
                 public_origin="https://example.com", cookie_secure=True,
                 metrics_token="z" * 32, search_provider="duckduckgo",
                 search_base_url="http://api.example.com/")


def test_unconfigured_search_is_not_a_config_error_in_prod(monkeypatch, tmp_path):
    # Disabled search (the default) is valid in prod; a well-formed prod Settings must build.
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import secrets as _secrets
    s = Settings(env="prod", jwt_secret=_secrets.token_urlsafe(48),
                 data_key=_secrets.token_urlsafe(48), redis_url="rediss://h/0",
                 database_url="postgresql://u:p@db/x", allowed_hosts="example.com",
                 public_origin="https://example.com", cookie_secure=True,
                 metrics_token=_secrets.token_urlsafe(32))
    assert "search_plaintext_remote" not in {f.code for f in s.findings()}
