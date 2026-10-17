# JARVIS — Skill & Capability Map

What JARVIS can do, which agent does it, what it needs to run, and how to add more.
This is the "skills" reference: a precise map from a spoken request to the code that handles it.

- Product overview and roadmap → [`docs/PRODUCTION_PLAN.md`](docs/PRODUCTION_PLAN.md)
- Deep technical reference → [`ARCHITECTURE.md`](ARCHITECTURE.md)
- Threat model → [`docs/SECURITY.md`](docs/SECURITY.md)

> Skill state: ✅ live · 🟡 seam in place (adapter needed) · ⛔ not wired

---

## 1. How a skill runs

A "skill" is an agent handling a task and, when it needs to touch your machine or the outside
world, emitting a `ToolRequest` that must clear two independent security gates before anything runs.

```
"Hey JARVIS, ask TONY to check my project"
      │
      ▼  (local: wake word → speech-to-text, audio never leaves the PC)
  text only ──HTTPS──►  Supervisor
                          OBSERVE → PLAN(route to TONY) → PROPOSE(speech + ToolRequest)
                          → APPROVE(policy gate 1) → EXECUTE → VERIFY → REPORT
                                                        │
                        allowed request ──────────────►  Local agent
                                                          validator (gate 2) → executor
                                                          runs `git status` in an allowed path
                          spoken reply ◄──────────────── result reported + audited
```

Two gates, one rule: **a human grant satisfies "needs approval" but never unlocks a hard block.**

## 2. The agent roster (skills by agent)

Defined in `jarvis-shared/src/jarvis_shared/agents.py`; behaviour in
`jarvis-backend/src/jarvis_backend/agents/runtime.py`.

| Agent | Role | Core skill | State | Routing keywords |
|---|---|---|---|---|
| **JARVIS** | Supervisor / orchestrator | Routes, delegates, converses, reports status. Owns mic + wake lifecycle. | ✅ | jarvis, help, status, who |
| **FRIDAY** | Local computer operator | Open allow-listed apps; file/terminal ops via `ToolRequest`. | 🟡 | open, run, launch, type, click, file |
| **TONY** | Developer / engineering | Inspect a repo (`git status --short`) in an approved project path. | ✅ | project, code, build, git, repo, deploy, test |
| **WONG** | Memory keeper | "remember that …" stores a fact; "what do you remember about …" recalls it. Encrypted at rest. | ✅ | remember, recall, note, document |
| **SHURI** | Research / web | Investigate a topic and synthesise findings (speech only, read-only). | 🟡 (search wired; disabled by default, enable via `JARVIS_SEARCH_*`; LLM synthesis via Phase 1 provider) | research, investigate, latest, search, web |
| **NATASHA** | Communications | Read inbox / send email / calendar via Google OAuth. Sends are RESTRICTED→approval. | ⛔ (SEAM: Google OAuth) | email, gmail, inbox, calendar, reply, meeting |
| **VISION** | Analysis & reasoning | Analyse data/documents with an LLM. | ✅ (LLM wired; `EchoLLM` offline default) | analyze, explain, reason, evaluate, compare |
| **DR. STRANGE** | Strategy & planning | Multi-step plans / roadmaps with an LLM. | ✅ (LLM wired; `EchoLLM` offline default) | plan, strategy, roadmap, steps, scenario |
| **QUICKSILVER** | Background jobs | LLM-reasoned plans for recurring/background work (scheduler wiring is Phase 2). | ✅ (LLM reasoning; scheduler ⛔) | schedule, every, background, recurring, daily |
| **NICK FURY** | Security gatekeeper | Appears in the trace whenever an action is held or blocked. | ✅ | approve, permission, security, danger |

