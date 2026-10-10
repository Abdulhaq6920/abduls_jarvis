# Success criteria coverage

Mapping each of the 15 spec criteria to where it lives in this prototype.

| # | Criterion | Status | Where |
| --- | --- | --- | --- |
| 1 | Starts automatically with Windows | Wired | `jarvis-local-agent/scripts/install_autostart.ps1` (Startup shortcut). |
| 2 | Wake-word detection works locally | Done (dev) / Seam (prod) | `wake.py` — `DevWakeWordEngine`; `PorcupineWakeWordEngine` for production. Audio stays local until wake. |
| 3 | User can speak without opening the UI | Done | Runtime is the primary interface; dashboard is secondary and optional. |
| 4 | Speech converted to text | Done (dev) / Seam (prod) | `stt.py` — `STTProvider`; `WhisperSTTProvider` for local prod. |
| 5 | Routes through LangGraph/supervisor | Done | `supervisor.py` (OBSERVE→…→REPORT) + `router.py` (`Router` Protocol, LangGraph-replaceable). |
| 6 | Specialized agents execute tasks | Done | `agents_runtime.py` — SHURI/TONY/NATASHA/FRIDAY + generic. |
| 7 | Distinct synthetic voice profiles | Done | `voice.py` — one `AgentVoiceProfile` per agent, distinct pitch/speed/style; verified in demo output. |
| 8 | Computer ops permission-controlled | Done | `policy.py` (backend gate) + `security.py` (independent local validator) + allow-lists. |
| 9 | Google integrations use OAuth | Seam | `tools.py::_integration`; reads SAFE, sends RESTRICTED→approval. |
| 10 | Risky operations require approval | Done | `approvals.py` + Test 5; dangerous commands blocked by both gates. |
| 11 | Background tasks run without the dashboard | Done | `scheduler.py` (APScheduler, no request-loop); runs in backend lifespan. |
| 12 | User can interrupt speech | Done | `runtime._is_interrupt` + `TTSProvider.stop()` — local, low-latency. |
| 13 | Activity is observable | Done | Dashboard presence chain + WebSocket `/ws/presence` + supervisor trace. |
| 14 | Important actions are auditable | Done | `audit.py` — append-only JSONL + in-memory; `/audit` endpoint + dashboard feed. |
| 15 | Polished AI-OS look (not a chatbot) | Done | `dashboard.py` — calm AI-OS UI with voice states, presence, approvals. |

## Architectural guarantees

- **Only JARVIS owns the mic/wake lifecycle.** Agents receive text/task only
  (`agents_runtime` has no audio access).
- **Local agent is the security boundary.** The LLM never gets raw shell/fs/input;
  every op is `ToolRequest → PolicyEngine → LocalSecurityValidator → ToolExecutor`.
  The local validator rejects dangerous/out-of-allowlist requests even when the
  backend approved them (`test_local_validator_rejects_dangerous_even_if_backend_asks`).
- **Six+1 stages never collapsed.** Every `/assist` response carries a full
  `OBSERVE→PLAN→PROPOSE→APPROVE→EXECUTE→VERIFY→REPORT` trace
  (`test_supervisor_emits_all_stages`).
- **Everything replaceable.** Wake/STT/TTS/LLM/Router/Scheduler all sit behind
  Protocols; adding an agent/voice/tool/provider is additive.

## Verified in this build

`python tests/test_prototype.py` → 9/9 pass. Live backend + runtime demo exercises all
five scenarios against real HTTP, printing per-agent voice ids and the approval flow.
