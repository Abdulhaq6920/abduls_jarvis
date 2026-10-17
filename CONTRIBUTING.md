# Contributing to JARVIS

How to extend JARVIS without breaking its security guarantees. Read
[`docs/SECURITY.md`](docs/SECURITY.md) and [`SKILL.md`](SKILL.md) first — the whole design rests on
invariants that are easy to regress if you don't know they're there.

---

## Golden rules (never regress these)

1. A human grant may satisfy "needs approval" but **never** unlocks a hard block.
2. The local validator (gate 2) may **tighten** the backend's verdict, never loosen it.
3. The LLM / agents never get raw shell, filesystem, or input — only `ToolRequest`s through both gates.
4. Commands never run through a shell. Shell metacharacters are DANGEROUS by definition.
5. The backend can never widen the PC's allow-lists.
6. No agent claims work it did not do. Unconnected capabilities say so (keep the `# SEAM` honesty).
7. Never log secrets or utterance text in the audit chain.

If a change would weaken any of these, it needs an explicit design discussion, not a quiet PR.

## Project layout

```
jarvis-shared/      contracts (agents, security types, command classifier, messages, voice)
jarvis-backend/     src/jarvis_backend/{api,core,agents,auth,storage,web} + cross-cutting modules
jarvis-local-agent/ src/jarvis_local_agent/ (runtime, wake, stt, tts, security, tools, …)
docs/               SECURITY, DEPLOYMENT, PROVIDERS, SUCCESS_CRITERIA, PRODUCTION_PLAN
tests/              unit/ integration/   (144 test functions; incl. real PostgreSQL via pgserver)
scripts/            ui_smoke.py, make_docker_secrets.py
```

## Dev setup

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e jarvis-shared -e "jarvis-backend[dev]" -e jarvis-local-agent
python -m jarvis_backend create-admin
python -m jarvis_backend serve           # http://localhost:8077
```

Lint/format with ruff (config in the root `pyproject.toml`): `ruff check .` and `ruff format .`.
Target Python 3.10+ (typing kept 3.10-compatible on purpose — see the ruff ignores).

## Recipe: add an agent

1. **Spec** — add an `AgentSpec` in `jarvis-shared/src/jarvis_shared/agents.py` (id, display name,
   role, personality, `tools` categories it may request, `routing_keywords`).
2. **Voice** — add an `AgentVoiceProfile` in `jarvis-shared/src/jarvis_shared/voice.py` with a
   distinct pitch/speed/style. **Use original synthetic voices; never imitate real actors or
   copyrighted performances.**
3. **Behaviour** — add a handler in `jarvis-backend/src/jarvis_backend/agents/runtime.py` and
   register it in `_AGENT_CLASSES`. An unregistered agent falls through to `LLMAgent` (the
   `build_agent` default) and reasons with the configured `LLMProvider`, speaking text only.
4. **Honesty** — if it needs an integration that isn't connected, do **not** lean on that LLM
   fallback: add an explicit handler that says the integration isn't connected and leave a
   `# SEAM` (as `ShuriAgent`/`NatashaAgent` do).

The router and supervisor need no changes — they pick up the new spec automatically.

## Recipe: close a SEAM (connect a real capability)

1. Find the `# SEAM` comment for the agent in `runtime.py`.
2. Call the real integration (web search, Google OAuth, `LLMProvider.complete`).
3. For anything that touches the machine or sends data, emit a `ToolRequest`:
   - reads → `declared_risk=SAFE`
   - sends / mutations → `declared_risk=RESTRICTED`, `requires_approval=True`
4. Speak only what actually happened. No optimistic claims.
5. Add tests covering the approval path and the refusal path.

## Recipe: add a tool category

1. **Validator (gate 2, authority)** — `jarvis-local-agent/src/jarvis_local_agent/security.py`:
   add the category to `_VALID_CATEGORIES` and a handler, defaulting to the **tightest** reasonable
   classification. Return a parsed/normalised form so the executor runs exactly what was judged.
2. **Policy (gate 1)** — mirror the risk assessment in `jarvis-backend/src/jarvis_backend/core/policy.py`.
3. **Executor** — implement the branch in `jarvis-local-agent/src/jarvis_local_agent/tools.py`.
   Scrub child-process env of secrets; never shell out.
4. **Tests** — add the dangerous case, the out-of-allow-list case, and the happy path.

## Recipe: swap a provider engine

Wake / STT / TTS / LLM / Router / Scheduler all sit behind Protocols. Implement the Protocol method
and extend the `build_*` factory. See [`docs/PROVIDERS.md`](docs/PROVIDERS.md) for each one's env
flag. The supervisor is agnostic; don't change it.

## Testing

```powershell
pytest tests/unit tests/integration            # includes real PostgreSQL via pgserver
python scripts/ui_smoke.py http://localhost:8077   # real-browser dashboard check (0 CSP violations)
```

- Security-relevant changes **must** come with tests. The command classifier, the two gates, the
  approval lifecycle, and the auth flows all have dedicated suites — extend them, don't bypass them.
- `asyncio_mode = "auto"` is set, so `async def test_...` works without a decorator.

## Secrets & config

- Never commit real secrets. `.env` is git-ignored and docker-ignored; a test asserts the committed
  `.env.example` holds no values.
- `python -m jarvis_backend init-secrets` generates every secret; `check-config` validates and
  prints none.
- In `JARVIS_ENV=prod` the server refuses to start on a weak/missing secret, SQLite, a missing
  Redis, or a non-https origin. Don't add a flag to bypass that.

## Pull request checklist

- [ ] None of the golden rules are weakened.
- [ ] New security-relevant behaviour has tests (incl. the refusal/block path).
- [ ] `ruff check .` and `ruff format .` are clean.
- [ ] `pytest tests/unit tests/integration` passes locally.
- [ ] New env vars documented in the relevant `.env.example` and `docs/PROVIDERS.md`.
- [ ] No secrets, tokens, or utterance text added to logs or the audit chain.
- [ ] New voices are original synthetic profiles.
