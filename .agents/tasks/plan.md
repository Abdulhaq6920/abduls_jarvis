# Implementation Plan — Phase 1: wire a real, configurable LLMProvider

Goal: replace the "no language model is connected yet" stub so VISION / DR_STRANGE /
QUICKSILVER / NICK_FURY produce real LLM answers, while keeping `EchoLLM` as the default
in `JARVIS_ENV=dev` and tests, and keeping SHURI (web search, Phase 3) and NATASHA (Gmail,
Phase 3) honestly stubbed. All edits are in place in `abduls_jarvis` (NO worktree).

## Design decisions (made here, grounded in the code read)

- **Error policy: safe fallback, never raise into `/assist`.** `Supervisor.handle` has no
  try/except around `agent.handle(...)`, so a raised exception would 500 the `/assist`
  request. Therefore `OpenAICompatibleLLM.complete` catches transport/HTTP/parse errors and
  returns a short honest sentence (e.g. "I couldn't reach the language model just now.")
  instead of raising. This keeps the honesty rule (no fabricated answer) and guarantees a
  provider failure never crashes a request. The agent layer does NOT need its own try/except
  because the provider already never raises.
- **Default stays `EchoLLM`.** `build_llm(settings)` returns `EchoLLM()` when
  `settings.llm_provider == "echo"` (the default) and when the provider is `openai`/`ollama`
  but no base URL / API key is resolvable — so dev and the test suite keep deterministic
  offline behaviour with zero config.
- **One real provider class, two presets.** A single `OpenAICompatibleLLM` speaks the
  OpenAI `/v1/chat/completions` JSON shape, which both OpenAI and local Ollama
  (`http://localhost:11434/v1`) serve. No separate Anthropic class in Phase 1.
- **One LLM-backed agent class** replaces `UnconnectedAgent` for the four `_VERB` agents.
  It builds a system prompt from `spec.personality` + `spec.role`, appends bounded context
  (allowed_paths, allowed_apps, recent history, memories) and the user task, then speaks
  `services.llm.complete(...)`. The honesty rule holds: LLM reasoning is real work.
- **httpx becomes a backend runtime dependency.** It is currently only in
  `jarvis-backend[dev]`; the new provider imports it at runtime, so it must move into the
  backend's `[project].dependencies`.

## Exact new env vars (JARVIS_ prefix, follows existing `Settings` patterns)

- `JARVIS_LLM_PROVIDER` — selector, `Literal["echo", "openai", "ollama"]`, default `"echo"`.
- `JARVIS_LLM_MODEL` — model name, `str`, default `"gpt-4o-mini"`.
- `JARVIS_LLM_BASE_URL` — OpenAI-compatible base URL, `str`, default `""`
  (OpenAI: `https://api.openai.com/v1`; Ollama: `http://localhost:11434/v1`).
- `JARVIS_LLM_API_KEY` — `Optional[SecretStr]`, default `None` (not required for Ollama).
- `JARVIS_LLM_TIMEOUT_SECONDS` — request timeout, `float`, default `30.0`.
- `JARVIS_LLM_MAX_TOKENS` — response cap, `int`, default `512`.

## Tests that assert the old stub string (must be updated, not weakened)

- `tests/integration/test_workflows.py::test_agents_never_claim_work_they_did_not_do`
  line asserting `"no language model" in ...VISION...` — update so VISION answers via the
  default `EchoLLM` (assert the reply is non-empty and does NOT contain "no language model"),
  while KEEPING the two SHURI / Gmail `"isn't connected"` assertions unchanged (those stay
  stubbed). Do not touch any auth/storage/security assertion in this file.

---

