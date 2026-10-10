# jarvis-backend

The server: authentication, routing, approvals, memory, scheduled jobs, the dashboard and metrics.
It receives **text** from the local agent (never audio), decides who handles it, and never runs a
tool itself. Anything that must run on your PC is handed to the local agent, which re-checks it.

## Layout

```
src/jarvis_backend/
  app.py            FastAPI factory + lifespan (start-up and graceful shutdown)
  __main__.py       CLI: serve | worker | migrate | init-secrets | check-config |
                         create-admin | create-device | verify-audit
  config.py         Settings: env vars, /run/secrets files, .env; refuses unsafe prod config
  container.py      Builds every service once (shared by the API and the worker)
  middleware.py     request id + access log + metrics, security headers, body-size cap
  crypto.py         Field-level encryption (Fernet) for sensitive columns
  cache.py          Redis cache/counters with a bounded in-process fallback
  ratelimit.py      Rate limiting on top of the cache
  events.py         Event bus (in-memory, or Redis for multiple replicas)
  observability.py  Prometheus metrics + runtime snapshot
  jobs.py           Cron job definitions + scheduler (run in exactly one process)
  maintenance.py    Daily retention / expiry
  auth/             passwords (argon2id), JWT codec, login/refresh/devices service, deps
  core/             supervisor (7 stages), router, policy engine
  agents/           roster/config registry + agent behaviour
  storage/          async SQLAlchemy models and one store per concern
  api/              one router module per concern (+ WebSocket)
  web/              the dashboard (plain ES modules, no build step, no innerHTML)
```

## Running

```powershell
python -m jarvis_backend create-admin     # first dashboard user
python -m jarvis_backend serve            # http://localhost:8077
python -m jarvis_backend worker           # the ONE scheduler (needed when serve runs with RUN_SCHEDULER=false)
python -m jarvis_backend check-config     # validate settings; prints no secrets
```

Settings are documented in the repository's `.env.example`. Interactive API docs are at
`/api/docs` in dev mode only.

## Request flow

`POST /api/assist` → **OBSERVE → PLAN → PROPOSE → APPROVE → EXECUTE → VERIFY → REPORT`, each stage
traced, audited, and published to the dashboard. APPROVE is the policy gate: safe actions go back to
the local agent, risky ones wait in the approval queue for a human, dangerous ones are blocked.
