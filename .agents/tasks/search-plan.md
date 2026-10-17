# Phase 3a — Wire SHURI web search (implementation plan)

> ## Verification record (first iteration — IMPLEMENTED)
> Commands run from repo root `c:\Users\Lenovo\Desktop\JARVIS\abduls_jarvis` with `JARVIS_ENV=dev`,
> `.\.venv\Scripts\python.exe -m pytest ... -q`:
> - `tests/unit/test_search.py` → **26 passed**
> - `tests/unit tests/integration` (full suite) → **267 passed, 5 skipped** (postgres `pgserver`
>   extras unavailable), run twice (~22–25s each).
> Baseline before this change was 239 passed; the +28 are the new `test_search.py` (26) and the two
> new SHURI agent tests in `test_agents_llm.py`.
> Known-flaky `test_config_and_crypto.py::test_cipher_key_rotation_keeps_old_data_readable` did NOT
> fail in either full run (not fixed, as instructed). Dev server constructs with search disabled;
> prod fail-closed does not trip on unconfigured (`provider="none"`) search.
> Chosen engine: **DuckDuckGo Instant Answer API** (keyless, HTTPS, JSON) via `GenericJsonSearch`,
> which also covers a SearxNG `/search?format=json` endpoint by changing the base URL.
> New env vars: `JARVIS_SEARCH_PROVIDER`, `JARVIS_SEARCH_BASE_URL`, `JARVIS_SEARCH_API_KEY`,
> `JARVIS_SEARCH_TIMEOUT_SECONDS`, `JARVIS_SEARCH_MAX_RESULTS`.


Goal: SHURI returns real, synthesised research findings instead of the "web search isn't
connected yet" stub. Mirror the Phase 1 `LLMProvider` pattern exactly: a new provider module,
`JARVIS_SEARCH_*` config with a prod fail-closed plaintext-remote finding, threaded through
`AgentServices`, a rewritten `ShuriAgent.handle`, docs, and tests. Edit IN PLACE in
`c:\Users\Lenovo\Desktop\JARVIS\abduls_jarvis` — no worktree.

## Key facts discovered during exploration (ground truth)

- **Backend package root**: `jarvis-backend/src/jarvis_backend/`. Phase 1 template is
  `core/llm.py` (Protocol + Echo default + real httpx provider + `_plaintext_remote` guard +
  `build_llm` factory).
- **Tests live at the REPO ROOT `tests/`**, NOT `jarvis-backend/tests/`. `test_llm.py` is at
  `tests/unit/test_llm.py`; agent test at `tests/unit/test_agents_llm.py`; HTTP e2e at
  `tests/integration/test_workflows.py`. The task text's `jarvis-backend/tests/...` path is
  wrong for this repo — create the new test at **`tests/unit/test_search.py`** to match the
  real layout and `pyproject.toml` `testpaths = ["tests"]`.
- **Test command** (from repo root, env `JARVIS_ENV=dev`):
  `.\.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q`
  Baseline is green: 239 passed, 5 skipped (postgres extras missing), ~23s. `asyncio_mode=auto`
  so `async def test_` needs no decorator.
- **Known-flaky pre-existing test**: `tests/unit/test_config_and_crypto.py::test_cipher_key_rotation_keeps_old_data_readable`.
  Unrelated; do NOT fix. If it flaps, re-run in isolation to confirm.
- **Ruff** config in root `pyproject.toml`: line-length 100, py311 target, `UP006/UP007/UP035/UP045`
  ignored → keep `typing.Optional/List` + `from __future__ import annotations`. The `.venv` may
  lack ruff/pip; rely on pytest and match surrounding style exactly (it will be ruff-clean).
- **AgentServices** (`agents/runtime.py`) currently: `memory: MemoryStore`, `llm: LLMProvider`.
  Built once in `Supervisor.__init__` as `self._services = AgentServices(memory=memory, llm=llm)`.
  `container.py` passes `llm=build_llm(settings)` into the `Supervisor(...)` constructor.
- **SHURI spec** (`jarvis-shared/.../agents.py`): `AgentId.SHURI`, `display_name="SHURI"`,
  `role="Research and web investigation."`, `personality=["intelligent","curious","energetic","analytical"]`,
  `tools=["browser"]`. SHURI returns SPEECH ONLY — no `ToolRequest` (read-only research).
