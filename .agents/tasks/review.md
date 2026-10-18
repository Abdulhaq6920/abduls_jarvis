# OpenAI-compatible reasoning for four JARVIS agents

Phase 1 replaces the disconnected reasoning stub with a configurable OpenAI-compatible provider for VISION, DR. STRANGE, QUICKSILVER, and NICK FURY while retaining `EchoLLM` as the offline default. The provider is selected in the container, passed through the existing seven-stage supervisor, and exposed as a speech-only dependency so model output cannot create `ToolRequest`s. The latest iteration correctly refuses every remote plaintext endpoint, but malformed HTTPS URLs can still escape the provider’s error boundary and crash `/assist`. The recorded gate is otherwise clean at 236 passing tests and five optional PostgreSQL skips. **Watch for:** a **confirmed** uncaught `httpx.InvalidURL` path and a **confirmed** credential still recoverable from two Phase 1 commits.

**Verdict**: NEEDS_CHANGES

## High-level view

`build_llm(settings)` keeps development and tests deterministic by choosing `EchoLLM` by default and whenever a non-echo provider has no base URL. A configured provider is created once by the container and threaded through `Supervisor` into `AgentServices`; routing, the seven supervisor stages, policy evaluation, and the local validator are unchanged.

The four reasoning personas build bounded system context from their role, personality, allow-list names, memories, and recent conversation, then return only the provider’s text. SHURI and NATASHA retain explicit honest stubs, and there is no path from model output to a tool request or action gate.

The provider converts the tested network, HTTP-status, JSON, response-shape, and bracket-malformed URL failures into an honest spoken fallback, and now refuses remote HTTP with or without a key. **Confirmed:** an HTTPS URL with a non-numeric port raises `httpx.InvalidURL`, which is neither an `httpx.HTTPError` nor one of the other caught exceptions, so it propagates through `LLMAgent` and `Supervisor.handle`.

The working tree’s `.env.example` is secret-free and protected by a regression test. **Confirmed:** the removed Hugging Face credential remains in commits `6da35a4` and `d3da033`, so revocation and branch-history cleanup are still required before publication.

The coder’s evidence records `ruff check .` passing and `pytest tests/unit tests/integration -q` completing with 236 passed, five skipped, and zero failed. A narrow malformed-URL spot-check was run because the supplied tests only covered malformed bracket syntax; it reproduced the uncaught `httpx.InvalidURL` without rerunning either suite.

<details>
<summary>Issues (2)</summary>

1. **Malformed HTTPS URL escapes fallback (confirmed)** — an invalid port raises `httpx.InvalidURL` outside the caught exception set and can 500 `/assist`; catch or pre-validate every `httpx` URL-construction failure, reject the same invalid production config, and add an invalid-port regression test.
2. **Credential remains in commit history (confirmed)** — the removed Hugging Face token is still recoverable from commits `6da35a4` and `d3da033`; revoke or rotate it and purge those objects from the branch before publication.

</details>

<details><summary>Details</summary>

## Speech-only provider wiring preserves the action boundary

The dependency flow remains narrower than the existing tool-capable agent path:

```text
Settings -> build_llm -> Container -> Supervisor -> AgentServices
                                                   |
                                                   v
                                                LLMAgent -> speech
                                                   X
                                              no ToolRequest
```

`LLMAgent` constructs a persona prompt from `spec.personality` and `spec.role`, adds bounded context, and returns `AgentOutcome` without tool requests. QUICKSILVER and NICK FURY also receive explicit prompt constraints against claiming scheduling or approval actions, while the integration-dependent SHURI and NATASHA handlers remain stubs rather than silently substituting generic reasoning. The LLM therefore cannot bypass either policy evaluation or the local validator.

## Malformed HTTPS URLs escape the never-raise boundary

**Confirmed:** `_plaintext_remote` checks bracket-malformed URLs because `urllib.parse.urlparse` raises for them, but it accepts `https://example.com:bad/v1`: reading `parsed.hostname` does not validate the port. `httpx` later raises `httpx.InvalidURL("Invalid port: 'bad'")` while constructing the request. `InvalidURL` inherits directly from `Exception`, not `httpx.HTTPError`, and the provider catches only `httpx.HTTPError`, `KeyError`, `IndexError`, `TypeError`, and `ValueError`, so the exception leaves `complete()` and can 500 `/assist` at the supervisor’s PROPOSE stage.

The provider should catch `httpx.InvalidURL` inside the same fallback boundary, or validate/construct the URL before the request and convert every invalid form to the honest fallback. Add a regression test for malformed HTTPS ports; the production config path should also reject the same invalid URL rather than accepting a provider that will fail at request time.

## Removed credential remains in branch history

**Confirmed:** a history search for the removed `HUGGINGFACE_API_KEY` assignment still finds commits `6da35a4` and `d3da033`; the assignment is absent from `HEAD`, but the credential remains recoverable from branch objects. The deployment-file regression test protects future edits to `.env.example`, but it cannot invalidate an already exposed token. Revoke or rotate the token at the provider and purge it from branch history before publishing the branch.

## Invalid-port failure is absent from provider coverage

The provider tests cover default selection, request construction, optional Bearer authentication, network and malformed-body fallbacks, bracket-malformed URLs, and keyed/unkeyed plaintext refusal. **Not tested (confirmed):** an invalid HTTPS port rejected by `httpx` rather than `urllib.parse`; add this case to enforce the advertised never-raise contract.

</details>

<details>
<summary>File map</summary>

- `jarvis-backend/src/jarvis_backend/core/llm.py` — implements provider selection, OpenAI-compatible requests, fallback behavior, and remote plaintext refusal.
- `jarvis-backend/src/jarvis_backend/config.py` and `__main__.py` — add typed LLM settings, production transport findings, and value-free configuration-error output.
- `jarvis-backend/src/jarvis_backend/agents/runtime.py` — turns four reasoning personas into bounded, speech-only LLM consumers while retaining integration stubs.
- `jarvis-backend/src/jarvis_backend/core/supervisor.py` and `container.py` — inject the selected provider without changing the seven-stage execution contract.
- `jarvis-backend/pyproject.toml` — promotes `httpx` to a runtime dependency.
- `.env.example`, `docs/PROVIDERS.md`, `SKILL.md`, and `CONTRIBUTING.md` — document provider configuration, outbound context, extension behavior, and safety rules.
- `tests/unit/test_llm.py`, `test_agents_llm.py`, and `test_deployment_files.py` — cover provider contracts, speech-only flow, failure handling, and committed secret templates.
- `tests/integration/test_workflows.py` and `test_cli.py` — update VISION’s offline behavior and verify configuration output does not expose the LLM key.

Full diff: `git diff origin/main...HEAD`

</details>
