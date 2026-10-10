# JARVIS prototype demo

Two ways to run the five prototype tests.

## A. Offline, deterministic (no audio, no accounts)

```powershell
.\.venv\Scripts\python.exe tests\test_prototype.py      # assertions
.\.venv\Scripts\python.exe -m jarvis_backend            # terminal 1: backend
.\.venv\Scripts\python.exe tests\live_runtime_demo.py   # terminal 2: scripted voice turns
```

`live_runtime_demo.py` prints each spoken line as:

```
🔊  [shuri_default] I've completed the research on LangGraph...
```

The `[...]` is the agent's configured synthetic voice id — proof the response is
routed to the right voice profile.

## B. Interactive (type to "speak")

```powershell
.\.venv\Scripts\python.exe -m jarvis_backend            # terminal 1
.\.venv\Scripts\python.exe -m jarvis_local_agent        # terminal 2
```

In terminal 2, type a line containing the wake phrase. Everything after it is your
request:

```
hey jarvis, ask SHURI to research LangGraph
```

Open the dashboard at http://127.0.0.1:8077 to watch agent presence, approvals, and
the audit feed live.

---

## The five tests

| Test | Say | Expected |
| --- | --- | --- |
| 1 | `Hey JARVIS.` | JARVIS: "Yes. I'm listening." |
| 2 | `Hey JARVIS, ask SHURI to research LangGraph.` | JARVIS announces delegation; SHURI answers in `shuri_default`. |
| 3 | `Hey JARVIS, ask TONY to check my project.` | TONY runs `git status` in the approved path (SAFE, auto-approved), speaks in `tony_default`. |
| 4 | `Hey JARVIS, check my Gmail.` | NATASHA reads the inbox (SAFE), speaks in `natasha_default`. No approval. |
| 5 | `Hey JARVIS, ask NATASHA to send a reply.` | NICK FURY gates it: pending approval + desktop notification. Executes only after you approve on the dashboard. |

## Approving an action (Test 5)

1. Trigger Test 5. A card appears under **Approvals** on the dashboard.
2. Click **Approve** (or `POST /approvals/{id}` with `{"granted": true}`).
3. The audit feed records `approval_resolved`.

## Interruption

Say `JARVIS, stop` while JARVIS is speaking. TTS stops immediately — this is a local,
backend-free operation.
