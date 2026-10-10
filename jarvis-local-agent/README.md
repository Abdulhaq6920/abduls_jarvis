# jarvis-local-agent

The persistent Windows runtime — **the primary interface**. Starts with Windows,
lives in the tray, detects the wake word locally, and speaks responses in each
agent's voice.

## Responsibilities

- Start with Windows (`scripts/install_autostart.ps1`), stay in the system tray.
- Monitor the **"Hey JARVIS"** wake word locally (low CPU idle, no cloud audio).
- Capture mic input after wake → STT → backend → TTS → playback.
- Run the conversation state machine (IDLE → WAKE → LISTENING → PROCESSING →
  RESPONDING → ACTIVE_CONVERSATION → IDLE) with a configurable timeout.
- Be the **security boundary**: re-validate every `ToolRequest` locally before
  executing any computer operation.
- Desktop notifications, pause/resume mic, interruption ("JARVIS, stop").

## Modules

| Module | Role |
| --- | --- |
| `wake.py` | `WakeWordEngine` Protocol + dev & Porcupine engines. |
| `stt.py` | `STTProvider` Protocol + dev & Whisper providers. |
| `tts.py` | `TTSProvider` Protocol + dev & pyttsx3; agent voice selection. |
| `security.py` | `LocalSecurityValidator` — independent second gate. |
| `tools.py` | `ToolExecutor` for fs/terminal/app/browser/screen/kbd/mouse. |
| `runtime.py` | `JarvisRuntime` — conversation FSM + voice pipeline. |
| `tray.py` | System tray (pystray) + console fallback. |
| `notifications.py` | Desktop notifications (win10toast) + console fallback. |
| `backend_client.py` | Secure httpx client to the backend. |
| `config.py` | Env-driven runtime config + allow-lists. |

## Run

```powershell
python -m jarvis_local_agent
```

Dev mode: type a line containing "hey jarvis"; the rest is your request. See
`../docs/PROVIDERS.md` to enable real microphone, speech, and tray.

## Autostart

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
# undo:
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Remove
```