- [ ] 1. Add LLM settings to `Settings`.
      Add the six `JARVIS_LLM_*` fields listed above in a new "--- llm ---" block in
      `Settings`, matching the existing field/typing style (`SecretStr` for the key, 3.10
      typing). Add a `llm_base_url_value` convenience property mirroring `redis_url_value`
      if helpful. Default `llm_provider="echo"` so dev/tests are unchanged. No new entries in
      `findings()` are required for Phase 1 (echo default is always safe).
      Files: `jarvis-backend/src/jarvis_backend/config.py`
      Verify: `JARVIS_ENV=dev pytest tests/unit/test_config_and_crypto.py -q` passes; and
      `python -c "from jarvis_backend.config import Settings; s=Settings(env='dev'); print(s.llm_provider, s.llm_model)"`
      prints `echo gpt-4o-mini`.

- [ ] 2. Add `OpenAICompatibleLLM` and `build_llm` factory in `llm.py`.
      Add `class OpenAICompatibleLLM` with `__init__(self, *, model, base_url, api_key,
      timeout, max_tokens)` and `async def complete(self, messages)` that POSTs to
      `{base_url}/chat/completions` via `httpx.AsyncClient` (Bearer auth only when an api_key
      is present), sends `{"model", "messages":[{role,content}...], "max_tokens"}`, parses
      `choices[0].message.content`, and on ANY `httpx`/HTTP-status/JSON/key error returns a
      short honest fallback string (never raises). Add
      `def build_llm(settings) -> LLMProvider` returning `EchoLLM()` for provider `"echo"`,
      or when a real provider is selected but has no usable base URL (empty) — otherwise an
      `OpenAICompatibleLLM` built from the settings. Keep `LLMMessage`, `LLMProvider`,
      `EchoLLM` as-is. Update the module docstring to say a real provider now exists.
      Files: `jarvis-backend/src/jarvis_backend/core/llm.py`
      Verify: `JARVIS_ENV=dev pytest tests/unit/test_llm.py -q` (created in step 6) passes;
      `ruff check jarvis-backend/src/jarvis_backend/core/llm.py` is clean.

- [ ] 3. Add `httpx` to the backend's runtime dependencies.
      Move/add `"httpx>=0.27"` into `[project].dependencies` in the backend pyproject (it is
      currently only under `[project.optional-dependencies].dev`; leave the dev entry too or
      drop the duplicate — it is already installed in the dev env, so tests still run).
      Files: `jarvis-backend/pyproject.toml`
      Verify: `python -c "import httpx, jarvis_backend.core.llm as m; print(hasattr(m,'build_llm'))"`
      prints `True`.

- [ ] 4. Thread the LLM through `AgentServices` and the DI container.
      Add `llm: LLMProvider` to the `AgentServices` dataclass in `runtime.py` (import
      `LLMProvider` from `jarvis_backend.core.llm`). In `Supervisor.__init__` accept a new
      keyword `llm: LLMProvider` and build `self._services = AgentServices(memory=memory,
      llm=llm)`. In `Container.create`, call `build_llm(settings)` and pass `llm=...` into the
      `Supervisor(...)` constructor. This is one coherent change across three files; it leaves
      the build green because the agent still works with any provider.
      Files: `jarvis-backend/src/jarvis_backend/agents/runtime.py`,
      `jarvis-backend/src/jarvis_backend/core/supervisor.py`,
      `jarvis-backend/src/jarvis_backend/container.py`
      Verify: `JARVIS_ENV=dev pytest tests/integration/test_workflows.py -q` collects and runs
      (the VISION assertion still refers to the old string here and may fail — that is fixed in
      step 7); `ruff check jarvis-backend/src` is clean.

