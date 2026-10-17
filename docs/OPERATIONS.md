# JARVIS — Operations Runbook

Day-2 operations: health, metrics, backups, key rotation, incident response. For first-time
deployment see [`DEPLOYMENT.md`](DEPLOYMENT.md); for the threat model see [`SECURITY.md`](SECURITY.md).

---

## Health & readiness

| Endpoint | Auth | Meaning |
|---|---|---|
| `GET /healthz` | none | Liveness — the process is up. Always cheap, no dependencies. |
| `GET /readyz` | none | Readiness — DB reachable (503 if not). Redis down degrades but stays **ready**. Load balancers poll this. |
| `GET /api/system/status` | admin | Rich runtime state: version, env, DB/cache ok, scheduler running, agent count, pending approvals, config findings. |
| `GET /api/runtime` | admin | Last local-agent heartbeat; `connected` false if older than 45s. |
| `GET /metrics` | metrics bearer token | Prometheus exposition. **Not** a user JWT — uses `JARVIS_METRICS_TOKEN`. Block at the proxy; scrape on the internal network. |

Notes from the code:
- `/readyz` returns `database` + `redis` status; `ready` is driven by the DB alone (Redis is an
  accelerator, so an outage degrades rather than fails readiness).
- Interactive API docs (`/api/docs`, `/api/openapi.json`) are **disabled in prod** by the app
  factory — expected, not a bug.
- There is deliberately **no CORS middleware**: the dashboard is same-origin only.

## Metrics worth alerting on

| Series | Alert when | Means |
|---|---|---|
| `jarvis_cache_fallback_active` | `== 1` | Redis is down; running on in-process fallback. |
| `jarvis_event_loop_lag_seconds` | `> 0.25` | Something is blocking the event loop. |
| `jarvis_process_rss_bytes` | trending up | Possible leak. |
| `jarvis_db_pool_checked_out` | near pool size | DB pool exhaustion; tune `JARVIS_DB_POOL_SIZE` or add PgBouncer. |
| `jarvis_pending_approvals` | `> 0` for long | Approvals piling up unactioned. |
| `jarvis_rate_limited_total` | spike | Brute force or abuse. |
| `jarvis_auth_events_total{event="login_401"}` | spike | Credential stuffing. |

All metric names verified against `observability.py`. Full series: `jarvis_http_requests_total`,
`jarvis_http_request_seconds`, `jarvis_http_in_flight`, `jarvis_assist_seconds`,
`jarvis_auth_events_total`, `jarvis_rate_limited_total`, `jarvis_ws_connections`,
`jarvis_event_loop_lag_seconds`, `jarvis_process_rss_bytes`, `jarvis_process_cpu_percent`,
`jarvis_process_threads`, `jarvis_process_open_files`, `jarvis_uptime_seconds`,
`jarvis_cache_hits`, `jarvis_cache_misses`, `jarvis_cache_errors`, `jarvis_cache_fallback_active`,
`jarvis_cache_local_items`, `jarvis_db_pool_checked_out`, `jarvis_pending_approvals`. Labels are
never user input, so series stay bounded.

## Logs

- JSON to stdout when `JARVIS_LOG_FORMAT=json`; every line carries `request_id` and `principal`.
- Follow one action end to end by its `X-Request-ID`.
- A 500 returns only an opaque `request_id` to the client; the detail is server-side only.
- The log scrubber redacts JWTs, `Bearer …`, `password=`, and credentials inside DB/Redis URLs.

## Audit trail

- Append-only, **hash-chained**, tamper-evident. Stores **no utterance text**.
- Verify integrity: `GET /api/audit/verify` (admin) or `python -m jarvis_backend verify-audit`.
  A healthy chain reports `CHAIN VERIFIED`.
- Watch for `refresh_reuse_detected` → treat as a credential leak (see Incident response).
- For real assurance beyond tamper-*evidence*, ship logs to append-only external storage.

## Backups

1. **`JARVIS_DATA_KEY` is the crown jewel.** Back it up **separately from the database**. Without it,
   encrypted conversations/memories/approvals/job output are unrecoverable.
2. Back up the database (PostgreSQL in prod). The data is ciphertext at rest, so a stolen backup is
   useless without the data key — which is exactly why the key must live elsewhere.
3. Keep `JARVIS_JWT_SECRET` recoverable too — losing it signs everyone out (not catastrophic, but
   disruptive).

## Key & secret rotation

**JWT signing secret** (`JARVIS_JWT_SECRET`):
- Set the new secret; move the old one into `JARVIS_JWT_PREVIOUS_SECRETS` briefly so existing tokens
  still verify. Remove the old one after the access-token lifetime (15 min) has passed.

**Data encryption key** (`JARVIS_DATA_KEY`, comma-separated, newest first):
- Prepend a new Fernet key; **keep the old key after it** so existing ciphertext stays readable
  (`MultiFernet` tries keys in order). Only drop the old key once data has been re-encrypted.

**Metrics token** (`JARVIS_METRICS_TOKEN`): rotate and update the Prometheus scrape config.

## Scaling

- **API is stateless** — scale replicas freely; sessions/limits/events live in Postgres/Redis.
- **Exactly one `worker`** fires scheduled jobs. Set `JARVIS_RUN_SCHEDULER=false` on API replicas
  and run a single `python -m jarvis_backend worker`. Scaling the worker fires every job N times.
