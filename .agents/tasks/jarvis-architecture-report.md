# JARVIS — Architecture & Production-Readiness Report

Read-only investigation of `c:\Users\Lenovo\Desktop\JARVIS\abduls_jarvis`. Every claim below is cited to the actual source file read during this pass; no files were modified.

## Summary answer

JARVIS is a **well-engineered, security-first voice assistant monorepo** whose *platform* (auth, storage, API, hardening, local-agent security) is genuinely production-grade, but whose *intelligence* (the specialist agents) is almost entirely **stubbed behind explicit `# SEAM` markers**. The code is honest about this: agents say "not connected yet" rather than faking work. The two security gates (backend policy + local validator), argon2id+JWT auth with refresh rotation/reuse detection, Fernet field encryption, and a hash-chained audit log are all real and implemented. The chief production gap is **capability, not plumbing**: no LLM, no web search, no Gmail/Calendar OAuth, and 4 of 10 agents are pure placeholders.

Three packages (all editable installs):
- `jarvis-shared` — contracts/domain models
- `jarvis-backend` — FastAPI supervisor, auth, storage, scheduler, observability
- `jarvis-local-agent` — Windows runtime: wake word, STT/TTS, executor, tray

---

## 1. AUTH layer

Files: `jarvis-backend/src/jarvis_backend/auth/{tokens,service,passwords,deps}.py`, `schemas_auth.py`

**Password hashing — argon2id (confirmed).** `passwords.py` uses `argon2.PasswordHasher` (argon2-cffi, argon2id defaults). Hashing/verification run in a worker thread (`asyncio.to_thread`) so they never block the loop. A `_DUMMY_HASH` gives constant-effort verification for unknown users (timing-equalisation against user enumeration). Policy follows NIST 800-63B: length 12–128, a small common-password blocklist, and a `len(set(password)) < 5` variety check (`check_policy`). `needs_rehash` supports transparent parameter upgrades on login.

**JWT — HS256 with pinned algorithm and required claims (confirmed).** `tokens.py`:
- `ALGORITHM = "HS256"`, pinned on both encode and verify; the presented `alg` header is never trusted (blocks `alg=none` / RS↔HS confusion).
- **Required claims** (`_REQUIRED`): `exp iat nbf iss aud sub jti sid typ role`. Missing any → rejected via PyJWT `options={"require": _REQUIRED}`.
- Also validates `iss`/`aud`, 5-second leeway, and `typ == "access"`.
- **Key rotation**: tokens carry a `kid` (first 8 hex of SHA-256 of secret); previous secrets (`jwt_previous_secrets`, comma-separated) still verify during a roll. Secret must be ≥32 chars or `TokenCodec.__init__` raises.
- `Claims.kind` is `"device"` when `sub` starts with `device:`, else `"user"`.

**Token TTLs (confirmed, from `config.py`):**
| Token | Setting | Default |
|---|---|---|
| Access | `access_token_minutes` | **15 min** |
| Refresh | `refresh_token_days` | **7 days** |
| Device access | `device_token_minutes` | **10 min** |

**Refresh rotation + reuse detection (confirmed).** `service.py`:
- Refresh tokens are opaque `secrets.token_urlsafe(48)` strings stored server-side, rotated on **every** use; the new session keeps the **original expiry** (`row.expires_at - now`) — no endless sliding session.
- Reuse of an already-rotated token *after* a 10-second grace (`REFRESH_REUSE_GRACE = timedelta(seconds=10)`) triggers `_revoke_family(..., "reuse_detected")` — the whole token family is revoked and `refresh_reuse_detected` is audited. Within the grace window a late replay just fails (tolerates double-submit races).
- `sessions.rotate(row.id)` is a conditional update; losing the race → 401 (no double issuance).

**Session revocation (confirmed).** Access tokens carry `sid`; `authenticate()` checks `_session_active(sid)` on **every request**, Redis-cached (active 30 s / revoked 900 s via `_ACTIVE_TTL`/`_REVOKED_TTL`) with the database as source of truth. `logout`, `logout_everywhere`, `change_password` (revokes all sessions of the user), and `revoke_device` all mark sessions revoked immediately. Device subjects validate `sid == "dev:"+device_id`.