- [ ] 5. Replace `UnconnectedAgent` with an LLM-backed agent for VISION/DR_STRANGE/QUICKSILVER/NICK_FURY.
      Rename/rewrite `UnconnectedAgent` to `LLMAgent` (keep the `_VERB` map for a one-line
      intent hint if useful). `handle` builds messages: a `system` `LLMMessage` from
      `", ".join(spec.personality)` + `spec.role` plus an instruction to be concise and
      spoken-friendly and to never claim external actions it cannot perform; optional context
      lines from `context` keys `allowed_paths`, `allowed_apps`, `history`
      (`[{user,agent,reply}]`), `memories` (`[str]`), bounded in size; and a `user`
      `LLMMessage` with the task. Call `await self.services.llm.complete(messages)` and return
      `AgentOutcome(reply)` with NO tool_requests. Update the `# SEAM` comment to reflect the
      LLM is now wired. Keep `ShuriAgent` and `NatashaAgent` and their `# SEAM` markers exactly
      as-is (web search = Phase 3, Gmail = Phase 3). Keep the `build_agent` fallback pointing at
      the new class so the four agents (and any future unmapped agent) use it.
      Files: `jarvis-backend/src/jarvis_backend/agents/runtime.py`
      Verify: `JARVIS_ENV=dev pytest tests/unit/test_agents_llm.py -q` (created in step 6)
      passes; `ruff check jarvis-backend/src/jarvis_backend/agents/runtime.py` is clean.

- [ ] 6. Add unit tests for the factory and an LLM-backed agent.
      Create `tests/unit/test_llm.py`: assert `build_llm(Settings(env="dev"))` returns an
      `EchoLLM` instance (default), and that selecting `llm_provider="openai"` with an empty
      base URL also falls back to `EchoLLM`. Create `tests/unit/test_agents_llm.py`: build an
      `AgentSpec` for `VISION`, inject a fake provider whose `complete` returns a known
      sentinel string, build the agent via `build_agent(spec, AgentServices(memory=<fake or
      real MemoryStore>, llm=FakeLLM()))`, call `handle("analyse this", {...})`, and assert the
      sentinel flows into `AgentOutcome.speech`. Follow the fixture/style conventions in the
      existing `tests/unit` suite; `asyncio_mode=auto` means `async def test_...` needs no
      decorator. Use a minimal in-test fake provider (a class with `async def complete`),
      not network.
      Files: `tests/unit/test_llm.py`, `tests/unit/test_agents_llm.py`
      Verify: `JARVIS_ENV=dev pytest tests/unit/test_llm.py tests/unit/test_agents_llm.py -q`
      passes.

- [ ] 7. Update the existing honesty test for the new EchoLLM behaviour.
      In `tests/integration/test_workflows.py::test_agents_never_claim_work_they_did_not_do`,
      replace the VISION assertion so it no longer expects "no language model": assert the
      VISION reply is non-empty and does not contain "no language model" (the default EchoLLM
      now answers). Leave the two `"isn't connected"` assertions (SHURI, Gmail) unchanged and
      do not weaken any other test.
      Files: `tests/integration/test_workflows.py`
      Verify: `JARVIS_ENV=dev pytest tests/integration/test_workflows.py -q` passes.

- [ ] 8. Update docs and `.env.example`.
      - `.env.example`: add a "---- llm ----" block documenting all six `JARVIS_LLM_*` vars
        with the echo default and OpenAI/Ollama examples (no real keys).
      - `docs/PROVIDERS.md`: expand the "LLM / routing" section to describe
        `OpenAICompatibleLLM`, `build_llm`, the env vars, the OpenAI vs Ollama base URLs, and
        the safe-fallback-on-error behaviour. Fix the stale `jarvis_backend/llm.py` path to
        `jarvis_backend/core/llm.py`.
      - `SKILL.md` section 4 ("Pluggable engine skills"): update the LLM row — dev default
        `EchoLLM`, production `OpenAI / Ollama (OpenAI-compatible)`, Env
        `JARVIS_LLM_PROVIDER=openai|ollama`. Optionally update the roster states in section 2
        for VISION/DR. STRANGE/QUICKSILVER/NICK FURY to ✅ (LLM wired); keep SHURI/NATASHA ⛔.
      Files: `.env.example`, `docs/PROVIDERS.md`, `SKILL.md`
      Verify: `JARVIS_ENV=dev pytest tests/unit/test_deployment_files.py -q` passes (the test
      that asserts `.env.example` holds no real secret values must stay green).

