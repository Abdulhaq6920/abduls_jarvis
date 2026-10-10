# Swapping components

Wake word, speech-to-text, text-to-speech, the router and the agents each sit behind a small
interface, so replacing one is an adapter, not a rewrite of the supervisor.

## What `jarvis-voice` uses today

| Layer | Implementation | Where |
|---|---|---|
| Wake word | Vosk, restricted grammar, **fully local** | `voice_local.py::VoskWakeWordEngine` |
| Speech-to-text | Vosk, **fully local** | `voice_local.py::VoskSTTProvider` |
| Text-to-speech | Windows SAPI, one voice profile per agent | `voice_local.py::SapiTTSProvider` |

`jarvis-voice` (the real microphone launcher) wires these directly. The environment variables
`JARVIS_WAKE_ENGINE` / `JARVIS_STT_ENGINE` / `JARVIS_TTS_ENGINE` only affect the typed-input
launcher, `jarvis-local-agent`, which is for development without a microphone.

## Replacing a layer

* **Wake word** (e.g. Picovoice Porcupine): implement `WakeWordEngine` from `wake.py`
  (`start / pause / resume / stop / paused / name`). A Porcupine adapter already exists there
  and reads `JARVIS_PICOVOICE_KEY`; it needs `pvporcupine` and a key from Picovoice.
* **Speech-to-text** (e.g. Whisper): implement `STTProvider.transcribe`. A `faster-whisper`
  adapter exists in `stt.py`; install with `pip install -e "jarvis-local-agent[voice-alt]"`.
* **Text-to-speech** (Azure, ElevenLabs, Piper): implement `synthesize` / `speak` / `stop` and map
  `AgentVoiceProfile.voice_id` to the engine's voice. Use original synthetic voices; never imitate
  a real person or a copyrighted performance.
* **Router** (e.g. a LangGraph or LLM router): implement `Router.route` from `core/router.py`
  and pass it to `Supervisor(router=...)` in `container.py`.
* **A new agent**: add an `AgentSpec` in `jarvis_shared/agents.py` and a default voice in
  `jarvis_shared/voice.py`, then a class in `jarvis_backend/agents/runtime.py` (unlisted agents fall
  back to `UnconnectedAgent`, which says honestly that it has no model).

## Not connected yet

These are deliberately stubs that say so out loud rather than pretend:

| Capability | Agent | What is needed |
|---|---|---|
| Web research | SHURI | A search API and an LLM to synthesise results |
| Gmail / Calendar | NATASHA | Google OAuth client (minimal scopes); send/modify must keep going through approval |
| Reasoning, planning, analysis | VISION, DR. STRANGE, QUICKSILVER, NICK FURY | An LLM provider and API key |
| GUI automation | FRIDAY | Keyboard/mouse/screen tools, always behind approval |

Secrets for these belong in `.env` or `./secrets/`, never in code or chat. Add the matching field
to `jarvis_backend/config.py` as a `SecretStr` so it is redacted everywhere.

## Backend configuration reference

See `.env.example` (every setting is documented there) and `python -m jarvis_backend check-config`.
Local agent settings are in `jarvis-local-agent/.env.example`.
