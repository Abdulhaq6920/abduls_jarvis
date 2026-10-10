# JARVIS: Living Build Plan

> **This is a living document.** It is the single source of truth for what we are building, in what
> order, and why. It is meant to change. Section 0 explains how to change it safely.
>
> Last updated: 2026-10-10 · Owner: product owner (you) + engineering (me)

---

## 0. How to use and change this plan

| Rule | Why |
|---|---|
| **Work packages (WP) are independent units** with an ID, dependencies, acceptance criteria and a status. Any WP whose dependencies are `DONE` can be pulled forward. | Lets us reorder without re-planning everything. |
| **Re-prioritise only in section 14 (Board)**. Never renumber WPs; add new IDs instead. | Stable references in commits, tests and chat. |
| **Every scope change goes in the Change Log (section 16)** with a one-line reason. | Nothing silently disappears. |
| **Vertical slices.** After every WP the system must still start, pass tests and be usable. | A half-built feature never breaks the product. |
| **Everything risky is behind a config switch** (`JARVIS_*`). Off = honest, rule-based behaviour. | Partial delivery is always safe to deploy. |
| **Size, not dates.** S = hours, M = 1-2 days, L = several days. If a WP exceeds 2x its size, split it. | Honest estimates for unknowns. |
| **Assumptions are tagged `[A]`** and must be verified (a probe, a test, or your confirmation) before dependants start. | Stops wrong guesses becoming architecture. |
| **"Done" means verified**: lint + tests green, feature actually run, observable (logs/metrics), documented here. | See section 11 quality gates. |
| **Security is never the trade-off.** If time is short, scope shrinks, not safeguards. | Non-negotiable. |

Status legend: `TODO` · `DOING` · `DONE` · `BLOCKED` (needs input) · `DROPPED`.

---

## 1. Vision

A voice-first assistant you log into once and then simply talk to: **"Hey JARVIS, do this task."**

- It **understands intent and plans** with a real multi-agent system powered by LLMs from Hugging Face.
- It **acts**: web search, Google Workspace, Microsoft 365, creating Office files, controlling the computer (PC) and the phone (Android).
- It **works in the background** and **waits for you** when it needs permission or information, then resumes.
- It is **always present but silent**: a floating bubble on the phone, a Dynamic-Island-style pill on the PC.
- It is **secure by construction**: nothing with side effects runs without passing the policy gate, the device's own validator, and (when risky) your approval.

### Non-goals and hard platform limits
- **iOS cannot** show a floating overlay, listen in the background, or control other apps. iOS gets only a reduced companion later (Siri/Shortcuts, Live Activity, push) if wanted.
- No claim of "zero bugs". We commit to the quality gates in section 11 instead.
- No data leaves the user's accounts except to providers they configure (Hugging Face, search, Google, Microsoft).

---

## 2. Current state (snapshot 2026-10-10)

**Working and tested** (215 Python tests, 18 frontend tests, ruff/eslint/tsc clean when run alone):
FastAPI backend on PostgreSQL (`jarvis_db`), argon2 login, rotating refresh tokens, device JWTs, hash-chained audit, field encryption, strict CSP, rate limits, Prometheus metrics, health endpoints, 7-stage supervisor, keyword router, policy gate + approvals, local validator/executor, React dashboard (Overview, Agents, Memory, Approvals, System), Vosk wake word + STT, SAPI TTS, Tk overlay.

**Known gaps (evidence-based)**

| Area | Gap |
|---|---|
| Intelligence | Agents are regex scripts. VISION, QUICKSILVER, NICK FURY, DR. STRANGE answer "no language model is connected". |
| Capabilities | Only terminal, filesystem, app-launch exist. No search, Office, Gmail/Calendar (stubs), no screen/keyboard/mouse. |
| Execution model | Each request is one synchronous call. No durable background tasks. |
| Device link | Local agent polls. No push channel, no mobile client. |
| Database | `create_all` instead of migrations, no foreign keys, app uses the `postgres` superuser (password `root`). No backups. |
| Logging | JSON + scrubbing + request ids exist. Missing: separate security/access/audit files, age retention, compression, traces, alert rules. |
| Frontend | 3 tests time out on this 4 GB machine under load. No E2E in CI. No generated API types. |
| Auth | No MFA/passkeys. One role. |
| Deploy | Docker stack never run end to end. No TLS in stack, no signing/SBOM/CD, no PC installer. |
| Operations | Servers must be started by hand; no one-command runner. |

