# JARVIS — Production Plan

A single document that captures **what JARVIS is**, **where it stands today**, and the
**ordered plan to take it to production**. Read this before building; it is the map the
rest of the docs fill in.

> Status legend: ✅ done · 🟡 partial / seam in place · ⛔ not started

---

## 1. The product in one paragraph

JARVIS is a **voice-first desktop assistant**. A small agent on your PC listens locally for
"Hey JARVIS", converts speech to text, and sends **only text** to a backend. The backend runs a
7-stage **supervisor** that routes the request to a themed specialist agent (SHURI research, TONY
engineering, NATASHA comms, WONG memory, FRIDAY computer control, and others), decides what — if
anything — may run on your PC, and speaks the answer back in that agent's own synthetic voice.
Anything risky is held in an **approval queue** and only runs after a human approves it on a
dashboard. Two independent security gates (a backend policy engine and a local validator on the
PC) mean no single compromised component can act on its own.

## 2. Why it is built this way (design pillars)

1. **Separation of duties.** Proposing, approving, and executing are three different identities.
   An agent proposes; only `admin` approves; only a registered `device` executes. Nothing holds
   two at once.
2. **Two independent gates.** The backend policy engine (gate 1) and the on-PC validator (gate 2)
   each classify every action. A human grant satisfies "needs approval" but can **never** unlock a
   hard block (destructive command, out-of-allow-list path, malformed request). The local gate can
   refuse something the backend allowed; it can never loosen a decision.
3. **Audio stays local.** Microphone audio never leaves the PC. Only text crosses the network.
4. **Honesty rule.** An agent never claims work it did not do. Unconnected capabilities say so out
   loud (SHURI: "web search isn't connected yet"), marked with `# SEAM` in the code.
5. **Everything replaceable.** Wake word, STT, TTS, LLM, Router, and Scheduler all sit behind
   Protocols. Swapping a dev stub for a production engine is configuration + an adapter, never a
   supervisor rewrite.
6. **Fail-closed in prod.** With `JARVIS_ENV=prod`, the server refuses to start on a weak/missing
   secret, SQLite, a missing Redis, or a non-https public origin.

## 3. System shape

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

Three installable packages:

| Package | Role |
|---|---|
| `jarvis-shared` | Contracts and domain models used by both sides: agent roster, `ToolRequest`/risk types, the shell-command classifier, supervisor stages, message schemas, logging. |
| `jarvis-backend` | FastAPI app: `api/` `core/` `agents/` `auth/` `storage/` `web/` plus cache, events, jobs, metrics, middleware, rate limiting. |
| `jarvis-local-agent` | Windows runtime: wake word, STT/TTS, overlay, tray, local validator, executor, credential store. |

## 4. The 7-stage request lifecycle

Every `/assist` call runs through — and records a trace of — seven explicit stages, so nothing
collapses into one uncontrolled LLM call:

```
OBSERVE  ingest utterance + conversation context + relevant memories
PLAN     route to an agent (fall back to JARVIS if that agent is disabled)
PROPOSE  the agent returns speech + optional ToolRequests
APPROVE  policy gate: auto-allow / hold for human approval / hard-block
EXECUTE  allowed requests dispatched to the LOCAL runtime (the security boundary)
VERIFY   sanity-check the outcome
REPORT   compose spoken reply, persist the turn, publish live presence updates
```

The supervisor **only decides**; it never executes tools itself.

## 5. Current state (what works vs what is a seam)

| Capability | State | Notes |
|---|---|---|
| Local wake word + offline STT + per-agent TTS voices | 🟡 | Dev engines work with zero deps. The **real on-device stack** (`jarvis-voice`) is **Vosk + Windows SAPI** — functional on Windows today. Porcupine / faster-whisper are the alternative (`voice-alt`) extra. |
| Routing, delegation, 7-stage supervisor trace | ✅ | Deterministic `KeywordRouter`; a `LangGraphRouter` can replace it via the `Router` Protocol. |
| Real command execution on the PC (allow-listed + re-validated) | ✅ | Terminal/filesystem/application through two gates. |
| Approvals (human grants, device executes, exactly once) | ✅ | Atomic claim; grants expire; separation of duties enforced. |
| Long-term memory ("remember that…"), encrypted at rest | ✅ | WONG; field-level Fernet encryption. |
| Dashboard (voice console, agents, memory, approvals, health, audit) | ✅ | Calm "AI-OS" UI, strict CSP, no token in web storage. |
| Web research (SHURI) | ⛔ | SEAM: wire a web-search tool + LLM synthesis. |
| Gmail / Calendar (NATASHA) | ⛔ | SEAM: Google OAuth; reads SAFE, sends RESTRICTED→approval. |
| LLM reasoning (VISION, DR. STRANGE, QUICKSILVER, NICK FURY) | ⛔ | SEAM: inject a configured `LLMProvider`. |
| GUI automation (keyboard / mouse / screen) | ⛔ | Validator treats these as RESTRICTED→approval; executor not wired. |
| MFA / passkeys | ⛔ | Password-only login today — the biggest security gap for internet exposure. |
| TLS termination | ⛔ | Terminate in front (LB / Caddy / Traefik); app sets Secure cookies + HSTS in prod. |