**Brute-force defence (confirmed).** `login()` enforces three rate limits: per IP+user (`limit`), per user (`limit*4`), per IP (`limit*6`), window `login_window_seconds` (default 900 s, `login_attempts_per_window` default 5). No hard account lock (deliberate, to avoid lockout-DoS of the real owner). Successful login clears the IP+user counter.

**deps.py roles.** Roles are `ADMIN = "admin"` and `AGENT = "agent"`. Dependencies: `admin_only`, `agent_only`, `any_role`, `require_role(*allowed)`. `verify_origin` is CSRF defence for cookie-authenticated endpoints (refresh/logout); `verify_metrics_token` uses `hmac.compare_digest`. `client_ip` only honours `X-Forwarded-For` for `trusted_proxy_hops` counted from the right.

---

## 2. STORAGE layer

Dir: `jarvis-backend/src/jarvis_backend/storage/` → `agent_settings.py, approvals.py, audit.py, db.py, identity.py, job_runs.py, kv.py, memory.py, models.py, turns.py, __init__.py`. Stores exported from `__init__.py`: `AgentSettingsStore, ApprovalStore, AuditLog, Database, DeviceStore, JobRunStore, KeyValueStore, MemoryStore, SessionStore, TurnStore, UserStore`.

**SQLite + PostgreSQL (confirmed).** `db.py` uses SQLAlchemy 2.x async. Empty `database_url` → `sqlite+aiosqlite:///<data_dir>/jarvis.db`; otherwise `postgresql+asyncpg://…` (it rewrites `postgres://`/`postgresql://` prefixes). SQLite path sets WAL, `foreign_keys=ON`, `busy_timeout=5000`. Postgres path adds pool sizing, `pool_recycle=1800`, and TLS via `_postgres_connect_args` (`database_ssl`: disable/prefer/require/verify-ca/verify-full; `verify-full` checks hostname). `UTCDateTime` TypeDecorator normalises tz-aware UTC on both backends.

**Field-level encryption — Fernet/MultiFernet (confirmed).** `crypto.py` defines `FieldCipher` (MultiFernet over comma-separated keys, newest-first), stored as `enc:v1:<token>`; values without the prefix are read as legacy plaintext (enables encryption on an existing DB). `EncryptedText`/`EncryptedJSON` TypeDecorators encrypt on write, decrypt on read. `container.py` calls `set_cipher(FieldCipher.from_setting(settings.secret("data_key")))` **before any store runs** (also in `__main__.py cmd_migrate`). Encrypted columns per `models.py`: `ApprovalRow.summary/request/result`, `TurnRow.utterance/speech`, `MemoryRow.text/tags`, `JobRunRow.output`. Algorithm is Fernet = AES-128-CBC + HMAC-SHA256 (authenticated). Honest limit documented: protects data at rest, not a live process (key in memory).

**Hash-chained audit log (confirmed).** `audit.py`: each `AuditEntry` stores `prev_hash` and `hash = SHA-256(prev_hash + canonical_json(entry))` with sorted-key canonical JSON. `verify()` walks the chain in id order and reports the first broken link or content mismatch. Writers serialised by an in-process `asyncio.Lock` **and** a PostgreSQL advisory lock (`pg_advisory_xact_lock(746100001)`) so replicas can't fork the chain. Documented honest limit: proves internal consistency only; a full-write attacker could recompute, and deleting the newest entries is undetectable without an external anchor.

**Atomic approval claim (confirmed).** `approvals.py` state machine `PENDING→GRANTED→EXECUTING→EXECUTED|FAILED` (plus `DENIED`/`EXPIRED`). Every transition is a single conditional `UPDATE … WHERE id=? AND status=<expected>` in `_transition`, returning the row only when `rowcount == 1`. So `resolve` (grant/deny), `claim` (GRANTED→EXECUTING), and `complete` are each win-once across racing replicas/agents. `expire_stale()` ages out stale `PENDING`/`GRANTED` past `ttl_seconds` (default 900).