---

## 3. Architecture

```
 Web dashboard (React)      PC agent (Windows)               Android companion (Kotlin)
 login, tasks, approvals    wake word, STT/TTS               floating bubble, FGS, FCM
        |                   Dynamic-Island pill (Qt)          local validator + executor
        |                   local validator + executor                |
        +----------------+-----------+-------------------------------+
                         | HTTPS + WebSocket (JWT)
                 TLS terminator / reverse proxy
                         |
   +--------------------- Backend (FastAPI, N stateless replicas) ---------------------+
   | API gateway  ->  Task engine  ->  Agent runtime (multi-agent, LLM)               |
   |                      |               |-> LLM layer (Hugging Face router)         |
   |                      |               |-> Tool broker -> policy -> approvals      |
   |                      |               |-> Memory / retrieval                      |
   |   Device hub (WS) <--+               |-> Integrations (search, Google, MS)       |
   |   Notifier (WS, FCM)   Audit (hash chain) + security log                         |
   +--------------------------------------------------------------------------------+
                         |                                   |
                  PostgreSQL (roles, Alembic)        Redis (cache, limits, events)
                         |
        Observability: rotated JSON logs, Prometheus, OpenTelemetry, Grafana, alerts
```

**Principles**
1. The backend plans; the **device acts** and always re-validates.
2. Every side effect is a **typed tool call** (schema, risk, audit, optional approval, idempotency key).
3. Long work is a **durable task** in PostgreSQL: survives restarts, pauses for the user, resumes.
4. **Untrusted content is data, never instructions** (web, email, files, other agents' text).
5. Stateless replicas; all state in PostgreSQL/Redis.
6. Every capability has an **off switch** and an **honest degraded mode**.

---

## 4. Decision log

| ID | Decision | Status | Notes |
|---|---|---|---|
| D-001 | LLM provider = **Hugging Face Inference Providers** (OpenAI-compatible router `https://router.huggingface.co/v1`) | DECIDED | Token held in `.env` as `JARVIS_HF_TOKEN`. User pasted it in chat, so **rotate it at end of session**. |
| D-002 | LLM access via our own thin `httpx` client, not an SDK | DECIDED | Fewer dependencies, full control of retries, redaction, metrics. |
| D-003 | Model roles (fast / default / reasoning) configured by env, not hard-coded | DECIDED | Models change; ids like `openai/gpt-oss-120b:fastest` live in config. |
| D-004 | Multi-agent pattern = **router + supervisor delegation + specialist tool loops** with depth/step/token limits | DECIDED | See section 6. |
| D-005 | Side-effect tools never execute inside the LLM loop; they become `ToolRequest`s through the existing policy gate | DECIDED | Reuses the tested approval + local-validator path. |
| D-006 | Keyword router stays as the offline/failed-LLM fallback | DECIDED | Resilience. |
| D-007 | PC bubble = PySide6 (Qt 6), replacing Tk overlay | PROPOSED | Tk cannot anti-alias or animate smoothly. Verify on target PC before committing. |
| D-008 | Mobile = native Android (Kotlin) first | PROPOSED | Needs Android Studio + a device. iOS later/reduced. |
| D-009 | Schema changes only via Alembic from WP-A1 onward | PROPOSED | |
| D-010 | Web search provider | OPEN | Need choice + key (Brave / Tavily / SerpAPI / Google CSE). |
| D-011 | Wake word engine | OPEN | Keep Vosk (free, local) or Picovoice (key, better accuracy). |
| D-012 | Hosting target + domain | OPEN | Needed for WP-I1/I2. |
| D-013 | Single-owner vs multi-user | OPEN | Affects data isolation tests and schema (`owner_id`). Default: single owner, but add `owner_id` columns now so multi-user is cheap later. |

---

## 5. Work packages

Workstreams: **A** Data and platform · **B** Observability · **C** Security · **D** AI and agents · **E** Task engine · **F** Capabilities · **G** Devices · **H** Dashboard · **I** Delivery · **J** Quality.

### A. Data and platform
| ID | Work package | Deps | Size | Status | Acceptance |
|---|---|---|---|---|---|
| WP-A0 | One-command local runner (`scripts/dev/up.ps1`: start backend + dashboard, wait for readiness, print URLs) | none | S | TODO | One command; fails loudly with the reason. |
| WP-A1 | Alembic baseline + `migrate` job + migration tests (SQLite + PostgreSQL, upgrade from empty and from baseline) | none | M | TODO | `create_all` no longer used outside tests; CI migrates a fresh DB. |
| WP-A2 | Foreign keys, missing indexes, `owner_id` columns | A1 | M | TODO | Orphan rows impossible; query plans checked for hot paths. |
| WP-A3 | Dedicated DB roles (`jarvis_owner`, `jarvis_app`, `jarvis_readonly`); rotate away from `postgres`/`root` | A1 | S | TODO | App cannot run DDL; `.env` updated; docs updated. |
| WP-A4 | New tables: `tasks`, `task_steps`, `device_capabilities`, `integration_accounts` (encrypted tokens), `notifications`, `usage_ledger`, `artifacts` | A1 | M | TODO | Migrations + store classes + tests. |
| WP-A5 | Backups: scheduled encrypted `pg_dump`, restore script, restore drill test, RPO/RTO documented | A3 | M | TODO | A restore into an empty DB passes an integrity check. |
| WP-A6 | Retention jobs + user data export/delete endpoints | A4 | M | TODO | Configurable retention; export is complete; delete is verified. |

### B. Observability
| ID | Work package | Deps | Size | Status | Acceptance |
|---|---|---|---|---|---|
| WP-B1 | Logging overhaul: separate rotated files (`app`, `access`, `security`, `error`, `llm`), size + age rotation, gzip, retention per stream, owner-only permissions, startup writability check | none | M | TODO | Files rotate under load test; permissions verified. |
| WP-B2 | Secret-leak test: seed known secrets through every logger/exception path and assert none reach any file or the console | B1 | S | TODO | Test fails if a secret leaks. |
| WP-B3 | Correlation id propagated PC/phone -> backend -> LLM -> tool -> device | B1 | M | TODO | One id traces an action across all logs. |
| WP-B4 | LLM/agent/tool/task metrics (requests, tokens, cost, latency, errors, queue depth, device presence) | D1 | M | DOING | Visible in `/metrics` and the System page. |
| WP-B5 | OpenTelemetry traces (OTLP) with spans request -> task step -> LLM -> tool | B3 | M | TODO | Trace viewer shows a full request. |
| WP-B6 | Grafana dashboards + Prometheus alert rules as repo files | B4 | M | TODO | Alerts listed in section 8 load without errors. |
| WP-B7 | Frontend error reporter -> scrubbed backend endpoint | B1 | S | TODO | UI exceptions appear in `error.log`. |

### C. Security
| ID | Work package | Deps | Size | Status | Acceptance |
|---|---|---|---|---|---|
| WP-C1 | TOTP MFA + recovery codes | A1 | M | TODO | Login requires MFA when enabled; recovery works once. |
| WP-C2 | WebAuthn passkeys | C1 | L | TODO | Passkey-only login possible. |
| WP-C3 | Per-capability authorization scopes; authz matrix test over every endpoint | A2 | M | TODO | Test enumerates routes and asserts expected status per role. |
| WP-C4 | Device pairing (one-time code/QR) and per-device capability scopes | A4 | M | TODO | Unpaired device cannot connect. |
| WP-C5 | Prompt-injection defences (trust labels, side-effect lock after untrusted input, egress allow-list) | D4 | L | TODO | Injection corpus yields zero unapproved side effects. |
| WP-C6 | SSRF-safe fetcher | none | M | TODO | Private/loopback/link-local/metadata IPs and redirects to them are blocked. |
| WP-C7 | Supply chain: Dependabot, pip-audit, npm audit, trivy, gitleaks, SBOM, image signing | I1 | M | TODO | CI blocks on high/critical fixable findings. |
| WP-C8 | Secrets management for prod + rotation runbook (JWT, data key, HF token, DB roles) | I2 | M | TODO | Rotation drill documented and tested. |
| WP-C9 | Security headers/CSP review for new UI; login anomaly alerts | H1 | S | TODO | |

### D. AI and agents (see section 6)
| ID | Work package | Deps | Size | Status | Acceptance |
|---|---|---|---|---|---|
| WP-D1 | **LLM layer**: typed messages/tools, Hugging Face provider (retries, jitter, circuit breaker, timeout, concurrency cap, usage accounting, secret-safe errors), null provider, daily token budget | none | L | DOING | Mocked-provider tests green; one live call verified; no secret in logs. |
| WP-D2 | **Agent runtime**: per-agent persona/model/tool allow-list, bounded tool-calling loop, honest fallback to rule-based agents on any LLM failure | D1 | L | DOING | Loop respects step/token/time limits; failure never crashes a request. |
| WP-D3 | **Multi-agent orchestration**: supervisor `delegate` tool (depth <= 2, cycle guard), LLM router with keyword fallback | D2 | L | DOING | A multi-hop request (JARVIS -> specialist) works and is traced. |
| WP-D4 | Safety layer: untrusted-content wrapping, arg schema validation, side-effect tools only via policy gate, output length caps | D2 | M | DOING | Unit tests for each guard. |
| WP-D5 | Memory v2: relevant-fact retrieval into context, user-controlled, encrypted | D2, A1 | M | TODO | Recall quality test on a fixture set. |
| WP-D6 | Eval harness: golden tasks (recorded provider), tool-selection accuracy, injection corpus; CI gate | D3 | M | TODO | `pytest -m eval` reports pass-rate; regressions fail CI. |
| WP-D7 | Model selection probe + documented model table (fast/default/reasoning), fallback chain across models | D1 | S | DOING | Table in section 6 filled from a real probe. |
| WP-D8 | Planner-executor for multi-step goals (DR. STRANGE plans, task engine executes steps) | E1, D3 | L | TODO | A 3-step goal runs as a task with visible steps. |
| WP-D9 | Streaming responses (SSE/WS) to UI and bubble | D1 | M | TODO | First tokens appear <1 s after provider starts. |

### E. Task engine
| ID | Work package | Deps | Size | Status | Acceptance |
|---|---|---|---|---|---|
| WP-E1 | Task + step tables, worker loop with `FOR UPDATE SKIP LOCKED`, states, expiry, cancel | A4 | L | TODO | Two workers never run one task twice. |
| WP-E2 | `waiting_approval` / `waiting_input` pause + resume, notifications | E1 | M | TODO | Answer after a backend restart resumes the task. |
| WP-E3 | Idempotency keys on tool calls | E1 | S | TODO | Retried step does not repeat a side effect. |
| WP-E4 | Task event stream with replay on reconnect | E1 | M | TODO | Client reconnect shows missed events. |
| WP-E5 | Scheduled/recurring tasks (QUICKSILVER) | E1 | M | TODO | Cron-style task survives restarts. |

### F. Capabilities
| ID | Work package | Deps | Size | Status | Acceptance |
|---|---|---|---|---|---|
| WP-F1 | Web search tool (provider adapter) + safe fetch + extraction | C6, D-010 | M | BLOCKED | Needs search provider/key. SHURI cites sources. |
| WP-F2 | Local Office tools: create/edit Word, Excel, PowerPoint (python-docx, openpyxl, python-pptx) within allowed folders; approval on overwrite | D2 | M | TODO | Generated files open in Office; path-escape test passes. |
| WP-F3 | PC tools hardening: files, apps, terminal allow-list, screenshot; keyboard/mouse always approval | D2 | L | TODO | Each tool has a deny test. |
| WP-F4 | Google Workspace connector (Gmail, Calendar, Drive) OAuth+PKCE, encrypted tokens, revoke | A4 | L | BLOCKED | Needs Google Cloud OAuth client. |
| WP-F5 | Microsoft 365 connector (Outlook, Calendar, OneDrive, Word/Excel online) | A4 | L | BLOCKED | Needs Entra app registration. |
| WP-F6 | "Connected accounts" UI with scopes and disconnect | F4/F5, H1 | M | TODO | |
| WP-F7 | Mobile intents catalogue (call, message, navigate, open app) | G5 | M | TODO | |

### G. Devices
| ID | Work package | Deps | Size | Status | Acceptance |
|---|---|---|---|---|---|
| WP-G1 | Device WebSocket hub, capability registry, offline queue | C4, E1 | L | TODO | Tool routed to the right online device. |
| WP-G2 | PC agent switches from polling to the WS channel | G1 | M | TODO | Latency measured before/after. |
| WP-G3 | **Dynamic-Island pill** (PySide6): idle/listening/thinking/speaking/approval/notification/error, click-through when idle, multi-monitor, DPI, reduced motion | G2, D-007 | L | TODO | Screenshot suite for every state; approval can be given in-pill. |
| WP-G4 | Tray, autostart, single instance, crash recovery, local rotated logs | G3 | M | TODO | Survives sign-out/sign-in and crash. |
| WP-G5 | Android app: pairing, foreground service, FCM, overlay bubble | C4, G1 | L | BLOCKED | Needs Android Studio + device. |
| WP-G6 | Android wake word + speech, local validator, intents | G5 | L | BLOCKED | |
| WP-G7 | Android AccessibilityService actions (opt-in, policy-sensitive) | G6 | L | TODO | |
| WP-G8 | Windows installer, code signing, auto-update | G4 | L | TODO | Clean install/uninstall on a fresh VM. |

### H. Dashboard
| ID | Work package | Deps | Size | Status | Acceptance |
|---|---|---|---|---|---|
| WP-H0 | Fix flaky tests (timeout, `userEvent.setup({delay:null})`) | none | S | TODO | 18/18 pass under load. |
| WP-H1 | Generated TS API types from OpenAPI; CI drift check | none | M | TODO | Changing a Pydantic model without regenerating fails CI. |
| WP-H2 | AI status panel on System page (provider, models, tokens today, limit, last error) | D1 | S | TODO | |
| WP-H3 | Chat/command UX: streaming reply, agent chain with per-step timing, tool-call cards | D3, D9 | M | TODO | |
| WP-H4 | Tasks page: live timeline, pause/resume/cancel, inline approval | E4 | L | TODO | |
| WP-H5 | Devices page: pairing QR, capabilities, presence | G1 | M | TODO | |
| WP-H6 | Usage/cost + logs/security-events viewer | B4 | M | TODO | |
| WP-H7 | Onboarding, empty states, command palette, notification centre | H4 | M | TODO | |
| WP-H8 | axe accessibility checks + visual regression in CI | H0 | M | TODO | |

### I. Delivery
| ID | Work package | Deps | Size | Status | Acceptance |
|---|---|---|---|---|---|
| WP-I1 | CI: python, web, image build, scans, E2E; cache; required checks | H0 | M | TODO | Green pipeline on a clean clone. |
| WP-I2 | Docker stack built and run end to end; TLS (Caddy/Traefik) in the stack | I1 | M | BLOCKED | Needs engine running + domain for real TLS. |
| WP-I3 | CD: tagged releases, signed images, rollback | I1 | L | TODO | |
| WP-I4 | Runbooks: deploy, rollback, restore, key rotation, incident response | I2 | M | TODO | A new operator succeeds using only the docs. |
| WP-I5 | Load test (k6/Locust) with targets; chaos drills (Redis/DB/LLM down) | I2 | M | TODO | Targets in section 9 met. |

### J. Quality
| ID | Work package | Deps | Size | Status | Acceptance |
|---|---|---|---|---|---|
| WP-J1 | Playwright E2E in CI (login -> ask -> approve -> result; mobile viewport) | A0 | M | TODO | |
| WP-J2 | Authz matrix + injection + SSRF + log-leak security suite | C3,C5,C6,B2 | M | TODO | |
| WP-J3 | Pre-release checklist and bug triage template | none | S | TODO | |

---

## 6. The multi-agent system (design)

### 6.1 Roles
| Agent | Job | Model role | Tools it may call |
|---|---|---|---|
| **Router** | Pick the best agent for an utterance (JSON output) | fast | none |
| **JARVIS** | Supervisor: converse, decide, **delegate** to specialists, summarise | default | `delegate`, `recall`, `remember` |
| **FRIDAY** | Computer operator | default | `open_app`, `list_dir`, `read_file`, `write_file` (via gate) |
| **TONY** | Developer / engineering | default | `run_command`, file tools (via gate) |
| **SHURI** | Research and web investigation | default | `web_search`, `fetch_page` (WP-F1, until then states it cannot browse) |
| **NATASHA** | Email, calendar, messaging | default | Gmail/Calendar tools (WP-F4/F5), `send_email` always approval |
| **WONG** | Knowledge and long-term memory | fast | `remember`, `recall` |
| **VISION** | Analysis, reasoning | reasoning | `recall` |
| **DR. STRANGE** | Strategy and multi-step planning | reasoning | `recall`, later `create_task_plan` |
| **QUICKSILVER** | Scheduling and background jobs | fast | later `schedule_task` |
| **NICK FURY** | Security reviewer | reasoning | **advisory only**: can recommend "ask the user" but can never loosen policy |

### 6.2 Orchestration patterns (all bounded)
1. **Route -> specialist**: router (LLM, fallback keyword) picks the agent; explicit "ask X to ..." always wins.
2. **Supervisor delegation**: JARVIS may call `delegate(agent, task)`; max depth 2, no repeated (agent, task) pair, each delegate gets its own step/token budget slice.
3. **Specialist tool loop**: call model -> if tool calls, run internal tools (recall/remember/delegate) or convert device tools to `ToolRequest`s -> feed results back -> repeat until a final answer or the limits are hit.
4. **Planner-executor** (WP-D8): DR. STRANGE writes a step list; the task engine executes steps and re-plans on failure.
5. **Verify** (later): a cheap check that the final answer addresses the request.

### 6.3 Hard limits (configurable)
`max_steps` per agent loop (default 5) · `max_tokens` per call (default 800) · delegation depth 2 · wall-clock per request (default 40 s) · daily token budget (default 300k) · concurrency cap on provider calls · per-user `assist` rate limit (existing).

### 6.4 Guardrails
- Tool arguments are validated against JSON schemas before use; unknown tools are rejected.
- **Side-effect tools never run inside the loop.** They become `ToolRequest`s -> backend policy -> approval -> device validator.
- Everything not typed by the user (memories, delegate replies, future web/email text) is wrapped as **untrusted data** with an explicit instruction that it may not change the agent's task or rules.
- System prompt states what the agent can and cannot do and forbids claiming unperformed actions.
- Secrets are never placed in prompts; prompts and completions are **not logged** (only model, tokens, latency, outcome).
- LLM failure -> the rule-based agent answers and says the language model is unavailable.

### 6.5 Model table (filled from the live probe, WP-D7)
| Role | Model id (env) | Why | Verified |
|---|---|---|---|
| fast | _pending probe_ | cheap, low latency, JSON-capable | no |
| default | `openai/gpt-oss-120b:fastest` `[A]` | tool-calling and reasoning documented by Hugging Face | no |
| reasoning | `openai/gpt-oss-120b:fastest` `[A]` | adjustable reasoning effort | no |

---

## 7. Security model

STRIDE summary

| Threat | Controls (existing = E, planned = P) |
|---|---|
| Spoofing | argon2id (E), refresh rotation + replay detection (E), device secrets (E), MFA/passkeys (P: C1, C2), pairing (P: C4) |
| Tampering | Policy gate + device validator (E), hash-chained audit (E), DB roles without DDL (P: A3), signed images (P: C7) |
| Repudiation | Audit of every decision (E), correlation ids end to end (P: B3) |
| Information disclosure | Field encryption (E), log scrubbing (E), CSP (E), TLS verify-full to DB (E, config), secret-leak test (P: B2), SSRF block (P: C6), no prompts in logs (P: D1) |
| Denial of service | Body caps, rate limits (E), token/step budgets (P: D1/D2), queue limits (P: E1) |
| Elevation of privilege | Allow-lists (E), approvals (E), scopes (P: C3), no raw shell for LLM (E by D-005) |

**Agent-specific**: prompt injection, confused deputy, data exfiltration, runaway spend, tool misuse. Mitigations in 6.4 and WP-C5. Hard kill switch `JARVIS_LLM_ENABLED=false` (or empty token) disables all model use instantly.

**Secrets handling**: `.env` is git-ignored and excluded from images; production uses file-based secrets; the Hugging Face token is a `SecretStr` and never printed. **The token pasted in chat on 2026-10-10 must be rotated.**

---

## 8. Observability specification

**Log streams** (all JSON lines, UTC, correlation id, principal, no secrets):
| File | Content | Rotation | Retention |
|---|---|---|---|
| `app.log` | application events | 10 MB x gzip | 30 days |
| `access.log` | one line per HTTP request | 10 MB x gzip | 14 days |
| `security.log` | auth, MFA, approvals, policy denials, pairing, key use | 10 MB x gzip | 180 days |
| `llm.log` | model, tokens, latency, outcome, agent (never content) | 10 MB x gzip | 30 days |
| `error.log` | WARNING+ with stack traces (scrubbed) | 10 MB x gzip | 60 days |

Files are owner-only, rotation is size **and** age, startup fails loudly if the directory is not writable.

**Metrics** (Prometheus): HTTP, assist latency, auth, rate limits, cache, DB pool (existing) + `jarvis_llm_requests_total{model,outcome}`, `jarvis_llm_tokens_total{model,kind}`, `jarvis_llm_seconds{model}`, `jarvis_llm_budget_used_ratio`, `jarvis_agent_steps_total{agent}`, `jarvis_tool_requests_total{category,verdict}`, `jarvis_tasks{state}`, `jarvis_devices_online`.

**Alerts**: 5xx rate, p95 latency, LLM error rate, LLM budget >80%, pending approvals aging, queue depth, DB pool saturation, audit chain broken, disk >85%, device offline.

---

## 9. Data model (target)

Existing: `users, sessions, devices, audit_log, approvals, turns, memories, agent_settings, job_runs, kv`.
Planned (WP-A4): `tasks(id, owner_id, goal, state, agent_id, created_at, updated_at, expires_at)`, `task_steps(id, task_id, idx, kind, status, input, output, idempotency_key)`, `device_capabilities(device_id, capability)`, `integration_accounts(id, owner_id, provider, scopes, token_enc, refresh_enc, expires_at)`, `notifications(id, owner_id, kind, payload, read_at)`, `usage_ledger(day, owner_id, model, prompt_tokens, completion_tokens, cost_micro)`, `artifacts(id, task_id, path, mime, sha256)`.
Rules: FKs everywhere, `UTCDateTime`, encrypted columns for content, indexes for every query path, retention jobs.

**Targets** (for WP-I5): login p95 < 500 ms, `assist` rule-based p95 < 300 ms, LLM-backed p95 bounded by provider + 1 s overhead, 100 concurrent dashboards, 50 devices.

---

## 10. Testing strategy

Unit (pure logic) · Integration (real PostgreSQL, migrations) · Contract (OpenAPI types, device protocol) · Security (authz matrix, injection, SSRF, log-leak) · E2E (Playwright, desktop + mobile) · AI evals (recorded provider) · Resilience (Redis/DB/LLM down, restart mid-task) · Load · Accessibility + visual.
The LLM layer is tested with `httpx.MockTransport` (no network in CI) plus an opt-in live smoke script.

---

## 11. Quality gates (definition of done for every WP)

1. ruff, ESLint, `tsc --strict` clean.
2. Python + frontend tests green; new code has tests; no network in unit tests.
3. Security tests green where relevant.
4. Migrations apply on empty DB and on previous release.
5. Docker image builds, non-root, read-only FS, scans clean of fixable high/critical CVEs.
6. Feature **actually run** (browser/device/CLI) and the result recorded in the Change Log.
7. Logs and metrics for the feature exist and contain no secret or prompt content.
8. This plan updated (status, decisions, change log).

---

## 12. Deployment and operations

Environments: **dev** (local, SQLite or Postgres) · **staging** (compose, real TLS if a domain exists) · **prod**.
Flow: PR -> CI -> image build/scan/sign -> tag -> deploy -> `migrate` job -> API/worker rollout -> smoke test -> rollback tag if red.
Runbooks to write (WP-I4): first deploy, upgrade, rollback, backup/restore drill, key rotation (JWT, data key, DB roles, HF token), incident response, "LLM provider down", "disk full".

---

## 13. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Prompt injection via web/email/files | Unwanted actions | Trust labels, side-effect lock, approvals, egress allow-list (C5) |
| Runaway LLM cost | Bill | Token budgets, step caps, alerts, kill switch |
| Provider outage/rate limits | Agents dumb | Circuit breaker, model fallback chain, rule-based fallback (D-006) |
| Small model mis-calls tools | Wrong actions | Schema validation, approvals, evals (D6), default to bigger model |
| 4 GB dev machine | Slow tests/flaky timeouts | Lower concurrency, longer timeouts, run heavy suites alone (H0) |
| Android accessibility policy | Store rejection | Opt-in, minimal scope, sideload option |
| Windows overlay quirks (DPI, full-screen apps) | Poor UX | Test matrix, fallback to toast |
| Token exposure (pasted in chat) | Account misuse | Rotate now; fine-grained scope; budget alerts |
| Superuser DB credentials | Data loss | A3 roles |

---

## 14. Board (re-prioritise here)

**NOW (in progress):** WP-D1 LLM layer · WP-D2 agent runtime · WP-D3 orchestration · WP-D4 safety · WP-D7 model probe · WP-B4 LLM metrics
**NEXT:** WP-H0 flaky tests · WP-A0 runner · WP-H2 AI status panel · WP-B1/B2 logging overhaul · WP-A1 Alembic · WP-A3 DB roles
**THEN:** WP-E1 task engine · WP-F2 Office tools · WP-F3 PC tools · WP-D5 memory v2 · WP-C1 MFA
**LATER / BLOCKED ON YOU:** WP-F1 (search key) · WP-F4/F5 (Google/Microsoft apps) · WP-G5+ (Android) · WP-I2/I3 (domain, hosting)

---

## 15. Inputs needed from the product owner

| # | Input | Unblocks |
|---|---|---|
| 1 | Rotate the Hugging Face token; confirm it has "Make calls to Inference Providers" | D1 (safety) |
| 2 | Web search provider + key (D-010) | F1 |
| 3 | Google Cloud OAuth client (steps provided when needed) | F4 |
| 4 | Microsoft Entra app registration | F5 |
| 5 | Windows only? Wake word choice (D-011) | G3/G4 |
| 6 | Android phone + Android Studio? | G5-G7 |
| 7 | Hosting target + domain (D-012) | I2/I3 |
| 8 | Single owner or multi-user (D-013) | A2/C3 |
| 9 | OK to create DB roles and change the `root` password | A3 |
| 10 | Retention rules for conversations/logs | A6/B1 |

---

## 16. Change log

| Date | Change | Reason |
|---|---|---|
| 2026-10-10 | Plan created as `docs/PLAN.md` (replaces the chat-only plan). Marked WP-D1..D4, D7, B4 as DOING. | User asked for a flexible, written end-to-end plan before building. |
| 2026-10-10 | D-001: Hugging Face chosen as LLM provider; token stored in `.env`. | User supplied key. |