- [ ] 9. Full lint + test gate (the project's PR check).
      Run the whole suite and linter per CONTRIBUTING. Fix any regressions before finishing.
      Files: none (verification only)
      Verify: from the repo root, `ruff check .` and `ruff format --check .` are clean, and
      `JARVIS_ENV=dev pytest tests/unit tests/integration -q` passes with no failures.

---

## Verification note (Phase 1 implementation)

Implemented in place in `abduls_jarvis` (no worktree). First iteration — no
`.agents/tasks/review.json` was present.

### Changed files
- `jarvis-backend/src/jarvis_backend/config.py` — added `--- llm ---` block + `llm_base_url_value` property.
- `jarvis-backend/src/jarvis_backend/core/llm.py` — added `OpenAICompatibleLLM` (safe-fallback, never raises) and `build_llm(settings)`.
- `jarvis-backend/src/jarvis_backend/agents/runtime.py` — `AgentServices.llm` field; replaced `UnconnectedAgent` with `LLMAgent` (speech-only, no ToolRequests); `build_agent` fallback → `LLMAgent`.
- `jarvis-backend/src/jarvis_backend/core/supervisor.py` — `Supervisor.__init__(..., llm)` → `AgentServices(memory=..., llm=...)`.
- `jarvis-backend/src/jarvis_backend/container.py` — `Supervisor(..., llm=build_llm(settings))`.
- `jarvis-backend/pyproject.toml` — `httpx>=0.27` moved into runtime deps (also removed a stray UTF-8 BOM that broke strict TOML parsing).
- `tests/integration/test_workflows.py` — honesty test: VISION now asserts non-empty reply without "no language model"; SHURI/Gmail "isn't connected" assertions unchanged.
- `tests/unit/test_llm.py`, `tests/unit/test_agents_llm.py` — new unit tests.
- `.env.example`, `docs/PROVIDERS.md`, `SKILL.md` — documented the new vars.

### New env vars (JARVIS_ prefix)
`JARVIS_LLM_PROVIDER` (echo|openai|ollama, default echo), `JARVIS_LLM_MODEL` (default gpt-4o-mini),
`JARVIS_LLM_BASE_URL` (default ""), `JARVIS_LLM_API_KEY` (Optional[SecretStr]),
`JARVIS_LLM_TIMEOUT_SECONDS` (default 30.0), `JARVIS_LLM_MAX_TOKENS` (default 512).

### Commands run (verification venv `.venv-test`, Python 3.11)
- `ruff check .` → **All checks passed!**
- `ruff check <changed files>` → **All checks passed!**
- `JARVIS_ENV=dev pytest tests/unit tests/integration -q` → **213 passed, 5 skipped, 1 failed**.
  - 5 skipped: `test_postgres.py` (optional `pgserver` not installed).
  - 1 failed: `tests/integration/test_cli.py::test_check_config_fails_loudly_on_unsafe_production`.
    **Pre-existing and unrelated to this change** — verified it also fails with `config.py`
    restored from HEAD (`git stash`). Cause: pydantic 2.14's `ValidationError` rendering now
    echoes the input dict (`'jwt_secret': 'short'`) into the message, so the test's
    "secret value not in output" assertion trips. Not touched (it is a security/config test).
- New + updated tests specifically → **8 passed** (`test_llm.py`, `test_agents_llm.py`,
  `test_agents_never_claim_work_they_did_not_do`).
- Dev startup smoke: `Container.create(dev settings)` succeeds and
  `supervisor._services.llm` is `EchoLLM` (provider=echo, model=gpt-4o-mini). Prod
  fail-closed config does not trip in dev.

---

## Verification note (Phase 1 — review iteration 2: fixes for `.agents/tasks/review.json`)

Addressed all six confirmed findings from `review.md`. Edits in place in `abduls_jarvis`
(no worktree).

### Findings fixed
1. **Exposed Hugging Face token (`.env.example`)** — removed the stray
   `HUGGINGFACE_API_KEY=hf_...` line (it was unused by the app and by `docker-compose.yml`;
   confirmed via grep). Added `test_env_example_has_no_real_secret_values` in
   `tests/unit/test_deployment_files.py` that rejects live token prefixes (`hf_`, `sk-`, …)
   and long secret-shaped values in the committed example. **Operational action still
   required outside this repo: revoke/rotate that token — it remains in git history.**
2. **Malformed response can crash `/assist`** — `OpenAICompatibleLLM.complete` now validates
   `message.content` is a `str` INSIDE the `try` (`raise TypeError` → caught → fallback), so a
   truthy non-string body can no longer raise out of the provider. Regression tests added
   (parametrised list/dict/int/bool content → honest fallback, never raises).
3. **Bearer key over remote plaintext HTTP** — added `_keyed_plaintext_remote(...)` guard in
   the provider (refuses to send and returns fallback; the key is never transmitted) AND a
   `llm_key_plaintext` finding in `Settings.findings()` (error-level in prod → fail-closed,
   warning in dev). Loopback `http://` (local Ollama) stays allowed; hosted providers must use
   `https://`. Tests cover the runtime refusal, the prod config error, and the loopback allow.
4. **Undocumented prompt-context disclosure** — `docs/PROVIDERS.md` now has a "What leaves the
   machine (data boundary)" table listing every bounded field sent to a hosted provider
   (persona, task, allowed paths ×5, allowed apps ×10, memories ×3, recent history ×3) and
   notes nothing leaves the machine with the default `EchoLLM`. Behaviour unchanged (fields
   already bounded/truncated); this is a disclosure, not a redesign.
5. **Provider request contract untested** — added deterministic `httpx.MockTransport` tests in
   `tests/unit/test_llm.py`: exact endpoint (`/v1/chat/completions`) + payload (model,
   max_tokens, messages), Bearer header present with a key / absent without one, normal
   parsing + strip, HTTP-500 fallback, malformed non-string content fallback, empty-content
   fallback, keyed-remote-plaintext refusal.
6. **Removed `UnconnectedAgent` still documented** — updated `SKILL.md` §5 and
   `CONTRIBUTING.md` "add an agent" recipe to describe the real `LLMAgent` fallback and to
   say a not-yet-wired *integration* needs an explicit honest stub (like
   `ShuriAgent`/`NatashaAgent`), not the silent LLM fallback.

### Changed files (iteration 2)
`.env.example`, `jarvis-backend/src/jarvis_backend/core/llm.py`,
`jarvis-backend/src/jarvis_backend/config.py`, `docs/PROVIDERS.md`, `SKILL.md`,
`CONTRIBUTING.md`, `tests/unit/test_llm.py`, `tests/unit/test_deployment_files.py`.

### Commands run (verification venv `.venv`, Python 3.11, `JARVIS_ENV=dev`)
- `ruff check <changed files>` → **All checks passed!**  (`ruff check .` → All checks passed!)
  Note: the repo is not `ruff format`-clean on `main` (93 files would reformat); the project
  gate is `ruff check`, so `ruff format` was not applied — changed code matches the
  surrounding compact style.
- `pytest tests/unit tests/integration -q` → **225 passed, 5 skipped, 1 failed**.
  - 5 skipped: `test_postgres.py` (optional `pgserver` not installed).
  - 1 failed: `tests/integration/test_cli.py::test_check_config_fails_loudly_on_unsafe_production`
    — **pre-existing and unrelated**; re-verified it fails identically with `config.py` stashed
    back to HEAD. Cause (unchanged from iteration 1): pydantic 2.14 echoes the input dict
    (`'secret': 'short'`) into the `ValidationError` message, tripping the test's "secret value
    not in output" assertion. Security/config test — left untouched, not weakened.
- Targeted run: `test_llm.py test_agents_llm.py test_deployment_files.py
  test_agents_never_claim_work_they_did_not_do` → **36 passed**.
- Dev startup smoke: `Container.create(dev settings)` → `supervisor._services.llm` is
  `EchoLLM`; prod fail-closed config does not trip in dev.

---

## Verification note (Phase 1 — review iteration 3: fixes for `.agents/tasks/review.json`)

Addressed all three findings from `review.md` (verdict `CHANGES_REQUESTED`). Edits in place
in `abduls_jarvis` (no worktree).

### Findings fixed
1. **Provider preflight escaped the never-raise boundary (confirmed)** — `_keyed_plaintext_remote`
   in `core/llm.py` called `urlparse(base_url)` *before* `complete`'s `try`, and `urlparse`
   raises `ValueError` on a malformed base URL (e.g. `http://[::1`). The helper now wraps the
   parse in `try/except ValueError` and treats a malformed URL as unsafe (returns `True` →
   `complete` refuses to send and returns the honest fallback), so `complete` NEVER raises.
   `Settings.findings()` got the same guard: a malformed keyed `llm_base_url` now fails closed
   as the `llm_key_plaintext` finding instead of aborting config validation with a raw
   `ValueError`. New regression tests: `test_provider_never_raises_on_malformed_keyed_base_url`
   (parametrised `http://[::1`, `http://[bad`, `http://][` → fallback, no request made) and
   `test_malformed_keyed_base_url_is_flagged_not_crashing_in_config`.
2. **Credential remains in commit history (confirmed)** — this is an operational action outside
   the code: the token is already absent from the working tree (`.env.example`) and guarded by
   `test_env_example_has_no_real_secret_values`, but purging it from commits `6da35a4`/`d3da033`
   is a history-rewriting `git` operation (force-push) that is destructive and requires explicit
   owner action, and the token itself must be **revoked/rotated at the provider** — neither can
   be done safely from inside this step. **Operational action still required before publishing:
   revoke/rotate the token and purge it from branch history (e.g. `git filter-repo`).**
3. **LLM key could leak through validation output (likely)** — `cmd_check_config` printed
   `str(exc)` for a failed `get_settings()`, and pydantic 2.14 echoes the raw input dict (incl.
   `JARVIS_JWT_SECRET` / `JARVIS_LLM_API_KEY`) into that rendering. Added `_safe_config_error`
   in `__main__.py`: for a `ValidationError` it prints only the structured, value-free `msg` of
   each error (never `err["input"]`), stripping pydantic's `"Value error, "` prefix. This also
   fixes the previously-failing baseline test `test_check_config_fails_loudly_on_unsafe_production`
   (the "secret value not in output" assertion now holds). New regression test
   `test_check_config_never_prints_the_llm_api_key` supplies a sentinel LLM key alongside an
   invalid prod config and asserts the sentinel is absent from the output.

### Changed files (iteration 3)
`jarvis-backend/src/jarvis_backend/core/llm.py`,
`jarvis-backend/src/jarvis_backend/config.py`,
`jarvis-backend/src/jarvis_backend/__main__.py`,
`tests/unit/test_llm.py`, `tests/integration/test_cli.py`.

### Commands run (verification venv `.venv`, Python 3.11, `JARVIS_ENV=dev`)
- `ruff check <changed files>` → **All checks passed!**  (`ruff check .` → All checks passed!)
- `pytest tests/unit tests/integration -q` → **231 passed, 5 skipped, 0 failed**.
  - 5 skipped: `test_postgres.py` (optional `pgserver` not installed).
  - The baseline `test_check_config_fails_loudly_on_unsafe_production` failure from iterations
    1–2 is now **resolved** by finding 3's sanitization (not weakened — the assertion that the
    secret value is absent now actually passes).
