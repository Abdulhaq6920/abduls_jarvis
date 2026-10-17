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

### Non-English Vosk models and SAPI voice hint

The desktop Vosk stack defaults to English (`en-us`), but the language is configurable:

| Env | Default | Effect |
| --- | --- | --- |
| `JARVIS_VOSK_MODEL_LANG` | `en-us` | Bundled language code loaded by `vosk.Model(lang=...)`. |
| `JARVIS_VOSK_MODEL_PATH` | *(empty)* | Path to a downloaded model directory; set it to use a model that is not bundled. Overrides the language code. |
| `JARVIS_TTS_VOICE_HINT` | *(empty)* | Substring of a SAPI voice name/id/language to prefer; empty keeps the english david/zira default. |

To use another language, download a model from the Vosk model list
(https://alphacephei.com/vosk/models), unzip it, and point `JARVIS_VOSK_MODEL_PATH` at that
directory (or set `JARVIS_VOSK_MODEL_LANG` for a bundled language). The wake grammar is then
derived from your configured `JARVIS_WAKE_PHRASE`, so a phrase in another language works too.
Pair it with `JARVIS_TTS_VOICE_HINT` to prefer a matching non-English Windows voice for
output. For the lowest-friction multi-language path, the dashboard browser voice button needs
no downloads — the browser's own speech recognition handles the language selection.

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

**Routing widens that boundary.** With `JARVIS_ROUTER=llm` (the default) and a hosted
provider, the router makes its own call *before* an agent is chosen, so the table above is not
the whole picture:

| Field | Source | Bound sent to the provider |
| --- | --- | --- |
| The request itself | the utterance, wake word stripped | every request that does not name an agent |
| Agent menu | `id`, `role`, `routing_keywords` of the live roster | no user data |

That call sends the raw request text for agents that otherwise never touch a provider —
FRIDAY, TONY, NATASHA, WONG and JARVIS are deterministic and LLM-free, yet their utterances
are classified by the model. A *"remember that ..."* fact for WONG is the sharpest case: it is
Fernet-encrypted at rest, and with hosted routing the utterance carrying it crosses the wire
for the routing decision. Three ways to avoid it: keep `JARVIS_ROUTER=keyword`, keep the
`echo` provider (the router short-circuits before any call), or point `JARVIS_LLM_BASE_URL` at
a local Ollama. An explicit "ask TONY to ..." is resolved locally and never sent either way.

With the default `EchoLLM` nothing leaves the machine. With a hosted provider this context
is transmitted to that provider, so point `JARVIS_LLM_BASE_URL` only at an endpoint you trust
with it. A fully offline deployment should use Ollama (`http://localhost:11434/v1`) or keep
the `echo` default. The fields are deliberately bounded and truncated; none include raw file
contents, secrets, or tool output — only names, short notes, and short reply snippets. The
utterance and the reply are never written to the logs on any path, including failures (those
log the error class only).

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

### Routing — `Router`

`jarvis_backend/core/router.py` ships two `Router` implementations and a factory:

- `KeywordRouter` — deterministic and offline. Explicit name, then whole-word
  `routing_keywords` scoring, then JARVIS. Predictable, but blind to synonyms and spelling
  variants ("analyse" vs `analyze`).
- `LLMRouter` — hands the utterance and a menu of agents (id, role, keywords, derived from
  the live roster) to the `LLMProvider` and asks for one agent id, so a natural phrasing
  reaches the right specialist without "ask TONY to ...". It wraps a `KeywordRouter`.
- `build_router(settings, llm)` — `KeywordRouter` for `JARVIS_ROUTER=keyword`, otherwise
  `LLMRouter`.

The fallback chain is strict: **explicit `ask <agent> to ...` → LLM → keywords → JARVIS.**
An explicitly named agent always wins and is never sent to the model. `LLMRouter.route`
never raises (same policy as the provider): an unreachable model, the provider's own offline
fallback sentence, a reply that names no roster agent *or names several* (a preamble like
"the JARVIS router would send this to VISION" is ambiguous, so it is refused rather than
guessed) all degrade to keyword routing, log a warning, and the PLAN stage records which path
was taken. `EchoLLM` is short-circuited **before** any call, so the dev/test default is
exactly the deterministic keyword behaviour, PLAN reason included; an empty utterance (a bare
wake word, a blank STT result) skips the call too.

The routing call has its own budget, `JARVIS_ROUTER_TIMEOUT_SECONDS` /
`JARVIS_ROUTER_MAX_TOKENS`, not the agent's: it is serialized ahead of the agent's own call,
so a stalled provider must not be able to burn the full `JARVIS_LLM_TIMEOUT_SECONDS` before
keyword routing takes over.

Routing only decides *who answers*; the chosen agent's `ToolRequest`s still pass the policy
gate and the local validator unchanged.

| Env | Default | Effect |
| --- | --- | --- |
| `JARVIS_ROUTER` | `llm` | `llm` \| `keyword`. `llm` falls back to keywords whenever the model is unavailable or unsure (always, with the `echo` provider). |
| `JARVIS_ROUTER_TIMEOUT_SECONDS` | `5` | Timeout for the routing call only. |
| `JARVIS_ROUTER_MAX_TOKENS` | `16` | Token cap for the routing call only (it answers with one id). |

## Web search — `SearchProvider`

`jarvis_backend/core/search.py` ships two `SearchProvider` implementations and a factory,
mirroring the LLM seam:

- `DisabledSearch` — the offline default. No network, no key; `search(...)` returns an empty
  list (never fabricated results). SHURI then honestly reports it found nothing.
- `GenericJsonSearch` — the real provider over `httpx`. It GETs a **DuckDuckGo-shaped** JSON
  endpoint and maps the instant-answer abstract plus `RelatedTopics` (recursing one level into
  grouped topics) into `SearchResult(title, url, snippet)`, capped to the limit. The one engine
  it parses today is the **DuckDuckGo Instant Answer API** (`https://api.duckduckgo.com/`), which
  is keyless and HTTPS, so it works out of the box. A Bearer header is sent only when a key is
  configured. **Other engines (e.g. SearxNG's `/search?format=json`, whose body is
  `{"results": [{title, url, content}]}`) have a different JSON shape** — pointing
  `JARVIS_SEARCH_BASE_URL` at one without extending `_parse` returns no results (honest and
  non-crashing, but not real hits). To support SearxNG, add a branch to `_parse` for its
  `results[]` shape; the class is structured for exactly that.
- `build_search(settings)` — returns `DisabledSearch` for `JARVIS_SEARCH_PROVIDER=none`
  (default) and for a real provider with no base URL; otherwise a `GenericJsonSearch`.

SHURI calls `services.search.search(topic)` and, when results come back, asks
`services.llm.complete(...)` for a brief spoken synthesis under an honesty rule (synthesise
only what the sources state, invent no URLs or facts). SHURI returns **speech only** — never a
`ToolRequest` — so research is read-only and can never touch the machine. On empty results it
says so honestly rather than fabricating. DuckDuckGo IA is an instant-answer service, not a
ranked-link index, so some topics legitimately return nothing.

**Error policy:** `GenericJsonSearch.search` never raises. On any transport, HTTP-status, JSON
or shape error it logs only the error class (never the query text or the key) and returns an
empty list, so a search outage never 500s an `/assist` request.

**Transport safety:** the query is only ever sent over `https://` or to a loopback host. If
`JARVIS_SEARCH_BASE_URL` is a remote `http://` endpoint the provider refuses the request —
whether or not an API key is set — so neither the query nor a Bearer `JARVIS_SEARCH_API_KEY` is
transmitted in plaintext; in `prod`, `check-config` fails closed on that configuration.
Loopback `http://` (a local engine) is unaffected. `provider=none` is a valid disabled state and
never produces a finding.

| Env | Default | Effect |
| --- | --- | --- |
| `JARVIS_SEARCH_PROVIDER` | `none` | `none` (disabled) \| `duckduckgo` \| `generic`. |
| `JARVIS_SEARCH_BASE_URL` | *(empty)* | DuckDuckGo `https://api.duckduckgo.com/` (the only shape parsed today). Empty ⇒ falls back to `DisabledSearch`. |
| `JARVIS_SEARCH_API_KEY` | *(unset)* | `SecretStr`; DuckDuckGo needs none; sent as Bearer for a keyed engine. |
| `JARVIS_SEARCH_TIMEOUT_SECONDS` | `10` | Per-request timeout. |
| `JARVIS_SEARCH_MAX_RESULTS` | `5` | Cap on results synthesised. |

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
