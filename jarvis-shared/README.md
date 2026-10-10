# jarvis-shared

Code used by both the backend and the local agent, so they agree on the wire format and on safety.

| Module | Contents |
|---|---|
| `agents.py` | `AgentId`, `AgentSpec`, the 10-agent roster and personalities |
| `voice.py` | `AgentVoiceProfile`, `VoiceSettings`, distinct synthetic default voices |
| `audio.py` | `AudioInput`, `AudioResult`, `Transcript` |
| `conversation.py` | `ConversationState` |
| `supervisor.py` | `SupervisorStage` (OBSERVE … REPORT) |
| `security.py` | `RiskLevel`, `CommandCategory`, `ToolRequest`, `ToolResult` |
| `messages.py` | `AssistRequest` / `AssistResponse` (with input length limits), `StageTrace` |
| `commands.py` | `classify_command`: exact-program, allow-listed-flag classification (no shell) |
| `log.py` | Structured logging, request ids, and secret scrubbing |

`commands.py` is the single implementation of "is this command safe?". Both sides call it, but each
applies its own allow-lists on top, so neither trusts the other's verdict.

Voices are original synthetic voices, never imitations of real people.
