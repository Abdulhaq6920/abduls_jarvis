# JARVIS

A voice-first desktop assistant. A small agent on your PC listens for **"Hey JARVIS"** locally,
turns your request into text, and hands it to a backend that routes it to the right specialist
(SHURI, TONY, NATASHA, ...). The answer is spoken back in that agent's own synthetic voice. Anything
risky waits for your approval on a dashboard before it runs.

```
 your PC                                      server (or the same PC)
┌──────────────────────┐   HTTPS + device    ┌─────────────────────────────────────┐
│ jarvis-local-agent   │   JWT (text only)   │ jarvis-backend  (FastAPI, N replicas)│
│  wake word  (local)  │ ──────────────────► │  supervisor ─ router ─ agents        │
│  speech→text(local)  │                     │  policy gate ─ approvals ─ audit     │
│  text→speech         │ ◄────────────────── │  memory ─ jobs ─ dashboard ─ metrics │
│  local validator ────┤  approved work      └───────┬──────────────┬──────────────┘
│  executes on the PC  │                       PostgreSQL         Redis
└──────────────────────┘                     (encrypted data)   (cache, limits, events)
```

Microphone audio never leaves your PC. Only text does.

## What it does today (and what it does not)

| Works | Stubbed, and says so out loud |
|---|---|
| Local wake word + offline speech-to-text + per-agent voices | Web research (SHURI), Gmail/Calendar (NATASHA), LLM reasoning (VISION, STRANGE, ...) |
| Routing, delegation, the 7-stage supervisor trace | GUI automation (keyboard / mouse / screen) |
| Real command execution on your PC, **only** inside allow-listed folders and after local re-validation | MFA, TLS termination (see `docs/SECURITY.md`) |
| Approvals: a human grants, a registered device executes, exactly once | |
| Long-term memory ("remember that…"), encrypted at rest | |
| Dashboard: voice console, **Agents page** (voices, enable/disable, activity, talk-to-agent), memory, approvals, system health, audit | |

Agents never claim work they did not do: with no search connected, SHURI says so.

## Run it (local, no Docker)

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e jarvis-shared -e "jarvis-backend[dev]" -e jarvis-local-agent
python -m jarvis_backend create-admin          # your dashboard login (password prompt is hidden)
python -m jarvis_backend serve                 # open http://localhost:8077
```

Then, to talk to it: register your PC under **System → Local agents**, store the printed secret
with `python -m jarvis_local_agent.credentials set`, copy `jarvis-local-agent/.env.example` to
`.env` (set `JARVIS_ALLOWED_PATHS`), and run `jarvis-voice`.

## Layout

```
jarvis-shared/      contracts, command classifier, logging   (used by both sides)
jarvis-backend/     api/ core/ agents/ auth/ storage/ web/    (+ cache, events, jobs, metrics)
jarvis-local-agent/ wake word, STT/TTS, overlay, validator, executor, tray
docker/ docker-compose.yml   production-style stack
docs/   SECURITY.md  DEPLOYMENT.md
tests/  unit/ integration/   (210 tests, incl. real PostgreSQL)
scripts/ ui_smoke.py (real-browser check), make_docker_secrets.py
```

## Configuration and secrets

* Settings come from `JARVIS_*` environment variables, `/run/secrets` files, then a git-ignored `.env`.
* `python -m jarvis_backend init-secrets` generates every secret into `.env` and prints none.
* `python -m jarvis_backend check-config` validates it. In `JARVIS_ENV=prod` the server **refuses to
  start** on a weak, missing or placeholder secret, SQLite, missing Redis, or a non-https origin.
* Back up `JARVIS_DATA_KEY`. Without it, encrypted conversations and memories are unrecoverable.

## Docs

* [`docs/SECURITY.md`](docs/SECURITY.md): threat model, what each defence is tested by, and the gaps.
* [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md): local, Postgres + Redis, Docker Compose, scaling, alerts.