- **Postgres is the limit** — keep `JARVIS_DB_POOL_SIZE × replicas < max_connections`, or front with
  PgBouncer.
- **Redis is disposable** — no persistence, LRU cap; if it dies the API degrades (slower,
  per-process limits) but keeps serving.

## Device lifecycle

- Register each PC as its own device (Dashboard → System → Local agents, or
  `python -m jarvis_backend create-device`). One device per PC so you can revoke one without
  touching the others.
- Store the device secret in the Windows Credential Manager (`python -m jarvis_local_agent.credentials set`),
  not in `.env`.
- **Lost/compromised PC:** revoke the device on the dashboard. Its token dies on the next request and
  it cannot mint a new one.

## Multi-factor auth (TOTP) & step-up

MFA is **opt-in per admin**. TOTP is RFC 6238 (SHA1 / 6 digits / 30s) and works with Google
Authenticator, Authy, or Microsoft Authenticator.

**Enroll:**

- Dashboard → System → Two-factor authentication → **Enable MFA**: scan the shown `otpauth://`
  URI (or type the base32 secret in manually), enter the code to confirm, then **save the ten
  one-time recovery codes** — they are shown only once.
- Or from the CLI: `python -m jarvis_backend enroll-mfa --username <name>`. It prints the URI +
  secret for manual entry, waits for a confirming code, then prints the recovery codes once.

**Recovery codes:** ten are generated at enablement, stored only as argon2 hashes (never
plaintext), and each works exactly once. Store them in your password manager, not on the same
device as the authenticator. They are shown only at enrollment — re-enroll to get a fresh set.

**Login with MFA:** a password returns an `mfa_pending` challenge (not an access token); the
dashboard then asks for a code (a recovery code also works). The code step is rate-limited like
the password step, and a TOTP replayed in the same 30s window is rejected.

**Disable:** requires the current password **and** a valid code (TOTP or recovery) — never the
password alone.

**Step-up on high-risk approvals:** when an MFA admin GRANTs a DANGEROUS approval (or RESTRICTED
if `JARVIS_APPROVAL_STEP_UP_RISK=RESTRICTED`), the dashboard prompts for a code; or pre-authorize
with the step-up endpoint and the grant is accepted for `JARVIS_STEP_UP_WINDOW_SECONDS` (default
300). An admin without MFA keeps today's behaviour (audited warning); set
`JARVIS_APPROVAL_REQUIRE_MFA=true` to require MFA for high-risk grants.

**Lost authenticator runbook:**

1. Sign in with a **recovery code** at the code prompt (it counts as the second factor).
2. In the dashboard, **Disable MFA** (password + a remaining recovery code), then **Enable MFA**
   again with the new device to get a fresh secret and new recovery codes.
3. If no recovery code remains, an operator with database access can clear the user's MFA columns
   (`mfa_enabled=false`, `mfa_secret=NULL`, `mfa_recovery_hashes=NULL`) so the user can re-enroll;
   or disable then recreate the account.

**Schema note (no Alembic yet):** fresh databases get the new `users.mfa_*` and
`sessions.step_up_at` columns automatically via `create_all`. An **existing** production database
needs a one-time manual migration until Phase 5 adds Alembic, e.g.:

```sql
ALTER TABLE users ADD COLUMN mfa_secret TEXT;
ALTER TABLE users ADD COLUMN mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN mfa_last_counter INTEGER;
ALTER TABLE users ADD COLUMN mfa_recovery_hashes TEXT;
ALTER TABLE sessions ADD COLUMN step_up_at TIMESTAMPTZ;
```

## Incident response

| Symptom | Action |
|---|---|
| `refresh_reuse_detected` in audit | A refresh token was replayed. Change your password (signs out every session); the token family is already auto-revoked. |
| `jarvis_cache_fallback_active == 1` | Redis is down. Expect slower responses + per-process rate limits. Restore Redis; no data loss (it holds no truth). |
| Suspected lost PC | Revoke that device immediately on the dashboard. |
| Audit chain fails to verify | Someone with DB write access may have tampered. Preserve the DB, pull external log copies, investigate; the newest-entry deletion case is the known blind spot. |
| Server will not start in prod | Run `python -m jarvis_backend check-config` — prod refuses to start on weak/missing secret, SQLite, missing Redis, or non-https origin. Fix the reported finding. |

## Prod start-up gate (fail-closed)

With `JARVIS_ENV=prod`, start-up aborts on any of:
- weak, missing, or placeholder `JARVIS_JWT_SECRET` / `JARVIS_DATA_KEY`,
- SQLite as the database,
- no `JARVIS_REDIS_URL`,
- a non-`https://` `JARVIS_PUBLIC_ORIGIN`.

Run `check-config` to see exactly which shortcut is active before deploying.

## TLS

The stack serves plain HTTP on one port — **do not expose that to the internet.** Terminate TLS in
front (cloud LB / Caddy / Traefik) pointing at nginx. Prod mode then sets `Secure` cookies + HSTS
and refuses to start unless `JARVIS_PUBLIC_ORIGIN` is `https://`.
