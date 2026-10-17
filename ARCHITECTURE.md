# JARVIS — Architecture

The deep technical reference. For the product pitch and roadmap see
[`docs/PRODUCTION_PLAN.md`](docs/PRODUCTION_PLAN.md); for the capability map see
[`SKILL.md`](SKILL.md); for the threat model see [`docs/SECURITY.md`](docs/SECURITY.md).

---

## 1. Topology

```
 your PC                                      server (or the same PC)
┌──────────────────────┐   HTTPS + device    ┌─────────────────────────────────────┐
│ jarvis-local-agent   │   JWT (text only)   │ jarvis-backend  (FastAPI, N replicas)│
│  wake word  (local)  │ ──────────────────► │  supervisor ─ router ─ agents        │
│  speech→text (local) │                     │  policy gate ─ approvals ─ audit     │
│  text→speech         │ ◄────────────────── │  memory ─ jobs ─ dashboard ─ metrics │
│  local validator ────┤  approved work      └───────┬──────────────┬──────────────┘
│  executes on the PC  │                       PostgreSQL         Redis
└──────────────────────┘                     (encrypted data)   (cache, limits, events)
```

Three Python packages, one shared contract layer:

```
jarvis-shared/      agents, security types, command classifier, supervisor stages,
                    message schemas, voice profiles, logging        (imported by both sides)
jarvis-backend/     api/ core/ agents/ auth/ storage/ web/
                    + cache, config, crypto, events, jobs, maintenance,
                      middleware, observability, ratelimit, container
jarvis-local-agent/ runtime, wake, stt, tts, overlay, tray, security (validator),
                      tools (executor), backend_client, credentials, config
```

## 2. Shared contracts — `jarvis-shared`

The dependency-light package both sides import so neither trusts the other's types.

| Module | Contents |
|---|---|
| `agents.py` | `AgentId` enum (10 agents), `AgentSpec` (role, personality, `tools` categories, `routing_keywords`), `default_agents()`. |
| `security.py` | `RiskLevel` (SAFE/RESTRICTED/DANGEROUS), `CommandCategory`, `ToolRequest` (the unit of work crossing the gates). |
| `commands.py` | `classify_command()` — the single shell-command classifier used by **both** gates. |
| `supervisor.py` | `SupervisorStage` enum (OBSERVE…REPORT). |
| `messages.py` | `AssistRequest` / `AssistResponse` / `StageTrace` — the `/assist` wire contract. |
| `voice.py` | `AgentVoiceProfile` — one distinct synthetic voice per agent. |
| `conversation.py`, `audio.py`, `log.py` | conversation state, audio types, structured logging. |

### `ToolRequest` — the unit that crosses both gates

Carries `request_id`, `agent_id`, `category`, `action`, `args`, `declared_risk`,
`requires_approval`, `allowed_paths`, `allowed_apps`, `reason`. An agent *proposes* one; the backend
policy engine and the local validator each judge it independently.

## 3. Backend — `jarvis-backend`

### 3.1 The supervisor (`core/supervisor.py`)

The orchestration brain. `Supervisor.handle(AssistRequest)` runs seven stages and records a
`StageTrace` for each; it **only decides** and never executes tools.

```
OBSERVE   _build_context(): bounded client context (allow-lists clipped to 10 items),
          last N turns of history, top-3 relevant memories. Audited.
PLAN      _plan(): router picks an agent; if that agent is disabled, fall back to JARVIS
          with a spoken note. Audited.
PROPOSE   build_agent(spec).handle(task, context) → AgentOutcome(speech, tool_requests).
APPROVE   _gate(): for each ToolRequest, policy.evaluate() → auto-allow / create approval
          (NICK FURY in the trace) / hard-block. Each verdict audited + published on the bus.
EXECUTE   allowed requests are returned to the caller for the LOCAL runtime to run.
VERIFY    marks "blocked by policy" or "ok".
REPORT    _compose_speech() prepends fallback/blocked/approval notes; persist the turn;
          publish a presence event (agent chain, speech, latency, pending approval).
```

Design choices worth noting:
- Only well-typed, bounded values from the (untrusted) client ever reach an agent.
- A delegation note is spoken **only** when the user explicitly named a specialist.
- The presence event drives the dashboard's live "who is working" chain over WebSocket.

### 3.2 Router (`core/router.py`)