**Memory (confirmed).** `memory.py` is keyword search in Python (not SQL) because the `text` column is encrypted and the DB can't match on it; it loads a capped set (`MAX_MEMORIES = 5000`) and scores. Hard cap prevents a runaway client filling the DB. Surface is intentionally small so a vector backend can replace it later.

---

## 3. API layer

Dir: `jarvis-backend/src/jarvis_backend/api/` → `deps.py, ws.py, routes_{agents,approvals,assist,auth,devices,jobs,memory,system}.py`. App factory: `app.py`.

**HTTP endpoints (verified from the router decorators):**

| Method | Path | Auth / role |
|---|---|---|
| GET | `/healthz` | public |
| GET | `/readyz` | public |
| GET | `/metrics` | metrics bearer token (`verify_metrics_token`), hidden from schema |
| GET | `/` and `/ui/*` | public (dashboard SPA + static) |
| POST | `/api/auth/login` | public |
| POST | `/api/auth/refresh` | refresh cookie + `verify_origin` |
| POST | `/api/auth/logout` | principal + `verify_origin` (204) |
| POST | `/api/auth/logout-all` | **admin** + `verify_origin` |
| GET | `/api/auth/me` | principal |
| POST | `/api/auth/change-password` | **admin** (204) |
| GET | `/api/auth/sessions` | **admin** |
| POST | `/api/auth/device-token` | public (device id+secret) |
| GET | `/api/devices` | **admin** |
| POST | `/api/devices` | **admin** (201) |
| DELETE | `/api/devices/{device_id}` | **admin** (204) |
| POST | `/api/assist` | any role (admin or device) |
| GET | `/api/agents` | any role |
| GET | `/api/voices` | any role |
| GET | `/api/voices/{agent_id}` | any role |
| PUT | `/api/agents/{agent_id}/voice` | **admin** |
| DELETE | `/api/agents/{agent_id}/voice` | **admin** |
| PUT | `/api/agents/{agent_id}/enabled` | **admin** |
| GET | `/api/agents/{agent_id}/activity` | any role |
| GET | `/api/memory` | **admin** |
| POST | `/api/memory` | **admin** (201) |
| DELETE | `/api/memory/{memory_id}` | **admin** (204) |
| DELETE | `/api/memory` | **admin** (requires `?confirm=`) |
| GET | `/api/approvals` | **admin** |
| GET | `/api/approvals/granted` | **device/agent** |
| POST | `/api/approvals/{id}/decision` | **admin** |
| POST | `/api/approvals/{id}/claim` | **device/agent** |
| POST | `/api/approvals/{id}/result` | **device/agent** |
| GET | `/api/approvals/{id}` | any role |
| GET | `/api/jobs` | **admin** (router-level) |
| GET | `/api/jobs/{id}/runs` | **admin** |
| POST | `/api/jobs/{id}/run` | **admin** |
| GET | `/api/system/status` | **admin** |
| GET | `/api/audit` | **admin** |
| GET | `/api/audit/verify` | **admin** |
| GET | `/api/turns` | **admin** |
| POST | `/api/runtime/heartbeat` | **device/agent** (204) |
| GET | `/api/runtime` | **admin** |

Role split: humans/dashboard = **admin**; the local agent (device) = **agent**. The agent role is confined to `assist`, reading its granted approvals, claiming/reporting them, and heartbeating. Admin controls config, memory, decisions, audit, jobs, devices.

**WebSocket:** `GET /ws/events` (`ws.py`). Token is sent as the **first message** (never in the URL), within `AUTH_TIMEOUT_SECONDS = 5`. **Admin only.** Origin checked (`_origin_ok`). Global cap `max_ws_connections` (200) and per-principal cap `max_ws_per_principal` (5). Token re-validated every `REVALIDATE_SECONDS = 60`; a logged-out session's socket is closed. Close codes 1008 (policy) / 1013 (try later).

