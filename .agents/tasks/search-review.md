# SHURI web search wired to LLM synthesis (Phase 3a)

SHURI stops saying "web search isn't connected yet" and instead runs a real query, then
synthesises the hits through the Phase 1 LLM seam. The change clones the `LLMProvider`
pattern end to end: a new `core/search.py` with a `SearchProvider` protocol, a `DisabledSearch`
offline default, one real `httpx`-async provider (`GenericJsonSearch`, keyless DuckDuckGo IA by
default), `JARVIS_SEARCH_*` config mirroring `JARVIS_LLM_*` plus a fail-closed plaintext-remote
finding, the provider threaded through `AgentServices`/`Supervisor`/`Container`, a rewritten
`ShuriAgent.handle`, docs, and tests. SHURI returns speech only — it never emits a `ToolRequest`,
so research can't touch the machine or bypass the policy gate.

Watch for: nothing blocking. One non-blocking doc-accuracy nit — the SearxNG claim in the docs
is overstated because `_parse` only understands the DuckDuckGo JSON shape (confirmed). Honesty,
never-raise, plaintext-refusal, result capping, and disabled-by-default all verified against the
code.

**Verdict**: APPROVED

## High-level view

The design mirrors `core/llm.py` closely enough that the review is largely a diff against that
template, which is the right call: `DisabledSearch` is the dev/test default returning `[]` (never
fabricated), `GenericJsonSearch` is the single real provider, and `build_search(settings)` picks
between them. A real-provider-with-no-base-URL degrades to `DisabledSearch` rather than failing,
same as `build_llm`.

The security posture is the important part and it holds: the provider refuses a remote plaintext
`http://` base URL before any socket opens (so the query never leaves the process), treats a
malformed or bad-port URL as unsafe (fail closed), and `search()` never raises — any transport,
HTTP, JSON or shape error is caught and logged by error class only, never the query or key. Config
validation adds a `search_plaintext_remote` finding that errors in prod and warns in dev, and
`provider="none"` (the default) produces no finding, so an unconfigured prod install still boots.

The honesty rule is enforced structurally: on empty results SHURI returns a fixed honest sentence
and never calls the LLM, so there is no path where a fabricated URL or finding can be synthesised
from nothing. On non-empty results it hands the persona, topic, and sources to the LLM under an
explicit "synthesise only what the sources state, invent no URLs or facts" instruction and speaks
the reply. No `ToolRequest` is ever produced.

The threading is minimal and in scope: `AgentServices` gains a `search` field, `Supervisor` gains a
`search` param, `Container` builds it via `build_search(settings)` next to `build_llm`. Nothing in
the 7-stage contract, policy gate, local validator, or NATASHA's Gmail seam is touched.

## Verification evidence

Per the task, the suite was not re-run. The commit message and the plan's verification record both
record: `JARVIS_ENV=dev`, repo-root `.venv` pytest; `tests/unit/test_search.py` → 26 passed; full
`tests/unit tests/integration` → 267 passed, 5 skipped (postgres `pgserver` extras), run twice;
baseline 239 → +28 new (26 in `test_search.py`, 2 SHURI tests in `test_agents_llm.py`). The
arithmetic is internally consistent — I counted 26 cases in `test_search.py` including the
parametrised ones, and 28 total with the two new agent tests — which corroborates the record. The
known-flaky `test_cipher_key_rotation_keeps_old_data_readable` did not fail and was correctly left
untouched. Evidence is concrete; no specific doubt warranted a spot-check.

<details>
<summary>Issues (1)</summary>

1. **SearxNG parse claim overstated (non-blocking)** — `_parse` only maps the DuckDuckGo
   `AbstractText`/`RelatedTopics` shape; a SearxNG `/search?format=json` body (`results[]` with
   `title`/`url`/`content`) would parse to `[]`, not real hits. Docs (`.env.example`, `PROVIDERS.md`,
   `SKILL.md`, commit message) imply SearxNG works by only changing the base URL. Behaviour is still
   safe (honest empty, never raises); tighten the docs or add a SearxNG branch to `_parse` in a
   later pass.