Two `Router` implementations, same async `route()` Protocol. Precedence is one chain:
1. **Explicit** `ask/tell/get/have <agent> to <task>` wins (shared regex + alias table); the
   model is never consulted for it.
2. `LLMRouter` (default, `JARVIS_ROUTER=llm`) asks the `LLMProvider` to pick one agent from a
   menu derived from the live roster — this is what makes a natural phrasing route itself.
3. `KeywordRouter` scores whole-word `routing_keywords` (tolerates simple plural/verb endings).
   It is the fallback for *every* LLM failure path, and the short-circuit for `EchoLLM`.
4. Otherwise **JARVIS** answers directly.

`strip_wake_word()` removes the "hey/hi/ok jarvis" preamble first. `route()` never raises, so a
model outage can't 500 an `/assist`; the PLAN stage reason records which path decided. Routing
only picks *who answers* — the two security gates are unchanged.

### 3.3 Agents (`agents/runtime.py`)

Each agent turns a task into `AgentOutcome(speech, tool_requests)`. Agents receive only text +
bounded context — never the microphone or wake word.

- `JarvisAgent` — greetings, status, "name a specialist" guidance.
- `TonyAgent` — emits a `terminal` `run_command` (`git status --short`) in the first allowed path.
- `NatashaAgent` — "send/reply/compose" → a `gmail` `send_email` `ToolRequest` (RESTRICTED, needs
  approval); reads are a SEAM.
- `FridayAgent` — "open/launch/start <app>" → an `application` `open_app` request (RESTRICTED).
- `WongAgent` — `remember`/`recall` against the encrypted `MemoryStore`.
- `ShuriAgent` — honest "web search isn't connected" (SEAM).
- `UnconnectedAgent` — VISION / QUICKSILVER / NICK_FURY / DR_STRANGE: "no language model connected
  yet" (SEAM: inject `LLMProvider`).

`build_agent(spec, services)` maps `AgentId → class`, defaulting to `UnconnectedAgent`. Adding an
agent is additive.

### 3.4 Policy engine — gate 1 (`core/policy.py`)

`PolicyEngine.evaluate(ToolRequest) → PolicyDecision(risk, allow, requires_approval, reason)`.

- `terminal`/`run_command` risk comes from `classify_command()`.
- `keyboard`/`mouse`/`screen` and mutating actions (`write_file`, `delete_file`, `move_file`,
  `send_email`, `post`, `delete`) are raised to at least RESTRICTED.
- `delete_file` on a bulk/root pattern (`*`, trailing slash, `c:`) → DANGEROUS.
- DANGEROUS → blocked (or approval-required if `block_dangerous=False`); RESTRICTED → approval;
  a SAFE request the agent itself flagged is still held. `describe_request()` builds the exact
  human-readable line shown on the approval card.

### 3.5 Auth (`auth/`)

`service.py`, `tokens.py`, `passwords.py`, `deps.py` + `schemas_auth.py` (verified against source):

- **Passwords** — argon2id (`argon2.PasswordHasher`), run in a worker thread so hashing never blocks
  the loop. A constant-effort dummy-hash verify defeats user enumeration by timing. Policy: length
  12–128, common-password blocklist, ≥5 distinct characters. `needs_rehash` upgrades parameters on
  login.
- **JWT** — `HS256`, algorithm pinned on encode *and* verify (the presented `alg` header is never
  trusted; `alg=none` and RS↔HS confusion are blocked). Required claims:
  `exp iat nbf iss aud sub jti sid typ role`. `kid` (first 8 hex of SHA-256 of the secret) enables
  rotation via `JARVIS_JWT_PREVIOUS_SECRETS`. Secret must be ≥32 chars.
- **TTLs** — access **15 min**, refresh **7 days**, device access **10 min**.
- **Refresh rotation + reuse detection** — opaque `token_urlsafe(48)` stored server-side, rotated on
  every use, keeping the *original* expiry (no sliding session). Replay after a 10-second grace →
  the whole token family is revoked and `refresh_reuse_detected` is audited.
- **Session revocation** — access tokens carry `sid`; `authenticate()` checks live session state on
  **every** request (Redis-cached, DB is truth), so logout/revoke is immediate. `change_password`
  revokes all of a user's sessions.
- **Brute force** — three layered rate limits (IP+user, user ×4, IP ×6) over a 15-minute window; no
  hard account lock (so an attacker cannot lock the real owner out).
