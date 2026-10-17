# Swapping in production providers

Every I/O layer is behind an interface, so moving from the dev prototype to real
engines is configuration + an adapter, never a supervisor rewrite.

> **The default real voice stack is Vosk + Windows SAPI, not Porcupine/Whisper.**
> `jarvis-voice` (`voice_app.py` → `voice_local.py`) runs **Vosk** for the wake word and
> speech-to-text (offline, no API key) and **Windows SAPI via pyttsx3** for per-agent voices.
> Install it with the `[voice]` extra. Porcupine and faster-whisper below are the *alternative*
> engines (the `[voice-alt]` extra) you can opt into — they are not the default. The tables in
> this file describe the swappable seams; the shipped default is Vosk/SAPI.
>
> ```powershell
> # the default real voice stack (Vosk wake + STT, SAPI voices)
> .\.venv\Scripts\python.exe -m pip install -e "jarvis-local-agent[voice]"
> # alternative engines (Porcupine wake, faster-whisper STT)
> .\.venv\Scripts\python.exe -m pip install -e "jarvis-local-agent[voice-alt]"
> ```

## Install the optional extras

```powershell
.\.venv\Scripts\python.exe -m pip install -e "jarvis-local-agent[voice,tray]"
```

## Wake word — `WakeWordEngine`

| Env | Value | Effect |
| --- | --- | --- |
| `JARVIS_WAKE_ENGINE` | `dev` (default) | Type-driven, zero deps. |
| `JARVIS_WAKE_ENGINE` | `porcupine` | Local Picovoice Porcupine inference. |
| `JARVIS_PICOVOICE_KEY` | your key | Required for Porcupine. |
| `JARVIS_WAKE_SENSITIVITY` | `0.0`–`1.0` | False-positive vs. miss tradeoff. |

Audio stays local until the wake word fires. To add another engine, implement the
`WakeWordEngine` Protocol in `wake.py` and extend `build_wake_engine`.

## Speech-to-text — `STTProvider`

| Env | Value | Effect |
| --- | --- | --- |
| `JARVIS_STT_ENGINE` | `dev` (default) | Text source, zero deps. |
| `JARVIS_STT_ENGINE` | `whisper` | Local faster-whisper (private, low latency). |

Add a cloud STT by implementing `STTProvider.transcribe` and extending `build_stt`.

## Text-to-speech — `TTSProvider`

| Env | Value | Effect |
| --- | --- | --- |
| `JARVIS_TTS_ENGINE` | `dev` (default) | Prints spoken line + voice id; uses SAPI if pyttsx3 present. |
| `JARVIS_TTS_ENGINE` | `pyttsx3` | Offline Windows SAPI5 voices. |

For Azure / ElevenLabs / Piper, implement `synthesize` + `speak` + `stop` and map
`AgentVoiceProfile.voice_id` to the engine's voice. **Use original synthetic voices;
never imitate real actors or copyrighted performances.** The agent logic does not
change.

## LLM / routing — `LLMProvider` + `Router`

`jarvis_backend/core/llm.py` ships two `LLMProvider` implementations and a factory:

- `EchoLLM` — the deterministic offline default. No network, no key; it echoes the user
  message. Keeps dev and the test suite reproducible.
- `OpenAICompatibleLLM` — the real provider. It POSTs to `{base_url}/chat/completions`
  using the OpenAI JSON shape, which both **OpenAI** and a local **Ollama** server serve. A
  Bearer header is sent only when an API key is set, so Ollama (no key) works as-is.
- `build_llm(settings)` — returns `EchoLLM` for `JARVIS_LLM_PROVIDER=echo` (default) and for
  a real provider with no base URL; otherwise an `OpenAICompatibleLLM` built from settings.

The reasoning agents (VISION / DR. STRANGE / QUICKSILVER / NICK FURY) call
`services.llm.complete(...)` with a persona system prompt plus the user task. They return
**speech only** — never a `ToolRequest` — so the LLM can never bypass the two security gates.

