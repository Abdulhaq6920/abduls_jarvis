# jarvis-backend

The orchestration backend. Receives post-STT text from the local runtime, routes it
through the supervisor, and returns spoken text + the responding agent (whose voice
the runtime uses). The backend never streams audio and never runs local tools itself.

## Supervisor stages (never collapsed)

```
OBSERVE → PLAN → PROPOSE → APPROVE → EXECUTE → VERIFY → REPORT
```

Each `/assist` response carries the full stage trace for observability + audit.

## Modules

| Module | Role |
| --- | --- |
| `supervisor.py` | The six+1 stage controller. |
| `router.py` | `Router` Protocol + `KeywordRouter` (LangGraph-replaceable). |
| `agents_runtime.py` | Agent execution; produces speech + gated `ToolRequest`s. |
| `registry.py` | Agent + voice-profile registries (additive). |
| `policy.py` | `PolicyEngine` — SAFE/RESTRICTED/DANGEROUS classification. |
| `approvals.py` | Approval queue (NICK FURY gate). |
| `scheduler.py` | APScheduler background jobs (QUICKSILVER). |
| `audit.py` | Append-only audit log. |
| `app.py` | FastAPI: REST + `/ws/presence` + dashboard. |
| `dashboard.py` | Self-contained AI-OS dashboard HTML. |

## Endpoints

```
POST /assist            run an utterance through the supervisor
GET  /voices            voice profiles (one per agent)
GET  /voices/{agent}    one profile
GET  /agents            roster
GET  /approvals         pending/all
POST /approvals/{id}    { "granted": true|false }
GET  /jobs              scheduled jobs
GET  /audit?limit=N     recent audit entries
GET  /status            health
GET  /                  dashboard
WS   /ws/presence       live presence stream
```

## Run

```powershell
python -m jarvis_backend        # http://127.0.0.1:8077
```
