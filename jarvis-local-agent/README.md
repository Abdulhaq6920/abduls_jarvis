# jarvis-local-agent

The part that runs on your Windows PC. It listens for "Hey JARVIS" **locally**, turns speech into
text **locally**, sends only that text to the backend, speaks the answer in the responding agent's
voice, and runs approved actions on your machine, after re-checking them itself.

Microphone audio never leaves the PC.

## Launchers

| Command | What it is |
|---|---|
| `jarvis-voice` | The real thing: microphone, offline wake word and speech-to-text, centre-screen wave overlay |
| `jarvis-local-agent` | Typed-input version for development without a microphone |
| `python -m jarvis_local_agent.credentials set` | Store the device id and secret in the Windows Credential Manager |

## Layout

```
src/jarvis_local_agent/
  voice_app.py      launcher: wires the real mic, overlay and runtime together
  runtime.py        conversation state machine, tool execution, approval loop, heartbeat
  backend_client.py device login (JWT), refuses plain http to non-local hosts
  security.py       LOCAL VALIDATOR: the real security boundary (allow-listed folders and apps)
  tools.py          executor: no shell, scrubbed environment, output capped
  voice_local.py    Vosk wake word + speech-to-text, Windows SAPI speech
  overlay.py        the on-screen wave overlay
  wake.py stt.py tts.py   replaceable engine interfaces (+ dev and alternative adapters)
  credentials.py    OS credential store helper
  config.py         settings (.env) incl. the allow-lists
  single_instance.py  only one runtime per sign-in
```

## Setup

1. Register this PC on the dashboard (System → Local agents) and copy the id and secret it shows once.
2. `python -m jarvis_local_agent.credentials set` (the secret prompt is hidden).
3. Copy `.env.example` to `.env` here and set `JARVIS_ALLOWED_PATHS` to the folders JARVIS may touch.
4. Run `jarvis-voice`. To start it at sign-in: `scripts\install_autostart.ps1`.

## Security in one paragraph

The backend can only *ask*. This agent decides: commands are classified by exact program and
allow-listed flags (never through a shell), the working directory must be inside
`JARVIS_ALLOWED_PATHS`, applications must be on `JARVIS_ALLOWED_APPS` and named (not paths), and a
human approval can satisfy "needs approval" but can never unlock a hard block. Child processes get an
environment stripped of secrets.