**app.py:** docs/openapi are **disabled in prod** (`docs_url=None`, `openapi_url=None`). Middleware stack (added-last-runs-first): `TrustedHostMiddleware` → `RequestContextMiddleware` → `BodyLimitMiddleware` → routes. Loopback hosts always allowed. **No CORS middleware** (deliberate: same-origin dashboard). Global exception handler returns an opaque `request_id`, logging detail server-side only.

---

## 4. CONFIG / hardening

Files: `config.py, crypto.py, middleware.py, ratelimit.py, cache.py, observability.py, events.py, jobs.py, maintenance.py`.

**Prod refuses to start (confirmed).** `config.py` `_enforce_production_rules` (a pydantic `model_validator(mode="after")`) raises `ValueError` listing problems (never values) when any `findings()` entry is `error` and `env=prod`. Error-level checks in prod:
- `jwt_secret_weak` — `JARVIS_JWT_SECRET` < 32 chars
- `jwt_secret_placeholder` — looks like a placeholder (`_PLACEHOLDERS` list, or `< 10` distinct chars)
- `data_key_missing` — `JARVIS_DATA_KEY` unset (data-at-rest NOT encrypted)
- `sqlite_in_prod` — SQLite in prod → must use PostgreSQL
- `redis_missing` — `JARVIS_REDIS_URL` unset in prod
- `hosts_open` — `JARVIS_ALLOWED_HOSTS` is `*` or empty
- `origin_not_https` — `JARVIS_PUBLIC_ORIGIN` not `https://`
- `cookie_insecure` — cookies not Secure
- `metrics_token_weak` — metrics enabled but token < 24 chars

Warnings (non-fatal): `db_tls_off`, `db_tls_optional`, `redis_tls_off`, `access_token_long`. In **dev**, errors downgrade to warnings and missing secrets are generated once into `data_dir/dev_secrets.json` (mode 0600) via `with_dev_secrets`.

**Secret handling.** All secrets are `SecretStr` (print as `**********`). Sources, highest-first: real `JARVIS_*` env vars → secrets dir (`/run/secrets` or `JARVIS_SECRETS_DIR`, one file per var) → `.env` (dev). `describe()` logs only set/unset + length. `warn_if_env_file_is_world_readable` (POSIX) flags a loose `.env`.

**Env var names (prefix `JARVIS_`):** `ENV, HOST, PORT, DATA_DIR, ALLOWED_HOSTS, PUBLIC_ORIGIN, TRUSTED_PROXY_HOPS, MAX_BODY_BYTES, MAX_WS_CONNECTIONS, MAX_WS_PER_PRINCIPAL, JWT_SECRET, JWT_PREVIOUS_SECRETS, JWT_ISSUER, JWT_AUDIENCE, ACCESS_TOKEN_MINUTES, REFRESH_TOKEN_DAYS, DEVICE_TOKEN_MINUTES, COOKIE_NAME, COOKIE_SECURE, LOGIN_ATTEMPTS_PER_WINDOW, LOGIN_WINDOW_SECONDS, ASSIST_PER_MINUTE, DATA_KEY, TURN_RETENTION_DAYS, HISTORY_TURNS, APPROVAL_TTL_SECONDS, DATABASE_URL, DATABASE_SSL, DATABASE_CA_FILE, DB_POOL_SIZE, DB_MAX_OVERFLOW, REDIS_URL, CACHE_DEFAULT_TTL, CACHE_MAX_LOCAL_ITEMS, RUN_SCHEDULER, TIMEZONE, LOG_LEVEL, LOG_FORMAT, LOG_TO_FILE, METRICS_ENABLED, METRICS_TOKEN`.

**Rate limits (confirmed).** `ratelimit.py` `RateLimiter.enforce` raises 429 with `Retry-After`. Concrete limits in code: login triple limit (above); `refresh:ip` 30/300 s; `device:ip` 60/300 s and `device:id` 30/300 s (`service.py`); assist `assist_per_minute` default 60. Degrades to per-process when Redis is down (bounded, counted).

**Body-size cap (confirmed).** `middleware.py` `BodyLimitMiddleware` rejects via Content-Length up front **and** counts streamed bytes (clients can lie), returning 413. Default `max_body_bytes = 65_536`.