- Targeted run: `test_llm.py test_cli.py test_config_and_crypto.py` → **48 passed**.
- Dev startup smoke: `Container.create(dev settings)` → `supervisor._services.llm` is
  `EchoLLM`; prod fail-closed config does not trip in dev.

---

## Verification note (Phase 1 — review iteration 4: fixes for `.agents/tasks/review.json`)

`review.json` verdict `CHANGES_REQUESTED` with two confirmed findings. Edits in place in
`abduls_jarvis` (no worktree).

### Findings fixed
1. **Remote plaintext prompt disclosure (confirmed)** — the transport guard previously only
   fired when an API key was present, so a remote `http://` endpoint with no key was accepted
   (even in prod) and `complete()` POSTed the prompt + bounded local context in cleartext. Fix:
   confidentiality no longer depends on the key.
   - `core/llm.py`: replaced `_keyed_plaintext_remote(base_url, has_key)` with
     `_plaintext_remote(base_url)` — true for any non-loopback `http://` host (and, fail-closed,
     a malformed URL). `OpenAICompatibleLLM.complete` calls it unconditionally and refuses to
     send (honest fallback, no request leaves the process) regardless of whether a key is set.
     Loopback `http://` (local Ollama) still allowed. Still NEVER raises.
   - `config.py`: the `findings()` check dropped the `and self.secret("llm_api_key")` condition
     and now flags any remote plaintext `llm_base_url` for a non-echo provider. Finding code
     renamed `llm_key_plaintext` → `llm_plaintext_remote` (error in prod → fail-closed, warning
     in dev); message updated to name the prompt (not just the key) as the exposed data.
   - `docs/PROVIDERS.md`: "Transport safety" paragraph updated — the prompt and bounded context
     are refused over remote `http://` whether or not a key is set.
   - Tests (`tests/unit/test_llm.py`): added
     `test_provider_refuses_to_send_prompt_over_remote_plaintext_http_without_a_key` (the
     reviewer-requested unkeyed refusal test — no request is made) and
     `test_unkeyed_remote_plaintext_http_is_a_config_error_in_prod`; updated the keyed-refusal,
     malformed-URL, config-finding, and loopback-allow tests to the key-independent rule.