</details>

<details>
<summary>Details</summary>

### Provider shape mirrors the LLM seam (confirmed)

`core/search.py` lands `SearchResult(BaseModel)` with `title`/`url`/`snippet`, a
`@runtime_checkable SearchProvider` protocol with `async def search(self, query, *, limit=5) ->
List[SearchResult]`, `DisabledSearch` returning `[]`, and `GenericJsonSearch` as the single real
`httpx`-async provider. `build_search` returns `DisabledSearch()` when `search_provider == "none"`
or the base URL is empty, else a `GenericJsonSearch`. This is the `build_llm` branch structure
field-for-field, including the half-configured-degrades-to-safe-default behaviour, so a provider
selected without a base URL silently becomes `DisabledSearch` rather than erroring.

### Transport safety and the never-raise contract (confirmed)

`_plaintext_remote` is copied from `llm.py` with identical semantics: it reads `parsed.port` to
force the lazy `ValueError` on a non-numeric port, and on any `urlparse` failure returns `True`
(treat as unsafe). `GenericJsonSearch.search` runs that preflight before building a request, so a
remote `http://` base URL — or a malformed/bad-port one — returns `[]` and logs
`"search request refused: plaintext http to a remote host"` with no query text. The request and
parse are wrapped in the same `(httpx.HTTPError, httpx.InvalidURL, KeyError, IndexError, TypeError,
ValueError)` catch as the LLM provider, logging only `type(exc).__name__`. The key is a `SecretStr`
unwrapped only into the `Authorization` header and never logged. `Supervisor.handle` has no
try/except around `agent.handle`, so this never-raise guarantee is what keeps a search outage from
500-ing `/assist`.

```python
cap = min(limit, self._max_results) if limit > 0 else self._max_results
if _plaintext_remote(self._base_url):
    log.warning("search request refused: plaintext http to a remote host")
    return []
```

Results are capped twice — `add_topic`/the loop stop at `cap`, and `_parse` returns `out[:cap]` —
so the `limit`/`max_results` ceiling holds regardless of how many topics the body carries. The DDG
parse takes the abstract first, then ungrouped `RelatedTopics` leaves, recursing one level into
grouped `Topics`, skipping any entry missing a string `Text`/`FirstURL` rather than inventing one.

### Honesty is structural, not advisory (confirmed)

`ShuriAgent.handle` keeps `_topic_after`, calls `services.search.search(topic, limit=5)`, and on an
empty list returns a fixed honest sentence ("web search returned nothing (it may not be
configured)… no findings to report") without ever calling the LLM. There is no code path from empty
results to synthesis, so SHURI cannot fabricate a finding or URL from nothing. On non-empty results
it builds a system prompt carrying SHURI's persona and role plus the explicit "Synthesise ONLY what
the provided sources state… never invent URLs or facts" rule, a user prompt listing the numbered
sources (title/url/snippet, each truncated), calls `services.llm.complete`, and returns
`AgentOutcome(reply.strip())` — speech only, no `ToolRequest`. The `# SEAM` comment is updated to
`SEAM (now wired)`.

### Config fail-closed, disabled stays valid (confirmed)

`config.py` adds the `JARVIS_SEARCH_*` block mirroring `JARVIS_LLM_*` (provider `Literal` defaulting
to `"none"`, base_url, optional `SecretStr` api_key, timeout, max_results), the
`search_base_url_value` property, and a `search_plaintext_remote` finding guarded by
`search_provider != "none" and search_base_url_value`. It reuses the shared `_LLM_LOOPBACK_HOSTS`
set and the same `try/urlparse/_ = parsed.port/except ValueError` fail-closed structure as the LLM
finding, emitted via `err(...)` so it errors in prod and warns in dev. Because the guard requires a
non-`none` provider *and* a base URL, a default or merely-unconfigured install produces no finding —
`test_unconfigured_search_is_not_a_config_error_in_prod` builds a full valid prod `Settings` and
confirms it. Loopback `http://` is allowed; a prod remote `http://` raises at construction.