**CSP / security headers (confirmed).** `security_headers()` sets a strict `Content-Security-Policy` (`default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`), plus `x-content-type-options=nosniff`, `x-frame-options=DENY`, `referrer-policy=no-referrer`, `permissions-policy` disabling geo/cam/mic/payment, COOP `same-origin`, CORP `same-origin`, and HSTS (`max-age=63072000; includeSubDomains`) **in prod only**. API responses get `cache-control: no-store`.

**Redis-degradation fallback (confirmed).** `cache.py` `Cache` wraps a Redis backend with a bounded in-process LRU (`LocalCache`, item cap, lazy+periodic expiry). On any Redis error it `_note_failure` (increments `cache_errors`, sets `fallback_active`, throttled warning) and serves from local. `delete` invalidates both tiers. `incr_window` is atomic in Redis (MULTI/EXEC pipeline). `events.py` mirrors this: `InMemoryBus` vs `RedisBus` pub/sub (`build_bus`); Redis publish failures are swallowed (best-effort live updates).

**Prometheus metric names (confirmed, `observability.py`):** `jarvis_http_requests_total`, `jarvis_http_request_seconds`, `jarvis_http_in_flight`, `jarvis_assist_seconds`, `jarvis_auth_events_total`, `jarvis_rate_limited_total`, `jarvis_ws_connections`, `jarvis_event_loop_lag_seconds`, `jarvis_process_rss_bytes`, `jarvis_process_cpu_percent`, `jarvis_process_threads`, `jarvis_process_open_files`, `jarvis_uptime_seconds`, `jarvis_cache_hits`, `jarvis_cache_misses`, `jarvis_cache_errors`, `jarvis_cache_fallback_active`, `jarvis_cache_local_items`, `jarvis_db_pool_checked_out`, `jarvis_pending_approvals`. Labels are never user input (bounded series). An event-loop-lag monitor runs as a background task.

**Jobs (confirmed, `jobs.py`).** Four static cron `JobSpec`s: `morning-email` (08:00 daily, NATASHA), `evening-dev` (19:00 daily, TONY), `weekly-summary` (Mon 09:00, DR STRANGE), `hourly-health` (hourly, TONY). Jobs run through the normal supervisor path (same policy gate + audit). `JobScheduler` (APScheduler) must run in exactly one process; `run_scheduler` gates it. Note: `morning-email`/`weekly-summary` route to agents (NATASHA/DR STRANGE) that are **stubbed**, so those jobs currently produce "not connected yet" output.

---

## 5. LOCAL AGENT runtime

Dir: `jarvis-local-agent/src/jarvis_local_agent/` → `runtime.py, tools.py, backend_client.py, credentials.py, wake.py, stt.py, tts.py, voice_app.py, voice_local.py, overlay.py, tray.py, single_instance.py, config.py, notifications.py, security.py, __main__.py`.

**Provider seams — dev vs production (confirmed).** The runtime depends only on Protocols, so engines are swappable by config (`JARVIS_WAKE_ENGINE`, `JARVIS_STT_ENGINE`, `JARVIS_TTS_ENGINE`). There are effectively **two production voice paths plus a dev path**:
- **Wake** (`wake.py`): `DevWakeWordEngine` (stdin/typed, zero-dep, CI) and `PorcupineWakeWordEngine` (Picovoice, needs `JARVIS_PICOVOICE_KEY`, `pragma: no cover - needs hw`). `build_wake_engine` selects on `"porcupine"` else dev.
- **Real on-device stack** (`voice_local.py`, launched by `voice_app.py`/`jarvis-voice`): `VoskWakeWordEngine` + `VoskSTTProvider` (offline Vosk, restricted-grammar wake with an energy gate `JARVIS_WAKE_MIN_LEVEL` to reject near-silence false positives) + `SapiTTSProvider` (pyttsx3/Windows SAPI, per-agent rate/volume in `VOICE_MAP`). A single `MicStream` owns the device (one consumer → no mic conflicts).
- **STT/TTS seams** (`stt.py`, `tts.py`): `DevSTTProvider` (stdin) and a `WhisperSTTProvider` seam (faster-whisper); `DevTTSProvider` console fallback.