- **context dict** passed to `handle`: keys `allowed_paths`, `allowed_apps`, `history`
  (list of `{user,agent,reply}`), `memories` (list of str).
- **`EchoLLM.complete`** returns `f"Acknowledged: {last_user_content[:160]}"` — so with the
  default echo LLM, SHURI's synthesis will be the echo of the synthesis prompt. That's fine and
  deterministic for tests (we inject a FakeLLM to assert the real synthesis path).

## Chosen search provider — and why

**DuckDuckGo Instant Answer API** (`https://api.duckduckgo.com/`) as the default real engine,
implemented as a *generic JSON provider* so a SearxNG `/search?format=json` endpoint also works
by only changing `JARVIS_SEARCH_BASE_URL`.

Why DuckDuckGo IA:
- **Keyless and HTTPS out of the box** — usable with zero configuration beyond flipping the
  provider on, matching the task's "prefer a keyless engine usable out of the box" requirement.
  (Keyless + JSON-by-default confirmed via public API docs; content rephrased for licensing
  compliance. Refs: https://api.duckduckgo.com/api , https://freeapihub.com/apis/duckduckgo-instant-answer-api )
- **Returns real titles/urls/snippets** we can map to `SearchResult`: top-level
  `Heading`/`AbstractText`/`AbstractURL` plus a `RelatedTopics` list whose leaf entries carry
  `Text` and `FirstURL`. No fabricated data.
- **Honest about emptiness**: it is an *instant-answer* service, not a ranked-link web index, so
  many queries return few/zero related topics. That is exactly why SHURI must keep the honesty
  rule — on empty results SHURI says nothing was found, never invents links.

The real provider class is therefore `GenericJsonSearch` (not DuckDuckGo-specific in name):
construct the request URL as `{base_url}?q={query}&format=json&no_html=1&no_redirects=1&skip_disambig=1`
for the DDG shape, and parse a configurable-but-defaulted response map. Default parsing targets
the DDG JSON shape (`AbstractText`+`AbstractURL`+`Heading`, then `RelatedTopics[].Text/.FirstURL`,
recursing one level into `RelatedTopics[].Topics` which DDG uses for grouped results). Cap to
`limit`. If/when a key is supplied it is sent as a Bearer header (same pattern as the LLM), so the
same class also covers a keyed SearxNG/other endpoint; unconfigured → `DisabledSearch` via the
factory.

## JARVIS_SEARCH_* settings (mirror JARVIS_LLM_*)

Added to `config.py` in a new `# --- search ---` block right after the llm block:

| Setting | Field | Default | Notes |
|---|---|---|---|
| `JARVIS_SEARCH_PROVIDER` | `search_provider: Literal["none","duckduckgo","generic"]` | `"none"` | `"none"` = disabled (dev/test default). `"duckduckgo"`/`"generic"` → real provider. |
| `JARVIS_SEARCH_BASE_URL` | `search_base_url: str` | `""` | DDG: `https://api.duckduckgo.com/`. Empty with a non-none provider falls back to `DisabledSearch`. |
| `JARVIS_SEARCH_API_KEY` | `search_api_key: Optional[SecretStr]` | `None` | Optional; most engines (DDG) need none. `SecretStr`. |
| `JARVIS_SEARCH_TIMEOUT_SECONDS` | `search_timeout_seconds: float` | `10.0` | Short timeout. |
| `JARVIS_SEARCH_MAX_RESULTS` | `search_max_results: int` | `5` | Cap on results returned/synthesised. |

Add property `search_base_url_value` → `self.search_base_url.strip().rstrip("/")` (mirrors
`llm_base_url_value`).

**Fail-closed finding** (`findings()`), mirroring the `llm_plaintext_remote` block exactly:
guard only when `self.search_provider != "none" and self.search_base_url_value`. Reuse the same
`try/urlparse/_ = parsed.port/except ValueError: plaintext_remote=True` structure and the
`_LLM_LOOPBACK_HOSTS` set (rename it to `_LOOPBACK_HOSTS` or add a shared `_SEARCH_LOOPBACK_HOSTS`
alias = `_LLM_LOOPBACK_HOSTS` to avoid duplication — prefer reusing the existing set). Emit code
`search_plaintext_remote` via `err(...)` (error in prod, warning in dev). **Do NOT add any
finding that fires merely because search is unconfigured** — `provider="none"` is valid and must
produce no finding.

## Ordered implementation items

- [ ] 1. Create the search provider module `core/search.py`, mirroring `core/llm.py`.
      Define: `SearchResult(BaseModel)` with `title: str`, `url: str`, `snippet: str`;
      `@runtime_checkable class SearchProvider(Protocol)` with
      `async def search(self, query: str, *, limit: int = 5) -> List[SearchResult]: ...`;
      `DisabledSearch` whose `search` returns `[]` (NOT fake results); `GenericJsonSearch` — the
      real httpx-async provider. Reuse the plaintext-remote guard: either import a shared helper
      or copy `_plaintext_remote` + `_LOOPBACK_HOSTS` from `llm.py` with the same semantics
      (malformed URL → treated unsafe/return []). `GenericJsonSearch.search` MUST: refuse remote
      plaintext `http://` before any request (log `"search request refused: plaintext http to a
      remote host"`, return `[]`); build the DDG-style query params; `httpx.AsyncClient(timeout=
      self._timeout)` GET; `raise_for_status()`; `response.json()`; map to `SearchResult`s
      (abstract first, then `RelatedTopics` leaves with `Text`+`FirstURL`, recurse one level into
      nested `Topics`); cap to `limit`; and NEVER raise — wrap the request+parse in
      `try/except (httpx.HTTPError, httpx.InvalidURL, KeyError, IndexError, TypeError, ValueError)`
      logging ONLY `type(exc).__name__` (never the query text or key) and returning `[]`.
      Add `build_search(settings) -> SearchProvider`: return `DisabledSearch()` when
      `settings.search_provider == "none"` or not `settings.search_base_url_value`; else
      `GenericJsonSearch(base_url=..., api_key=settings.secret("search_api_key") or None,
      timeout=settings.search_timeout_seconds, max_results=settings.search_max_results)`.
      Files: `jarvis-backend/src/jarvis_backend/core/search.py`
      Verify: `.\.venv\Scripts\python.exe -c "import jarvis_backend.core.search"` imports clean;
      full build confirmed by the item 7 test run.

- [ ] 2. Add `JARVIS_SEARCH_*` settings + `search_base_url_value` property + the
      `search_plaintext_remote` fail-closed finding to `config.py`, in a new `# --- search ---`
      block immediately after the `# --- llm ---` block, mirroring the llm settings and the
      `llm_plaintext_remote` finding verbatim in structure (reuse `_LLM_LOOPBACK_HOSTS`). No
      finding when `search_provider == "none"`.
      Files: `jarvis-backend/src/jarvis_backend/config.py`
      Verify: run the config tests —
      `.\.venv\Scripts\python.exe -m pytest tests/unit/test_config_and_crypto.py tests/unit/test_llm.py -q`
      (env `JARVIS_ENV=dev`) — all pass (confirms no regression to existing findings/prod rules).

- [ ] 3. Thread the provider through the runtime. In `agents/runtime.py` add
      `search: SearchProvider` to `AgentServices` (import `SearchProvider`/`SearchResult` from
      `jarvis_backend.core.search`). In `core/supervisor.py` add a `search: SearchProvider`
      constructor param and pass it into `AgentServices(memory=memory, llm=llm, search=search)`.
      In `container.py` import `build_search` and pass `search=build_search(settings)` into the
      `Supervisor(...)` construction (same place `llm=build_llm(settings)` is passed).
      Files: `jarvis-backend/src/jarvis_backend/agents/runtime.py`,
      `jarvis-backend/src/jarvis_backend/core/supervisor.py`,
      `jarvis-backend/src/jarvis_backend/container.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_workflows.py -q`
      (env `JARVIS_ENV=dev`) — app constructs and existing workflows pass (the honesty test in
      item 6 is updated in the same change set; see item 6).

- [ ] 4. Rewrite `ShuriAgent.handle` in `agents/runtime.py`. Keep `_topic_after(task,
      ["research","investigate","find out","look into"])`. Call
      `results = await self.services.search.search(topic, limit=self.services... )` — use
      `limit` from a small constant or thread the configured max via the provider's own cap
      (the provider already caps to `max_results`; pass `limit=5` or a module constant). If
      `results` is empty: return an HONEST `AgentOutcome` saying the web search returned nothing
      / isn't configured (do NOT fabricate) — wording must still contain a clear honest signal.
      If results exist: build an LLM message list — a system prompt giving SHURI's persona
      (`spec.personality` joined + `spec.role`) + the HONESTY RULE ("synthesise only what these
      sources state; cite nothing not present; do not invent URLs or facts"); a user message with
      the topic and the numbered results (title / url / snippet each truncated), ask for a brief
      spoken synthesis. Call `reply = await self.services.llm.complete(messages)` and return
      `AgentOutcome(reply.strip())` — SPEECH ONLY, NO `ToolRequest`. Replace the old `# SEAM`
      comment with a `# SEAM (now wired)`-style note: search is config-driven, `DisabledSearch`
      by default. Do not touch `NatashaAgent`.
      Files: `jarvis-backend/src/jarvis_backend/agents/runtime.py`
      Verify: covered by item 6 agent test + item 7; also
      `.\.venv\Scripts\python.exe -m pytest tests/unit/test_agents_llm.py -q`.

- [ ] 5. Update docs. `.env.example`: add a `# ---- search (SHURI research) ----` block after the
      llm block with the five `JARVIS_SEARCH_*` vars, defaults, and a one-line note that `none`
      disables (dev/test default) and a remote `http://` base URL is a prod fail-closed error.
      `docs/PROVIDERS.md`: update the research/SHURI note — add a "Web search — `SearchProvider`"
      subsection mirroring the LLM one (DisabledSearch default, GenericJsonSearch over httpx,
      keyless DuckDuckGo default, never-raises + no-query/key-logging error policy, the same
      transport-safety plaintext-remote rule, and the env var table). `SKILL.md`: section 2 SHURI
      row `⛔ (SEAM: web search + LLM)` → `🟡 (search wired; disabled by default, enable via
      JARVIS_SEARCH_*)` (keep honest — `DisabledSearch` is the default); section 3 `browser` row
      state `⛔ (SEAM)` → note search is now wired via `SearchProvider` (read-only, speech only,
      no ToolRequest); section 4 engine table — add a `Search | SearchProvider (core/search.py) |
      DisabledSearch | DuckDuckGo / generic JSON | JARVIS_SEARCH_PROVIDER, JARVIS_SEARCH_BASE_URL`
      row. `docs/PRODUCTION_PLAN.md`: Phase 3 "SHURI: web search tool → LLM synthesis" line and
      the section-5 state-table "Web research (SHURI)" row `⛔` → `🟡` with a note that search is
      config-driven and disabled by default (LLM synthesis via the Phase 1 provider).
      Files: `.env.example`, `docs/PROVIDERS.md`, `SKILL.md`, `docs/PRODUCTION_PLAN.md`
      Verify: docs-only; no command. Confirm the `JARVIS_SEARCH_*` names in `.env.example` and
      `PROVIDERS.md` exactly match the `config.py` env names (item 2).

- [ ] 6. Update the honesty workflow test and add a SHURI agent test. In
      `tests/integration/test_workflows.py::test_agents_never_claim_work_they_did_not_do`: the
      first assertion `"isn't connected" in say(... "ask SHURI to research agent security")` will
      break because SHURI's default-disabled wording changes — update it to assert the NEW honest
      empty wording (e.g. assert a phrase like `"nothing"` / `"not configured"` / `"no findings"`
      that item 4's empty-branch actually produces) WITHOUT weakening the honesty intent; leave
      the Gmail (`"isn't connected"`) and VISION assertions unchanged. Add SHURI coverage either
      here or in `tests/unit/test_agents_llm.py`: inject a fake `SearchProvider` returning 2
      `SearchResult`s + a `FakeLLM` returning a sentinel, build SHURI via `build_agent(_spec(
      AgentId.SHURI), AgentServices(memory=None, llm=fake, search=fake_search))`, assert
      `outcome.speech == SENTINEL` and `outcome.tool_requests == []` and that the fake LLM's
      system+user messages contained the topic and the result titles/urls; and a second case with
      `DisabledSearch()` asserting SHURI stays honest (empty-branch wording, no fabricated URL).
      Files: `tests/integration/test_workflows.py`, `tests/unit/test_agents_llm.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_agents_llm.py tests/integration/test_workflows.py -q`
      (env `JARVIS_ENV=dev`) — all pass.

- [ ] 7. Add `tests/unit/test_search.py` mirroring `tests/unit/test_llm.py` (same
      `_mock_client(monkeypatch, handler)` httpx.MockTransport helper, patching
      `jarvis_backend.core.search.httpx.AsyncClient`). Cover:
      (a) `build_search(Settings(env="dev"))` → `DisabledSearch` (default `provider="none"`);
      (b) `build_search(Settings(env="dev", search_provider="duckduckgo"))` with empty base URL →
      `DisabledSearch` (half-configured degrades);
      (c) `build_search` with provider + base URL → `GenericJsonSearch`;
      (d) `DisabledSearch().search("x")` → `[]`;
      (e) real provider parses a mocked DDG-shaped 200 body into capped `SearchResult`s
      (assert titles/urls/snippets and `len <= limit`);
      (f) real provider returns `[]` and never raises on: a network error (unroutable
      `http://127.0.0.1:1/`), a malformed JSON/`{}` body, and a malformed base URL incl.
      invalid port (`https://host:bad/`) — parametrise like the llm malformed-url tests;
      (g) real provider refuses remote plaintext `http://` (DDG-style `http://api.example.com/`)
      so the mock handler is NEVER called (`sent["called"] is False`) and `search` returns `[]`
      — the query must not leave the process;
      (h) config fail-closed: `Settings(env="dev", search_provider="duckduckgo",
      search_base_url="http://api.example.com/").findings()` contains code
      `search_plaintext_remote`, and a prod `Settings(..., search_base_url="http://api.example.com/")`
      raises `ValueError` matching `plaintext` (build the full valid prod Settings like the llm
      prod tests do), while `search_provider="none"` yields NO such finding, and loopback
      `http://localhost.../` does not trip it.
      Files: `tests/unit/test_search.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_search.py -q` — all new tests
      pass.

- [ ] 8. Full-suite regression gate. Run the whole suite and confirm no regressions beyond the
      one known-flaky crypto test.
      Files: (none — verification only)
      Verify: `$env:JARVIS_ENV="dev"; .\.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q`
      — expect all pass except possibly the known-flaky
      `test_config_and_crypto.py::test_cipher_key_rotation_keeps_old_data_readable`; if it fails,
      re-run it in isolation to confirm it is the pre-existing flake, do NOT fix it. Baseline was
      239 passed / 5 skipped; after this change expect the new search + SHURI tests added on top,
      same 5 postgres skips.

## Constraints / do-not-regress (carry into every item)

- Do NOT change the 7-stage supervisor contract, the policy gate, or the local validator beyond
  adding the `search` field/param. NATASHA stays stubbed (Phase 3b) — do not touch its Gmail seam.
- The search provider must NEVER raise out of `/assist`; never log the query text or API key
  (log `type(exc).__name__` only); never fabricate results or URLs.
- Keep Python 3.10 compatibility and the per-file style (`from __future__ import annotations`,
  `typing.Optional/List`, 100-col). Match `llm.py` structure so edits stay ruff-clean.
- SHURI returns SPEECH ONLY — no `ToolRequest` (read-only research, no local-agent tool gate).
- Honesty rule is paramount: empty results ⇒ honest "nothing found / not configured".

## Note on task-text path discrepancy

The task text references `jarvis-backend/tests/...` for the new tests, but this repo keeps all
tests at the repo-root `tests/` tree (confirmed: `test_llm.py` lives at `tests/unit/test_llm.py`,
and `pyproject.toml` sets `testpaths=["tests"]`). This plan uses `tests/unit/test_search.py` so
the tests are actually collected and run. Source modules still go under
`jarvis-backend/src/jarvis_backend/` exactly as the task states.
