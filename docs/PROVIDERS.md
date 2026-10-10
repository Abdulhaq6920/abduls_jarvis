# Swapping in production providers

Every I/O layer is behind an interface, so moving from the dev prototype to real
engines is configuration + an adapter, never a supervisor rewrite.

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

- `jarvis_backend/llm.py` ships `EchoLLM`. Implement `LLMProvider.complete` for
  OpenAI/Anthropic/Ollama and inject it into the agents.
- `jarvis_backend/router.py` ships `KeywordRouter`. A `LangGraphRouter` implementing
  the `Router` Protocol can replace it; the supervisor is agnostic.

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