> **Documentation discrepancy worth flagging:** `docs/PROVIDERS.md` describes seams as porcupine/whisper/pyttsx3, but the *actual wired real-voice runtime* (`voice_app.py` → `voice_local.py`) uses **Vosk + Windows SAPI**, with Porcupine and faster-whisper present as the alternative (`voice-alt`) extra, not the default. `requirements.txt` matches the code: `vosk`/`pyttsx3` under `[voice]`, `pvporcupine`/`faster-whisper` under `[voice-alt]`.

**Executor (confirmed, `tools.py`).** Runs only requests the local validator already cleared. Defence in depth: **never uses a shell** (`shell=False`, argv list produced by the validator), caps runtime (`TIMEOUT_SECONDS = 30`) and output (`MAX_OUTPUT = 4000`), re-checks filesystem paths against its own allow-list, resolves programs with `shutil.which`, `stdin=DEVNULL`. Supports `terminal` (+ `pwd`/`ls` builtins), `filesystem` (read/list/write), `application` (launch). `gmail`/`calendar`/`browser` return explicit "not connected yet" (**SEAM**).

**Child-process env scrubbing (confirmed).** `child_environment()` strips any variable matching `(token|secret|password|passwd|api[_-]?key|credential|^JARVIS_)` (case-insensitive) before spawning, so a launched program can't read the agent's credentials. Applied in both `_terminal` and `_application`.

**Credential storage — Windows Credential Manager (confirmed, `credentials.py`).** Device id/secret preferred from `keyring` (Windows Credential Manager, OS-encrypted), else `JARVIS_DEVICE_ID`/`JARVIS_DEVICE_SECRET` env. CLI `set`/`clear` with a hidden `getpass` prompt. `config.py RuntimeConfig.load()` pulls from keyring when env is unset; `device_secret` is a `SecretStr`.

**Plain-http refusal to non-loopback (confirmed).** `config.py backend_is_safe_transport()` returns true only for `https`, loopback hosts (`localhost/127.0.0.1/::1`), or an explicit `JARVIS_ALLOW_INSECURE_BACKEND=true`. `backend_client.py` **raises `BackendError` in its constructor** otherwise, refusing to send device credentials over plain http to a remote host. Device JWT auto-renews before expiry and once more on a 401. Documented privacy guarantee: only text crosses the boundary; mic audio never leaves the machine.

**Local validator = the real boundary (confirmed, `security.py`).** `LocalSecurityValidator.validate` re-classifies every request with the shared `classify_command`, enforces path/app allow-lists only this machine knows, and can **reject what the backend allowed but never loosen**. `allows(approved)` = `ok and (approved or not needs_approval)`; a hard block (DANGEROUS / out-of-allow-list / malformed) can never be overridden by an approval. Applications must be a bare name (no path separators) and on `allowed_apps`.

**Runtime FSM (confirmed, `runtime.py`).** `IDLE→WAKE_DETECTED→LISTENING→PROCESSING→RESPONDING→ACTIVE_CONVERSATION` with conversation-timeout fallback to IDLE. Barge-in ("JARVIS stop/cancel/quiet/enough") handled locally, no backend. Wake engine suspended during capture/speech so JARVIS can't wake itself. Background loops: heartbeat (`heartbeat_seconds` 15) and approval polling (`approval_poll_seconds` 3) — polls granted approvals, atomically claims, re-validates locally, executes, reports result.

---

## 6. TESTS + CI

**Test tree** (`tests/`): `conftest.py`, `mic_check.py`, `overlay_preview.py` (manual helpers), `unit/` (7 test modules), `integration/` (5 test modules).

**Test-function counts (via `def test_`):**
| File | Fns |
|---|---|
| unit/test_local_security.py | 20 |
| unit/test_storage.py | 14 |
| unit/test_deployment_files.py | 14 |
| unit/test_config_and_crypto.py | 12 |
| unit/test_cache_and_ratelimit.py | 10 |
| unit/test_commands.py | 5 |
| unit/test_launchers.py | 3 |
| integration/test_auth.py | 26 |
| integration/test_workflows.py | 21 |
| integration/test_end_to_end.py | 6 |
| integration/test_postgres.py | 5 |
| integration/test_cli.py | 8 |
| **Total `def test_`** | **144** |

