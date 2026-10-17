"""Web-search provider abstraction.

Agents depend only on :class:`SearchProvider`, so DuckDuckGo / a SearxNG instance / any
other JSON engine is a configuration swap. :class:`DisabledSearch` keeps the whole pipeline
runnable offline and is the default in dev and tests -- it returns NO results (never fake
ones), so an agent that gets back an empty list stays honest and says nothing was found.

:class:`GenericJsonSearch` is the real provider. It defaults to the keyless, HTTPS
DuckDuckGo Instant Answer API (``https://api.duckduckgo.com/``), which needs no API key and
serves JSON out of the box. The same class also covers a SearxNG ``/search?format=json``
endpoint (or any similar engine) by only changing ``JARVIS_SEARCH_BASE_URL``; when a key is
configured it is sent as a Bearer header exactly like the LLM provider. DuckDuckGo IA is an
*instant-answer* service rather than a ranked-link index, so many queries legitimately
return few or zero related topics -- which is precisely why SHURI must keep the honesty rule
on an empty result rather than inventing links.

:func:`build_search` selects between them from :class:`~jarvis_backend.config.Settings`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx
from jarvis_shared.log import get_logger
from pydantic import BaseModel

if TYPE_CHECKING:
    from jarvis_backend.config import Settings

log = get_logger(__name__)

# Hosts a plaintext http:// base URL may legitimately use (a local SearxNG instance). Any
# other host over http:// is refused: the query text (and a Bearer key, if set) would travel
# in cleartext. Mirrors the LLM provider's guard. See :func:`_plaintext_remote`.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _plaintext_remote(base_url: str) -> bool:
    """True when the base URL is a plaintext http:// endpoint on a non-loopback host.

    The query text crosses the wire on every request, so confidentiality must not depend on
    whether an API key happens to be set. Loopback http:// (a local SearxNG instance) is
    fine; any hosted endpoint must use https://. Mirrors ``llm._plaintext_remote`` exactly.
    """
    try:
        parsed = urlparse(base_url)
        scheme, hostname = parsed.scheme, (parsed.hostname or "")
        # Touch .port too: urlparse accepts a non-numeric port lazily, but reading it raises
        # ValueError for e.g. "https://host:bad/". httpx would otherwise raise only at
        # request time, outside this guard.
        _ = parsed.port
    except ValueError:
        # A malformed base URL (an unterminated IPv6 literal, or a non-numeric port) makes
        # urlparse raise. Treat it as unsafe: refuse to send rather than letting the error
        # escape the provider's never-raise contract.
        return True
    if scheme != "http":
        return False
    return hostname not in _LOOPBACK_HOSTS


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


@runtime_checkable
class SearchProvider(Protocol):
    async def search(self, query: str, *, limit: int = 5) -> List[SearchResult]: ...


class DisabledSearch:
    """Default offline provider. Returns no results -- never fabricated ones."""

    async def search(self, query: str, *, limit: int = 5) -> List[SearchResult]:
        return []


class GenericJsonSearch:
    """Real provider for a DuckDuckGo-shaped (or SearxNG-shaped) JSON search endpoint.

    Works keyless against the DuckDuckGo Instant Answer API
    (``https://api.duckduckgo.com/``) and against a SearxNG ``/search?format=json`` endpoint.
    A Bearer header is sent only when an API key is configured, so DuckDuckGo (which needs
    none) works out of the box.

    Transport confidentiality: a remote plaintext http:// endpoint is refused before any
    request is made (the query would otherwise cross the wire in cleartext), independently of
    whether an API key is set. Loopback http:// (local SearxNG) stays allowed. Config
    validation flags the same case as a fail-closed finding.

    Error policy: ``search`` NEVER raises. On any transport, HTTP-status, JSON or
    response-shape error it logs only the error class (never the query text or the API key --
    the key is a ``SecretStr`` and is only unwrapped into the Authorization header) and
    returns ``[]``. ``Supervisor.handle`` has no try/except around ``agent.handle``, so a
    raised exception would 500 the ``/assist`` request; returning ``[]`` guarantees a
    provider failure never crashes a request while keeping the honesty rule.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        max_results: int = 5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._max_results = max_results

    async def search(self, query: str, *, limit: int = 5) -> List[SearchResult]:
        cap = min(limit, self._max_results) if limit > 0 else self._max_results
        if _plaintext_remote(self._base_url):
            # Refuse to send the query (and any Bearer key) over plaintext to a remote host
            # (config validation flags this too). Nothing leaves the process.
            log.warning("search request refused: plaintext http to a remote host")
            return []
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "no_redirects": "1",
            "skip_disambig": "1",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.get(self._base_url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
            results = self._parse(data, cap)
        except (httpx.HTTPError, httpx.InvalidURL, KeyError, IndexError, TypeError,
                ValueError) as exc:
            # Log the class only -- never the query text or the key.
            log.warning("search request failed", extra={"error": type(exc).__name__})
            return []
        return results

    @staticmethod
    def _parse(data: Any, cap: int) -> List[SearchResult]:
        """Map a DuckDuckGo-shaped JSON body into capped :class:`SearchResult`s.

        Abstract (instant answer) first, then ``RelatedTopics`` leaves carrying
        ``Text`` + ``FirstURL``, recursing one level into grouped ``RelatedTopics[].Topics``.
        A non-string / missing field is skipped, never fabricated. Raises nothing beyond the
        caller's guarded exception set (a non-dict body yields no results).
        """
        if not isinstance(data, dict):
            return []
        out: List[SearchResult] = []

        abstract = data.get("AbstractText")
        abstract_url = data.get("AbstractURL")
        if isinstance(abstract, str) and abstract.strip() and isinstance(abstract_url, str) \
                and abstract_url.strip():
            heading = data.get("Heading")
            title = heading.strip() if isinstance(heading, str) and heading.strip() else "Summary"
            out.append(SearchResult(title=title, url=abstract_url.strip(),
                                    snippet=abstract.strip()))

        def add_topic(topic: Any) -> None:
            if len(out) >= cap or not isinstance(topic, dict):
                return
            text = topic.get("Text")
            url = topic.get("FirstURL")
            if isinstance(text, str) and text.strip() and isinstance(url, str) and url.strip():
                title = text.strip().split(" - ", 1)[0][:120]
                out.append(SearchResult(title=title, url=url.strip(), snippet=text.strip()))

        related = data.get("RelatedTopics")
        if isinstance(related, list):
            for entry in related:
                if len(out) >= cap:
                    break
                if isinstance(entry, dict) and isinstance(entry.get("Topics"), list):
                    for nested in entry["Topics"]:
                        if len(out) >= cap:
                            break
                        add_topic(nested)
                else:
                    add_topic(entry)
        return out[:cap]


def build_search(settings: Settings) -> SearchProvider:
    """Return the configured provider.

    ``DisabledSearch`` for the default ``provider="none"`` and for a real provider that has
    no usable base URL (so a half-configured env degrades to the safe offline default rather
    than failing). Otherwise a :class:`GenericJsonSearch`.
    """
    if settings.search_provider == "none" or not settings.search_base_url_value:
        return DisabledSearch()
    return GenericJsonSearch(
        base_url=settings.search_base_url_value,
        api_key=settings.secret("search_api_key") or None,
        timeout=settings.search_timeout_seconds,
        max_results=settings.search_max_results,
    )