## 6. Roadmap to production (ordered)

Each phase is independently shippable. Earlier phases are prerequisites for later ones.

### Phase 0 — Baseline hardening (confirm what exists) ✅ mostly done
- `init-secrets` → `check-config` → `serve` works; prod fail-closed checks pass.
- 2-gate security + approval lifecycle covered by tests (incl. real PostgreSQL).
- **Action:** run the full test suite + UI smoke on a clean checkout, record results.

### Phase 1 — First real intelligence (LLM provider)
- Implement `LLMProvider.complete` for one provider (Ollama local, or OpenAI/Anthropic).
- Inject into `UnconnectedAgent` personas (VISION, DR. STRANGE, etc.) and SHURI's synthesis.
- Keep the honesty rule: only speak findings the model actually produced.
- **Exit:** VISION/STRANGE answer with real reasoning; no "no model connected" fallback.

### Phase 2 — Real voice stack
- The default real stack is already wired: **Vosk** wake word + STT and **Windows SAPI** (pyttsx3)
  TTS, launched by `jarvis-voice` (`voice_local.py`). Install `jarvis-local-agent[voice]`.
- Optional upgrade: Porcupine wake word (`JARVIS_WAKE_ENGINE=porcupine` + `JARVIS_PICOVOICE_KEY`)
  and faster-whisper STT — the `voice-alt` extra. For higher-quality TTS, add Azure/ElevenLabs/Piper.
- **Constraint:** use original synthetic voices; never imitate real actors.
- **Exit:** end-to-end "Hey JARVIS → spoken answer" with no typed input (works today on Windows).

### Phase 3 — External integrations (through the gate)
- SHURI: web search tool → LLM synthesis.
- NATASHA: Google OAuth (minimal scopes); inbox/calendar reads SAFE, sends RESTRICTED→approval.
- **Rule:** every integration goes through propose → approve → execute. No shortcuts.
- **Exit:** a research request and a "send email" request both run the full approval path.

### Phase 4 — Auth hardening for exposure
- Add TOTP or WebAuthn MFA.
- Step-up re-auth on high-risk approvals.
- **Exit:** login requires a second factor; approving a DANGEROUS-adjacent action re-prompts.

### Phase 5 — Deploy for real
- **Add Alembic migrations.** Today `create_schema()` is `create_all` only — fine for a first
  install, unsafe for any non-additive schema change. Do this before the first prod schema change.
- Build and run the Docker Compose stack end to end (currently statically checked only).
- Terminate TLS in front; set `JARVIS_PUBLIC_ORIGIN=https://…`, `JARVIS_ALLOWED_HOSTS`.
- Move encryption keys to a KMS/Vault; ship audit log to append-only storage (defeats the known
  newest-entry truncation blind spot).
- Wire Prometheus alerts (Redis down, event-loop lag, pending approvals, auth 401 spikes).
- **Exit:** API scales to N replicas + one worker; health/readiness green; alerts firing in tests.

### Phase 6 — GUI automation (optional, highest risk) — decide first
The validator already *recognises* `keyboard`/`mouse`/`screen` as RESTRICTED→approval, but the
executor has **no handler** for them, so such a request currently falls to "control is not
implemented". This is a half-exposed capability — either implement it or remove the category.
- If implementing: wire the executors behind the existing RESTRICTED→approval classification.
- **Exit:** a GUI action is proposed, approved, executed, and audited, with a hard kill switch.

## 7. Definition of "production ready"

- [ ] All phases 0–5 complete; phase 6 only if GUI automation is in scope.
- [ ] `JARVIS_ENV=prod` starts cleanly; `check-config` reports no dev shortcuts.
- [ ] MFA enabled; approvals step-up authenticated.
- [ ] TLS in front; HSTS + Secure cookies verified; CSP has zero violations (UI smoke).
- [ ] Encryption keys in KMS/Vault; `JARVIS_DATA_KEY` backed up off-box.
- [ ] Audit log shipped to append-only storage; `verify-audit` green.
- [ ] Full test suite + real-browser smoke green in CI on every push.
- [ ] Independent security review completed (not pen-tested today).
- [ ] Dashboards + alerts live for the series listed in `DEPLOYMENT.md`.

## 8. Non-negotiable guardrails (do not regress)

- Never let a human grant override a hard block.
- Never run commands through a shell; always parse to argv and classify by exact program + flags.
- Never let the backend widen the PC's allow-lists.
- Never give an LLM raw shell/filesystem/input access — only `ToolRequest`s through both gates.
- Never log secrets or utterance text in the audit chain.
- Never let an agent claim work it did not do.

## 9. Where to go next

- `SKILL.md` — the capability/skill map (what each agent can do and how to extend it).
- `ARCHITECTURE.md` — the deep technical reference.
- `docs/SECURITY.md` — threat model + what each defence is tested by.
- `docs/DEPLOYMENT.md` — local → Postgres/Redis → Docker Compose → scaling.
- `docs/PROVIDERS.md` — swapping dev stubs for production engines.
- `CONTRIBUTING.md` — how to add an agent / tool / provider safely.