The 144 count is test *functions*; `SECURITY.md`'s "~210 tests" is plausible once pytest parametrization expands cases (this pass did not execute the suite — read-only). What is covered, by file name and placement: local security gate (largest unit suite), storage stores, config/crypto, cache+ratelimit, command classifier, launcher scripts, deployment-file sanity; and integration: full auth lifecycle, agent/approval workflows, end-to-end assist, CLI, and **real PostgreSQL** (`test_postgres.py`, via `pgserver` per the CI comment).

**CI (`.github/workflows/ci.yml`), four jobs:**
- **test** (ubuntu, py3.11): installs pinned (`-c constraints.txt`) editable packages + `[postgres,dev]` extras + pytest/pytest-asyncio/fakeredis/pyyaml/playwright; `ruff check` on all packages + tests; `pytest tests/unit tests/integration -q` with `JARVIS_ENV=dev` (throwaway per-run secrets). Comment confirms PostgreSQL runs in-suite via pgserver.
- **audit**: `pip-audit -r constraints.txt --strict` against the exact pinned set.
- **secrets**: `gitleaks` over full history (`fetch-depth: 0`).
- **image**: `docker build -f docker/Dockerfile`; asserts the image contains no `.env`/no `/src`, runs as non-root uid `10001`, and `trivy` scan failing on CRITICAL/HIGH (unfixed ignored).

`permissions: contents: read` (least privilege); `concurrency` cancels superseded runs.

---

## 7. PRODUCTION GAPS (from code, not just docs)

Full-tree grep for `SEAM|TODO|FIXME|XXX|HACK|NotImplemented`. Findings (the `XXX`/`todo` hits in `config.py`/`scan_for_secrets.py` are placeholder-detection *patterns*, not markers; the `NotImplementedError` in `__main__.py` is a legitimate Windows signal-handler guard). Real seams:

**Stubbed agent capabilities (`agents/runtime.py`):**
- **SHURI** — web search / research: `# SEAM` → "web search isn't connected yet, so I have no findings." No search tool, no LLM synthesis.
- **NATASHA** — Gmail: `# SEAM` for inbox read via Google OAuth → "Gmail isn't connected yet." (Can still *prepare* an outbound email `ToolRequest` that routes through the approval gate, but there is no connected sender.)
- **UnconnectedAgent** covers **VISION, QUICKSILVER, NICK FURY, DR STRANGE** — all `# SEAM` → "no language model is connected yet." These are placeholders pending an `LLMProvider`.
- Only **JARVIS, TONY (git status), FRIDAY (launch allow-listed app), WONG (memory)** do real work. `build_agent` maps 6 real classes; the other 4 fall through to `UnconnectedAgent`.

**LLM provider not wired (`core/llm.py`):** docstring states the template agents "do not call an LLM yet; this is the seam." `EchoLLM` keeps the pipeline runnable offline. No OpenAI/Anthropic/local model connected.

**Local-agent integration seams:**
- `tools.py` — `gmail`/`calendar`/`browser` categories return "integration is not connected yet" (`# SEAM` for OAuth-backed integrations).
- `stt.py` / `wake.py` — `WhisperSTTProvider` and `PorcupineWakeWordEngine` are production seams; the dev engines and the Vosk/SAPI stack are what actually run. Porcupine's `start()` is `pragma: no cover - needs hw`.

**GUI automation:** the local validator *recognises* `keyboard`/`mouse`/`screen` categories and marks them "needs approval" (`security.py`), but the executor (`tools.py`) has **no handler** for them — such a request falls to "control is not implemented." So screen/keyboard/mouse automation is declared but unimplemented.