2. **Credential remains in commit history (confirmed)** — OPERATIONAL, outside this code step.
   The token is already absent from the working tree (`.env.example`) and guarded by
   `test_env_example_has_no_real_secret_values`, but it is still recoverable from commits
   `6da35a4` / `d3da033`. Purging it is a history-rewriting, force-push `git` operation
   (destructive, needs the repo owner) and the token must be **revoked/rotated at the provider**
   — neither is safe to do automatically from inside this loop. **Action still required before
   publishing the branch: revoke/rotate the Hugging Face token at the provider and purge it from
   history (e.g. `git filter-repo`), then force-push.**

### Changed files (iteration 4)
`jarvis-backend/src/jarvis_backend/core/llm.py`,
`jarvis-backend/src/jarvis_backend/config.py`,
`docs/PROVIDERS.md`,
`tests/unit/test_llm.py`.

### Commands run (verification venv `.venv`, Python 3.11, `JARVIS_ENV=dev`)
- `ruff check jarvis-backend/src/jarvis_backend/core/llm.py config.py tests/unit/test_llm.py`
  → **All checks passed!**  (`ruff check .` → **All checks passed!**)
- `pytest tests/unit/test_llm.py -q` → **25 passed**.
- `pytest tests/unit tests/integration -q` → **236 passed, 5 skipped, 0 failed**.
  - 5 skipped: `test_postgres.py` (optional `pgserver` not installed).
- Dev startup smoke: `build_llm(get_settings())` → `EchoLLM` (provider=echo,
  model=gpt-4o-mini); prod fail-closed config does not trip in dev.