### Threading stayed in scope (confirmed)

The only runtime changes beyond `search.py` and `ShuriAgent` are: `AgentServices` gains
`search: SearchProvider`; `Supervisor.__init__` gains a `search` param passed into
`AgentServices(...)`; `Container` imports `build_search` and passes `search=build_search(settings)`
alongside `llm=build_llm(settings)`. The 7-stage supervisor contract, policy gate, local validator,
and NATASHA's Gmail seam are untouched (confirmed by reading the full runtime diff).

### Tests mirror test_llm.py and lock the honesty rule (confirmed)

`tests/unit/test_search.py` reuses the `test_llm.py` `MockTransport` pattern and covers: factory
defaults to `DisabledSearch`; half-configured degrades; real provider built when configured;
`DisabledSearch` returns `[]`; a DDG body parsed into capped results with grouped `Topics`
flattened; Bearer header only when keyed; `[]` on network error, on five malformed bodies, on HTTP
500, and on four malformed/bad-port base URLs (asserting the mock handler was never called); remote
plaintext `http://` refused with and without a key (handler never called); and the config
fail-closed cases (dev finding, malformed-not-crashing, disabled yields no finding, loopback
allowed, prod raises, unconfigured prod builds). The two new agent tests inject a fake
`SearchProvider` (2 results) plus `FakeLLM` and assert SHURI speaks the sentinel, emits no tool
requests, passed the topic to the provider, and that the persona, topic, and each result's
title/url reached the LLM; the second asserts `DisabledSearch` keeps SHURI honest ("nothing"/"not
configured", no "http", no sentinel, no tools). `test_agents_never_claim_work_they_did_not_do` was
updated from the old `"isn't connected"` SHURI assertion to the new honest-empty wording without
weakening the honesty intent, leaving the Gmail and VISION assertions intact.

### SearxNG parse claim is optimistic (confirmed, non-blocking)

The docs and commit message state the generic provider "also covers a SearxNG `/search?format=json`
endpoint by changing the base URL". `_parse` only reads the DuckDuckGo shape
(`AbstractText`/`AbstractURL`/`Heading`, then `RelatedTopics[].Text`/`.FirstURL`). A real SearxNG
JSON body is `{"results": [{"title", "url", "content"}, …]}`, which has none of those keys, so the
provider would return `[]` for SearxNG — honest and non-crashing, but not the "real hits" the docs
imply. This is a documentation-accuracy issue, not a behaviour or safety defect; the stated
requirement only asks for one real provider (DuckDuckGo) and that is delivered. Non-blocking.

</details>

<details>
<summary>File map</summary>

- `jarvis-backend/src/jarvis_backend/core/search.py` — NEW: `SearchResult`, `SearchProvider`,
  `DisabledSearch`, `GenericJsonSearch`, `_plaintext_remote`, `build_search`.
- `jarvis-backend/src/jarvis_backend/config.py` — `JARVIS_SEARCH_*` settings,
  `search_base_url_value`, `search_plaintext_remote` fail-closed finding.
- `jarvis-backend/src/jarvis_backend/agents/runtime.py` — `AgentServices.search`; `ShuriAgent`
  rewrite (search → LLM synthesis, honest empty branch, speech only).
- `jarvis-backend/src/jarvis_backend/core/supervisor.py` — `search` constructor param threaded into
  `AgentServices`.
- `jarvis-backend/src/jarvis_backend/container.py` — `build_search(settings)` wiring.
- `tests/unit/test_search.py` — NEW: provider/factory/transport/config coverage.
- `tests/unit/test_agents_llm.py` — `FakeSearch`; two SHURI tests; existing tests pass `search=`.
- `tests/integration/test_workflows.py` — honesty test updated to new SHURI empty wording.
- `.env.example`, `docs/PROVIDERS.md`, `SKILL.md`, `docs/PRODUCTION_PLAN.md`,
  `.agents/tasks/search-plan.md` — docs.

Full diff: `git diff 19ab845^ 19ab845`

</details>