**What leaves the machine (data boundary).** When `JARVIS_LLM_PROVIDER` points at a hosted
provider, `LLMAgent` sends more than the persona and the task. The system prompt is enriched
in `LLMAgent._context_lines` with a *bounded* slice of local context so the model can reason
usefully:

| Field | Source | Bound sent to the provider |
| --- | --- | --- |
| Persona + role | `spec.personality`, `spec.role` | always |
| User task | the utterance routed to the agent | always |
| Allowed project paths | `context["allowed_paths"]` | first 5 |
| Allowed application names | `context["allowed_apps"]` | first 10 |
| Remembered notes | `context["memories"]` | first 3, 160 chars each |
| Recent conversation | `context["history"]` | last 3 turns, 80 chars/side |

With the default `EchoLLM` nothing leaves the machine. With a hosted provider this context
is transmitted to that provider, so point `JARVIS_LLM_BASE_URL` only at an endpoint you trust
with it. A fully offline deployment should use Ollama (`http://localhost:11434/v1`) or keep
the `echo` default. The fields are deliberately bounded and truncated; none include raw file
contents, secrets, or tool output — only names, short notes, and short reply snippets.

**Error policy:** `OpenAICompatibleLLM.complete` never raises. On any transport, HTTP-status,
JSON or shape error (including a successful response whose `message.content` is not a string)
it logs the error class (never the key or payload) and returns a short honest fallback
sentence, so a provider outage or a malformed body never 500s an `/assist` request.

**Transport safety:** the prompt and the bounded context above are only ever sent over
`https://` or to a loopback host. If `JARVIS_LLM_BASE_URL` is a remote `http://` endpoint the
provider refuses the request — **whether or not an API key is set** — so neither the prompt
nor a Bearer `JARVIS_LLM_API_KEY` is ever transmitted in plaintext; in `prod`, `check-config`
also fails closed on that configuration. Local Ollama over loopback `http://` needs no key and
is unaffected.

| Env | Default | Effect |
| --- | --- | --- |
| `JARVIS_LLM_PROVIDER` | `echo` | `echo` \| `openai` \| `ollama`. |
| `JARVIS_LLM_MODEL` | `gpt-4o-mini` | Model name (Ollama: e.g. `llama3.1`). |
| `JARVIS_LLM_BASE_URL` | *(empty)* | OpenAI `https://api.openai.com/v1`; Ollama `http://localhost:11434/v1`. Empty ⇒ falls back to `EchoLLM`. |
| `JARVIS_LLM_API_KEY` | *(unset)* | `SecretStr`; required for OpenAI, not for Ollama. |
| `JARVIS_LLM_TIMEOUT_SECONDS` | `30` | Per-request timeout. |
| `JARVIS_LLM_MAX_TOKENS` | `512` | Response length cap. |

`jarvis_backend/core/router.py` ships `KeywordRouter`. A `LangGraphRouter` implementing the
`Router` Protocol can replace it; the supervisor is agnostic.

## Scheduler

`jarvis_backend/scheduler.py` uses APScheduler in-process. For durability across
restarts, point it at a persistent job store or move jobs to a dedicated worker
(Celery/RQ). Jobs already run independently of FastAPI requests.

## Google integrations (OAuth)

The Gmail/Calendar tool executors in `jarvis_local_agent/tools.py::_integration` are
seams. Wire Google OAuth there; reads stay SAFE, sends/mutations are RESTRICTED and
route through the approval queue (as Test 5 demonstrates).

## Config reference (env vars)

```
JARVIS_BACKEND_URL        default http://127.0.0.1:8077
JARVIS_BACKEND_HOST/PORT  backend bind (default 127.0.0.1:8077)
JARVIS_WAKE_PHRASE        default "hey jarvis"
JARVIS_CONV_TIMEOUT       active-conversation timeout seconds (default 12)
JARVIS_ALLOWED_PATHS      ; or , separated allow-list for filesystem/terminal
JARVIS_ALLOWED_APPS       , separated app allow-list for application tool
```