**Honesty rule:** an unconnected (or disabled) skill says so out loud instead of pretending. SHURI
with search disabled (the default `JARVIS_SEARCH_PROVIDER=none`) replies that it found nothing / is
not configured, and never fabricates a finding or URL. Enabling `JARVIS_SEARCH_*` turns on real
search + LLM synthesis. The remaining `# SEAM` markers in `runtime.py` (e.g. NATASHA's Gmail) work
the same way — connect the integration there to turn the skill on.

## 3. Tool categories (what a skill may ask the PC to do)

Every `ToolRequest` carries a `category`. The local validator
(`jarvis-local-agent/src/jarvis_local_agent/security.py`) is the authority on what each means.

| Category | Example action | Default classification | State |
|---|---|---|---|
| `terminal` | `run_command` | SAFE if read-only allow-listed; RESTRICTED if unknown/mutating; DANGEROUS blocked | ✅ |
| `filesystem` | `read_file`, `list_dir` | SAFE (read-only, inside allow-list) | ✅ |
| `filesystem` | `write_file`, `move_file` | RESTRICTED → approval | ✅ |
| `filesystem` | `delete_file` | DANGEROUS → approval (bulk/root patterns hard-blocked) | ✅ |
| `application` | `open_app` | RESTRICTED → approval; bare name only, must be on the PC's app allow-list | ✅ |
| `keyboard` / `mouse` / `screen` | GUI automation | RESTRICTED → approval | 🟡 the validator classifies these, but the executor (`tools.py`) has **no handler** — a request falls to "control is not implemented". Declared, not runnable. |
| `gmail` / `calendar` | read vs `send_email`/mutate | reads SAFE; sends/mutations RESTRICTED → approval | ⛔ (SEAM: OAuth) |
| `browser` | web fetch/search | — | 🟡 SHURI's web search is now wired via `SearchProvider` (`core/search.py`), read-only and **speech only** — no `ToolRequest`. Disabled by default; enable via `JARVIS_SEARCH_*`. |

### Command classification (the heart of terminal safety)

`jarvis-shared/src/jarvis_shared/commands.py` — commands are **never run through a shell**:

- Parsed to an argv; classified by **exact program + allow-listed flags**, not substring matching.
- Any shell metacharacter (`& | ; < > `` ` `` $ ( ) ^ %` …) → **DANGEROUS**.
- Destructive/interpreter programs (`rm`, `del`, `format`, `dd`, `reg`, `cmd`, `powershell`,
  `curl`, `wget`, …) → **DANGEROUS**.
- Unknown commands → **RESTRICTED** (need approval), never auto-SAFE.
- A non-allow-listed flag demotes a SAFE command (so `git diff --output=x` is not treated as read-only).

## 4. Pluggable engine skills (local agent)

Behind Protocols; swap dev stubs for production engines with env vars. See
[`docs/PROVIDERS.md`](docs/PROVIDERS.md).

| Skill | Protocol | Dev (default) | Production option | Env |
|---|---|---|---|---|
| Wake word | `WakeWordEngine` (`wake.py`, `voice_local.py`) | type-driven, zero deps | **Vosk** (default real stack via `jarvis-voice`) · Porcupine (`voice-alt`) | `JARVIS_WAKE_ENGINE=porcupine`, `JARVIS_PICOVOICE_KEY` |
| Speech-to-text | `STTProvider` (`stt.py`, `voice_local.py`) | text source | **Vosk** (default real stack) · faster-whisper (`voice-alt`) | `JARVIS_STT_ENGINE=whisper` |
| Text-to-speech | `TTSProvider` (`tts.py`, `voice_local.py`) | prints line + voice id | **Windows SAPI via pyttsx3** (default real stack) · Azure / ElevenLabs / Piper | `JARVIS_TTS_ENGINE=pyttsx3` |
| LLM | `LLMProvider` (`core/llm.py`) | `EchoLLM` | OpenAI / Ollama (OpenAI-compatible) | `JARVIS_LLM_PROVIDER=openai\|ollama`, `JARVIS_LLM_BASE_URL`, `JARVIS_LLM_API_KEY` |
| Search | `SearchProvider` (`core/search.py`) | `DisabledSearch` | DuckDuckGo Instant Answer (keyless; other JSON shapes need a `_parse` branch) | `JARVIS_SEARCH_PROVIDER=duckduckgo\|generic`, `JARVIS_SEARCH_BASE_URL` |
| Router | `Router` (`core/router.py`) | `KeywordRouter` | `LangGraphRouter` | implement Protocol |
| Scheduler | APScheduler (`jobs.py`) | in-process | persistent store / Celery / RQ | config |

## 5. Add a new skill (the extension recipe)

Adding capability is **additive** — the supervisor and router pick it up with no changes.

### Add an agent
1. Register an `AgentSpec` in `jarvis-shared/src/jarvis_shared/agents.py` (id, role, personality,
   `tools` categories, `routing_keywords`).
2. Add a voice profile in `jarvis-shared/src/jarvis_shared/voice.py` (distinct pitch/speed/style —
   **original synthetic voices only**).
3. Add a handler class in `jarvis-backend/src/jarvis_backend/agents/runtime.py` and register it in
   `_AGENT_CLASSES`. An agent you leave unregistered falls through to `LLMAgent` (the
   `build_agent` default), so it will reason with the configured `LLMProvider` and speak only
   text — never a `ToolRequest`. If the new agent is meant to be a not-yet-wired *integration*
   (web search, OAuth, a device), do **not** rely on that fallback: add an explicit handler
   that honestly says the integration isn't connected (like `ShuriAgent`/`NatashaAgent` and
   their `# SEAM` markers), so it never silently answers as a generic reasoner.

### Connect an unconnected skill (close a SEAM)
1. Find the `# SEAM` marker in `runtime.py` for that agent.
2. Call the real integration (search tool, Google OAuth, `LLMProvider`).
3. Emit `ToolRequest`s for anything that touches the machine or sends data — reads SAFE,
   sends/mutations RESTRICTED so they route through the approval queue.
4. Keep the honesty rule: only speak what actually happened.

### Add a tool category
1. Extend `_VALID_CATEGORIES` and add a handler in the local validator (`security.py`), defaulting
   to the **tightest** sensible classification.
2. Mirror the risk assessment in the backend policy engine (`core/policy.py`).
3. Implement the executor branch in `jarvis-local-agent/.../tools.py`.
4. Add tests for the dangerous and out-of-allow-list cases.

## 6. Allow-lists (the PC's own authority)

Set on the PC, in the local agent's `.env`. **The backend cannot widen these.**

```ini
JARVIS_ALLOWED_PATHS=C:\Users\you\projects      # ; or , separated; filesystem + terminal cwd
JARVIS_ALLOWED_APPS=notepad,code,explorer,chrome,msedge
```

- Paths are resolved and checked with `relative_to` (no traversal escapes).
- Apps must be **bare names** on the list and resolvable on `PATH` (a path like
  `C:\evil\notepad.exe` is refused — the allow-list is on the name).

## 7. Skill safety invariants (never regress)

1. A human grant can satisfy "needs approval" but **never** unlock a hard block.
2. The local validator may **tighten** the backend's verdict, never loosen it.
3. The LLM/agents never receive raw shell/filesystem/input — only `ToolRequest`s through both gates.
4. Commands never touch a shell; shell metacharacters are DANGEROUS by definition.
5. An agent never claims work it did not do.
6. Approving a high-risk action (DANGEROUS, or RESTRICTED when configured) requires a **fresh
   second factor** when the admin has MFA — a stolen live session alone cannot grant it. Step-up
   only adds a gate; it never unlocks a hard block, and the agent/device role stays 403.

## 8. Operator authentication (MFA + step-up)

TOTP MFA is opt-in per admin (RFC 6238; implemented in stdlib — the runtime never imports
`pyotp`). Enroll in the dashboard (System → Two-factor authentication) or via the CLI
(`python -m jarvis_backend enroll-mfa --username <name>`). The secret is field-encrypted at rest
and never returned after confirmation; ten one-time recovery codes are shown once and stored as
argon2 hashes. With MFA on, a password returns an `mfa_pending` challenge (a distinct token type
rejected by every protected route), and real tokens are issued only after a valid code. See
[`docs/OPERATIONS.md`](docs/OPERATIONS.md) for the enrollment and lost-authenticator runbook.

| Setting | Default | Purpose |
|---|---|---|
| `JARVIS_MFA_ISSUER` | `JARVIS` | Name shown in the authenticator app / otpauth URI |
| `JARVIS_MFA_RECOVERY_CODES` | `10` | One-time recovery codes generated at enablement |
| `JARVIS_STEP_UP_WINDOW_SECONDS` | `300` | How long a step-up proof stays fresh for a grant |
| `JARVIS_APPROVAL_REQUIRE_MFA` | `false` | Require the admin to have MFA for high-risk grants |
| `JARVIS_APPROVAL_STEP_UP_RISK` | `DANGEROUS` | Lowest risk that forces step-up (`DANGEROUS`/`RESTRICTED`) |