**Operational gaps implied by the code:**
- No DB migration framework — `Database.create_schema()` is `create_all` only; schema evolution beyond additive is unaddressed (encryption has a `v1` prefix path for forward compat, but no Alembic).
- Audit log integrity is internal-only (documented): no external/write-once anchor, so newest-entry truncation is undetectable.
- Scheduler jobs `morning-email` and `weekly-summary` target stubbed agents, so two of four scheduled jobs currently emit "not connected yet."

---

## Production readiness scorecard

| Component | State | Gap |
|---|---|---|
| Auth (argon2id, JWT HS256, rotation, revocation) | **Production-ready** | None material; TTLs sane, reuse detection present |
| Storage engine (SQLite/Postgres, async SQLAlchemy) | **Production-ready** | No migration tool (Alembic); `create_all` only |
| Field encryption (Fernet/MultiFernet) | **Production-ready** | Key in process memory (documented); pair with disk encryption |
| Audit log (hash chain + advisory lock) | **Production-ready** | No external anchor; truncation of newest entries undetectable |
| Approval queue (atomic claim) | **Production-ready** | None |
| API surface + RBAC (admin/agent) | **Production-ready** | None; roles enforced per-route and router-level |
| WebSocket events | **Production-ready** | Admin-only; caps + revalidation present |
| Config hardening (prod refuse-to-start) | **Production-ready** | None; comprehensive error/warn checks |
| Rate limiting | **Production-ready** | Per-process degradation when Redis down (documented) |
| Cache / event bus (Redis + fallback) | **Production-ready** | Single-replica only without Redis (enforced in prod) |
| Observability (Prometheus + loop lag) | **Production-ready** | Metrics token required in prod |
| Local agent security (gate 2, env scrub, no-shell) | **Production-ready** | None; strong defence-in-depth |
| Credential storage (Windows Cred Manager) | **Production-ready** | keyring fallback to env on headless |
| Voice stack (Vosk + SAPI, Porcupine/Whisper seams) | **Functional (Windows)** | Doc says porcupine/whisper; real default is Vosk/SAPI — align docs |
| Specialist agents (10 roster) | **Stubbed** | 4/10 pure placeholders; SHURI/NATASHA integrations absent |
| LLM provider | **Not connected** | `EchoLLM` only; no OpenAI/Anthropic/local model wired |
| Web search (SHURI) | **Not connected** | `# SEAM` — returns "no findings" |
| Gmail / Calendar (NATASHA) | **Not connected** | `# SEAM` — no Google OAuth |
| GUI automation (keyboard/mouse/screen) | **Declared, unimplemented** | Validator recognises; executor has no handler |
| CI/CD | **Production-ready** | 4 jobs: tests+pg, pip-audit, gitleaks, docker+trivy |
| Test coverage | **Strong for platform** | 144 test fns; heavy on security/storage/auth. Capability stubs lightly testable by nature |
| DB migrations | **Missing** | No Alembic; add before schema changes in prod |

## Conclusions & recommendations

1. **The platform is shippable; the brain is not.** Everything that makes an assistant *safe* is implemented and tested. Everything that makes it *useful beyond git/app-launch/memory* is a documented seam. Prioritise wiring one `LLMProvider` (`core/llm.py`) — that single integration lights up VISION, QUICKSILVER, NICK FURY, DR STRANGE and gives SHURI synthesis.
2. **Close the capability seams in dependency order:** LLM provider → web search (SHURI) → Google OAuth (NATASHA Gmail/Calendar, also fixes 2 of 4 scheduled jobs). Each has a clear seam marker to implement against.
3. **Add Alembic** before any non-additive schema change; `create_all` is fine for first install only.
4. **Reconcile `docs/PROVIDERS.md` with the code**: the real default voice stack is Vosk + Windows SAPI, with Porcupine/faster-whisper as the `voice-alt` extra. Misleading docs will cost deployment time.
5. **Either implement or remove GUI automation**: the validator advertises keyboard/mouse/screen but the executor can't run them. Leaving a half-exposed capability is a footgun.
6. **For hardened prod, add an external audit anchor** (ship the JSON log stream to write-once storage) to defeat newest-entry truncation — the code already documents this as the intended path.

*Content from source docstrings was rephrased/summarised; all technical claims cite the files read above. No source files were modified during this investigation.*