- **Roles** — `admin` (humans/dashboard) and `agent` (device). `verify_origin` is the CSRF defence on
  cookie endpoints; `verify_metrics_token` uses `hmac.compare_digest`.

### 3.6 Storage (`storage/`)

`db.py`, `models.py`, `crypto.py`, `approvals.py`, `audit.py`, `memory.py`, `turns.py`, `kv.py`,
`identity.py`, `job_runs.py`, `agent_settings.py` (verified against source):

- **Engine** — SQLAlchemy 2.x async. Empty `JARVIS_DATABASE_URL` → `sqlite+aiosqlite` (WAL,
  `foreign_keys=ON`); otherwise `postgresql+asyncpg` (both `postgres://` and `postgresql://` forms
  rewritten) with pool sizing, `pool_recycle`, and TLS modes `disable…verify-full`.
- **Field-level encryption** — `FieldCipher` (MultiFernet over comma-separated keys, newest first),
  stored as `enc:v1:<token>`; legacy plaintext is read transparently so encryption can be enabled on
  an existing DB. Encrypted columns: approval `summary/request/result`, turn `utterance/speech`,
  memory `text/tags`, job-run `output`. The cipher is installed before any store runs.
- **Hash-chained audit log** — `hash = SHA-256(prev_hash + canonical_json(entry))`; `verify()` walks
  the chain and reports the first broken link. Writers serialised by an in-process lock **and** a
  PostgreSQL advisory lock, so replicas cannot fork the chain. Stores **no utterance text**.
- **Atomic approval claim** — state machine `PENDING→GRANTED→EXECUTING→EXECUTED|FAILED` (+ `DENIED`/
  `EXPIRED`); every transition is a conditional `UPDATE … WHERE id=? AND status=<expected>` returning
  the row only when `rowcount == 1`, so grant/claim/complete are each win-once across racing
  replicas. `expire_stale()` ages out grants past the TTL (default 900 s). Verified on **SQLite and
  PostgreSQL**.
- **Memory** — keyword search in Python (the `text` column is encrypted, so the DB cannot match on
  it), capped at 5000 rows; the small surface lets a vector backend replace it later.
- **Gap:** no migration framework — `create_schema()` is `create_all` only. Add Alembic before any
  non-additive schema change.

### 3.7 API & web (`api/`, `app.py`, `web/`)

Routes grouped by concern with per-route role dependencies. Role split: **admin** = humans/dashboard
(config, memory, decisions, audit, jobs, devices); **agent** = the device (confined to `assist`,
reading/claiming/reporting its own approvals, and heartbeating).

| Area | Endpoints (role) |
|---|---|
| Health | `GET /healthz`, `GET /readyz` (public); `GET /metrics` (metrics token, hidden from schema) |
| Auth | `POST /api/auth/login` (public); `/refresh` `/logout` (cookie + origin check); `/logout-all` `/change-password` `/sessions` (admin); `GET /api/auth/me` (any); `POST /api/auth/device-token` (device id+secret) |
| Devices | `GET/POST /api/devices`, `DELETE /api/devices/{id}` (admin) |
| Assist | `POST /api/assist` (any role) |
| Agents | `GET /api/agents` `/voices` `/voices/{id}` `/agents/{id}/activity` (any); `PUT/DELETE /agents/{id}/voice`, `PUT /agents/{id}/enabled` (admin) |
| Memory | `GET/POST /api/memory`, `DELETE /api/memory/{id}`, `DELETE /api/memory?confirm=` (admin) |
| Approvals | `GET /api/approvals` + `/{id}/decision` (admin); `/granted` + `/{id}/claim` + `/{id}/result` (device); `GET /api/approvals/{id}` (any) |
| Jobs | `GET /api/jobs`, `/{id}/runs`, `POST /{id}/run` (admin) |
| System | `/api/system/status` `/api/audit` `/api/audit/verify` `/api/turns` `/api/runtime` (admin); `POST /api/runtime/heartbeat` (device) |
| WebSocket | `GET /ws/events` — admin only; token in the **first message** (never the URL); origin-checked; global + per-principal connection caps; re-validated every 60 s |

`app.py` wires the lifespan (DB, Redis, event bus, scheduler, loop monitor) and middleware
(added-last-runs-first: `TrustedHostMiddleware` → `RequestContextMiddleware` → `BodyLimitMiddleware`
→ routes). Interactive docs/openapi are **disabled in prod**. There is deliberately **no CORS
middleware** (the dashboard is same-origin). Unhandled errors return only an opaque `request_id`.

