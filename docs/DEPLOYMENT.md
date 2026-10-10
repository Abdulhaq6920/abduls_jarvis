# Deploying JARVIS

Three ways to run it, from simplest to most production-like. Each is independent.

## 1. Local development (SQLite, no Docker, no Redis)

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e jarvis-shared -e "jarvis-backend[dev]" -e jarvis-local-agent
python -m jarvis_backend create-admin          # prompts for a password (hidden)
python -m jarvis_backend serve                 # http://localhost:8077
```

Dev mode generates its own signing secret and data key into `~/.jarvis/dev_secrets.json` so the
first run needs no setup. `check-config` lists exactly which dev shortcuts are active.

## 2. PostgreSQL + Redis without Docker

Edit `.env` (copy `.env.example`, then `python -m jarvis_backend init-secrets`):

```ini
JARVIS_DATABASE_URL=postgresql+asyncpg://jarvis:PASSWORD@db-host:5432/jarvis
JARVIS_DATABASE_SSL=verify-full            # REQUIRED for any non-local database
JARVIS_DATABASE_CA_FILE=/path/to/ca.pem    # if your provider uses its own CA
JARVIS_REDIS_URL=rediss://:PASSWORD@redis-host:6380/0
```

Then `python -m jarvis_backend migrate` once, and `serve` and `worker` as separate processes.
`worker` must run as exactly one instance; it is the only process that fires scheduled jobs.

Managed providers (RDS, Azure, Neon, Supabase, Upstash) work: paste their connection string.
Both `postgres://` and `postgresql://` forms are accepted and rewritten for the async driver.

## 3. Docker Compose (API replicas + worker + Postgres + Redis + nginx)

> Not yet run end to end: Docker was unavailable where this was written. The files are checked
> by `tests/unit/test_deployment_files.py`, but expect to fix small things on first `up`.

Windows laptop walkthrough: [`DOCKER_WINDOWS.md`](DOCKER_WINDOWS.md). In short:

```powershell
python scripts/docker/make_docker_secrets.py                 # ./secrets/* (git-ignored), prints no values
copy docker\env.docker.example docker\.env.docker     # non-secret settings; edit for your host
scripts\docker\dc.ps1 up -d --build                          # low-memory profile by default
scripts\docker\dc.ps1 run --rm api create-admin              # first dashboard user
scripts\docker\dc.ps1 -Full up -d --scale api=3              # big machine: scale the API
scripts\docker\dc.ps1 logs -f api worker
```

For a real server set, in `docker\.env.docker`: `JARVIS_ENV=prod`,
`JARVIS_ALLOWED_HOSTS=jarvis.example.com` and `JARVIS_PUBLIC_ORIGIN=https://jarvis.example.com`.

Scaling notes:

* **API is stateless.** Sessions, rate limits and the event bus live in Postgres/Redis, so any
  replica can serve any request. A WebSocket sticks to one replica; Redis relays events to all.
* **Exactly one `worker`.** Scaling it makes every scheduled job fire N times.
* **Postgres is the scaling limit.** Tune `JARVIS_DB_POOL_SIZE` × replicas below
  `max_connections`, or put PgBouncer in front.
* **Redis is disposable.** It runs with no persistence and a 128 MB LRU cap. If it dies, the API
  degrades (slower, per-process rate limits) but keeps serving.

### TLS

The stack serves plain HTTP on one port. **Do not expose that to the internet.** Terminate TLS in
front of it (a cloud load balancer, Caddy, or Traefik) and point it at nginx. Production mode then
sets `Secure` cookies and HSTS, and refuses to start unless `JARVIS_PUBLIC_ORIGIN` is `https://`.

### Local agent against a remote backend

Run the voice agent on your PC, not in Docker (it needs your microphone). Point it at the
**https** URL, register the PC as a device on the dashboard, and store the secret in the Windows
Credential Manager:

```powershell
python -m jarvis_local_agent.credentials set
```

## Observability

| What | Where |
|---|---|
| Liveness / readiness | `GET /healthz` · `GET /readyz` (503 when the DB is down) |
| Metrics | `GET /metrics` with `Authorization: Bearer $JARVIS_METRICS_TOKEN` (blocked at nginx; scrape the API on the internal network) |
| Logs | JSON to stdout with `request_id` and `principal` on every line; follow one action with `X-Request-ID` |
| Runtime / memory state | Dashboard → System, or `GET /api/system/status` |
| Audit | Dashboard → System → Audit trail; `python -m jarvis_backend verify-audit` |

Series worth alerting on: `jarvis_cache_fallback_active == 1` (Redis down),
`jarvis_event_loop_lag_seconds > 0.25` (something is blocking the loop),
`jarvis_process_rss_bytes` trending up, `jarvis_db_pool_checked_out` near the pool size,
`jarvis_pending_approvals > 0` for long, and a spike in `jarvis_rate_limited_total` or
`jarvis_auth_events_total{event="login_401"}`.

## Tests

```powershell
pytest tests/unit tests/integration          # ~200 tests; includes real PostgreSQL via pgserver
python scripts/dev/ui_smoke.py http://localhost:8077   # real-browser check of the dashboard
```
