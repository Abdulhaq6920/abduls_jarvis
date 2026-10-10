# jarvis-shared

Shared Pydantic contracts used across every boundary (local runtime ↔ backend ↔
dashboard). Keeping them here means both sides agree on the wire format and each
implementation layer stays replaceable.

| Module | Contents |
| --- | --- |
| `agents.py` | `AgentId`, `AgentSpec`, the 10-agent roster + personalities. |
| `voice.py` | `AgentVoiceProfile`, `VoiceSettings`, distinct synthetic defaults. |
| `audio.py` | `AudioInput`, `AudioResult`, `Transcript`. |
| `conversation.py` | `ConversationState` FSM states. |
| `supervisor.py` | `SupervisorStage` (OBSERVE…REPORT). |
| `security.py` | `RiskLevel`, `CommandCategory`, `ToolRequest`, `ToolResult`. |
| `messages.py` | `AssistRequest`, `AssistResponse`, `StageTrace`, `RuntimeStatus`. |

Voices are **original synthetic** profiles — never impersonations of real actors or
copyrighted performances.