### 3.8 Cross-cutting

| Module | Responsibility |
|---|---|
| `config.py` | `JARVIS_*` env + `/run/secrets` + `.env` settings; prod fail-closed validation. |
| `crypto.py` | Fernet / `MultiFernet` key handling. |
| `middleware.py` | Body-size cap, security headers / CSP, Host allow-list, request id. |
| `ratelimit.py` | Layered rate limits (per IP+user, user, IP). |
| `cache.py` | Redis accelerator with bounded in-process fallback. |
| `events.py` | Event bus (Redis pub/sub, cross-replica). |
| `jobs.py` | APScheduler jobs run in the backend lifespan / `worker`. |
| `observability.py` | Prometheus metrics + structured logs. |
| `container.py` | Dependency wiring. |

## 4. Local agent — `jarvis-local-agent`

The persistent Windows runtime and the **real security boundary**.

| Module | Responsibility |
|---|---|
| `runtime.py` | Conversation loop; wake → listen → `/assist` → speak; interrupt handling. |
| `wake.py` | `WakeWordEngine` (dev / Porcupine). Audio stays local until the wake word fires. |
| `stt.py` | `STTProvider` (dev / faster-whisper). |
| `tts.py` / `voice_local.py` | `TTSProvider` (dev / pyttsx3) + real-mic voice app. |
| `overlay.py` / `tray.py` | Centre-screen wave overlay; system tray. |
| `security.py` | **`LocalSecurityValidator` — gate 2.** Re-classifies every request, enforces the PC's own allow-lists, returns the parsed argv so the executor runs exactly what was judged. |
| `tools.py` | **`ToolExecutor`** — runs validated requests; integration seams for Gmail/calendar. |
| `backend_client.py` | HTTPS client; refuses plain `http://` to non-loopback hosts. |
| `credentials.py` | Device secret in the Windows Credential Manager. |
| `single_instance.py` | One runtime per PC. |
| `config.py` | Local `JARVIS_*` env (allow-lists, wake phrase, timeouts). |

### Gate 2 — `LocalSecurityValidator.validate()`

Independent of the backend. For each category:
- `terminal` → re-`classify_command()`; DANGEROUS hard-blocked; cwd must be inside an allowed path
  (builtins like `dir`/`ls`/`pwd` exempted from the cwd requirement).
- `filesystem` → path must resolve inside an allowed root (`relative_to`); reads SAFE, writes/moves
  RESTRICTED, deletes DANGEROUS — all need approval except reads.
- `application` → bare name only (a path is refused), must be on the app allow-list **and** found on
  `PATH`.
- `keyboard`/`mouse`/`screen` → RESTRICTED (approval). Integration mutations → RESTRICTED; reads SAFE.

`ValidationResult.allows(approved)` = `ok and (approved or not needs_approval)`. **A grant can satisfy
"needs approval" but can never flip `ok=False`.**

## 5. The security spine (end to end)

```
agent proposes ──► backend policy (gate 1) ──► approval queue ──► admin approves
                     blocks DANGEROUS            (encrypted)        (admin JWT, 15 min)
                                                                        │
  result reported ◄── executor ◄── LOCAL validator (gate 2) ◄── device claims it
  (spoken + audited)  no shell      re-checks everything,            (atomic: exactly once)
                                    can refuse an approved request
```

Separation of duties: **propose ≠ approve ≠ execute**, three different identities.

## 6. Runtime & deployment shape

- **API is stateless** — sessions, rate limits, events live in Postgres/Redis; any replica serves
  any request. A WebSocket sticks to one replica; Redis relays events to all.
- **Exactly one worker** fires scheduled jobs (scaling it would fire each job N times).
- **Redis is an accelerator, never truth** — on outage, sessions fall back to the DB, caches to a
  bounded local store, limits to per-process; degradation is visible in metrics.
- **Postgres is the scaling limit** — tune `JARVIS_DB_POOL_SIZE` × replicas under `max_connections`
  or front it with PgBouncer.

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the three run modes and
[`docs/PROVIDERS.md`](docs/PROVIDERS.md) for swapping engines.

## 7. Extending the system

Everything is additive and behind a Protocol — see [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`SKILL.md`](SKILL.md) §5 for the agent / tool / provider recipes.
