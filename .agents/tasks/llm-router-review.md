# LLM-backed intent routing with a keyword safety net

`LLMRouter` joins `KeywordRouter` behind the `Router` Protocol so a natural phrasing ("analyse this data") reaches the right specialist without the user saying "ask VISION to ...". The explicit-name branch is factored into a shared `match_explicit()` used by both routers and returns before any model call; everything else builds a small agent menu from the passed-in specs, asks the injected `LLMProvider` for one id, and degrades to keyword scoring when the provider is `EchoLLM`, when the reply names no known agent, or when the call raises. `Router.route` became async, `KeywordRouter` keeps its deterministic core as `decide()`, and `supervisor._plan` awaits it. `JARVIS_ROUTER=keyword|llm` (default `llm`) picks the implementation, and because the default provider is `EchoLLM` the out-of-the-box path stays exactly the old deterministic behaviour.

**Watch for:** `_parse_agent` returns the *earliest* known id anywhere in the reply, so a chatty model that preambles "The JARVIS router would send this to VISION" routes to JARVIS and "Not TONY, VISION should analyse it" routes to TONY — a wrong route that looks like a successful parse and never reaches the keyword fallback (confirmed, reproduced). An empty or wake-word-only utterance still spends a provider round trip and can hand a blank task to a specialist (confirmed). A real provider outage is invisible: `OpenAICompatibleLLM.complete` never raises, so the `except` branch and its log line are effectively dead for the shipped provider and outages fall back silently (confirmed).

**Verdict**: NEEDS_CHANGES

## High-level view

The fallback chain is ordered as the brief specifies, with `match_explicit()` shared rather than duplicated and the `EchoLLM` check sitting before `complete()`. Every fallback composes its reason as `"<keyword reason> (keyword fallback: <why>)"`, so the PLAN stage still says which path decided.

The weak link is reply parsing. Accepting only roster ids is right, and a hallucinated name degrades to keywords as intended, but choosing the earliest match in the reply means any preamble that names an agent wins over the actual answer. JARVIS is the most likely accidental winner because the system prompt itself says "the request router of the JARVIS assistant" and JARVIS is also a legitimate answer, so a confident "JARVIS" is indistinguishable from an echoed one.

`route()` never raises and logs only `type(exc).__name__`. The subtlety is that the shipped provider already swallows its own transport errors and returns an honest fallback sentence, so a down model arrives as an unparsable reply rather than an exception — and the unparsable path logs nothing at all.

The agent menu is genuinely derived from the passed-in specs, so a newly registered agent is routable with no prompt change. The menu truncates to six keywords per agent, which silently hides keywords the deterministic router still honours.

Routing only chooses an agent: the policy gate, local validator, approval flow, auth/MFA, the `ToolRequest` model and the honesty rule are untouched, the disabled-agent fallback to JARVIS still applies to whichever router decided, and the 7-stage trace is intact. One asymmetry falls out of the feature rather than the diff — `_delegation_note` only announces a hand-off when `decision.explicit` is true, so the LLM-routed turns this change exists to create stay silent about the hand-off.

The data boundary widened in a way the docs do not yet cover. With `router=llm` and a hosted provider, every non-explicit utterance is sent to the model *before* an agent is chosen, including requests that end up with local-only agents that never call the provider.

## Issues (9)

1. **Earliest-id reply parsing misroutes (blocking)** — `_parse_agent` takes the first roster id appearing anywhere in the reply, so preambles and negations ("The JARVIS router would send this to VISION" → JARVIS; "Not TONY, VISION should analyse it" → TONY) resolve to the wrong agent and look like a successful parse. Prefer the reply's first whitespace token when it is a known id, or scan only the first line, before falling back to a free-text scan.
2. **Blank utterance still costs a model call** — `utterance` has no `min_length` and `strip_wake_word("Hey JARVIS")` yields `""`, so the router prompts the provider with an empty `Request:` and can route a blank task to a specialist. Short-circuit to the keyword fallback when the stripped text is empty.
3. **Provider outages fall back silently** — `OpenAICompatibleLLM.complete` returns a fallback sentence instead of raising, so a real outage takes the "no usable agent" path, which emits no log line at all. Log a warning on the unparsable path too, so the PLAN reason is not the only trace of a down model.
4. **Data-boundary docs not updated** — `docs/PROVIDERS.md` still bounds egress to what `LLMAgent` sends; with `router=llm` the raw utterance of every non-explicit request now goes to the provider first, including FRIDAY commands and WONG "remember that ..." facts that are Fernet-encrypted at rest. Extend that section.
5. **Router inherits the 30s agent timeout** — the routing round trip is serialized ahead of the agent's own call with `llm_timeout_seconds` (default 30) and `llm_max_tokens` (512). Give routing a short timeout and a small token cap.
6. **Menu truncation hides live keywords** — `_MENU_KEYWORDS = 6` drops NATASHA's "meeting" (7 keywords), so the model cannot see a term the keyword router still matches. Raise the cap or derive it from the roster.
7. **No hand-off announcement on LLM routes** — `_delegation_note` requires `decision.explicit`, so an LLM-routed specialist answers with no "VISION will handle that." Decide whether non-explicit specialist routes should announce.
8. **Default-install trace is noisy** — every dev/offline request now carries "(keyword fallback: no language model configured)" in the user-visible PLAN reason. Consider suppressing the suffix for the `EchoLLM` short-circuit.
9. **Test-count evidence does not reconcile** — the commit attributes 16 new tests to this file; it collects 14 (spot-checked: `14 passed`). Correct the arithmetic in the record.

<details>
<summary>Details</summary>

### Fallback chain and the EchoLLM short-circuit

```
route(utterance)
  strip_wake_word -> match_explicit ──hit──> RouteDecision(explicit=True)   [model never called]
                            │ miss
                  isinstance(llm, EchoLLM) ──yes──> keyword.decide(utterance)
                            │ no
                  await llm.complete(prompt) ──raise──> keyword.decide(utterance)
                            │ reply
                  _parse_agent(reply, agents) ──None──> keyword.decide(utterance)
                            │ AgentId
                  RouteDecision(agent_id, explicit=False, "LLM routed to X.")
```

`_fallback` hands the *original* utterance to `decide()`, which strips the wake word itself, so the prefix is not stripped twice and the fallback `task` matches the pure keyword path exactly.

### Reply parsing picks the earliest id, not the answer

`_parse_agent` normalises the reply to padded uppercase with punctuation collapsed to spaces, then scans every roster id and keeps the match with the smallest offset. The normalisation holds up: `AgentId` values are uppercase (`DR_STRANGE`, `NICK_FURY`), `"Dr. Strange"` resolves, and whole-token matching stops `VISIONARY` and `TONYS` from reaching a real agent.

The offset tie-break is the problem. Reproduced against the roster:

```
'The JARVIS router would send this to VISION' -> AgentId.JARVIS
'Not TONY, VISION should analyse it'         -> AgentId.TONY
'VISION'                                     -> AgentId.VISION
'Agent id: VISION'                           -> AgentId.VISION
```

Both failures are silent. An unknown name degrades to keywords with a visible "keyword fallback" reason, but these parse "successfully" and the trace reads `LLM routed to JARVIS.` — the operator sees a confident decision. The JARVIS case is the likely one in practice: the system prompt names the JARVIS assistant, so a small model echoing the framing produces exactly this reply, and the outcome is worse than no LLM at all (`"open notepad"` would have reached FRIDAY through keywords).

Taking the last match instead just inverts the failure ("route this to VISION, not FRIDAY"). Checking the reply's first token first, then scanning, matches what the system prompt actually asks for.

### Empty and wake-word-only utterances reach the provider

`AssistRequest.utterance` caps length but sets no `min_length`, and `strip_wake_word("Hey JARVIS")` returns `""`. Confirmed with a fake provider:

```
empty utterance -> AgentId.VISION | task '' | prompted: 'p, steps, scenario\n\nRequest: \n\nAgent id:'
```

The model is asked to route an empty request, and whatever it names receives an empty task; the keyword router answered as JARVIS deterministically here. Blank STT results are a real input on the voice path — the preceding commit exists to handle wake words with no speech after them.

### A provider outage is invisible

`route()` holds its never-raise contract, logs the exception class only, and never touches the utterance or the API key (`CancelledError` derives from `BaseException`, so cancellation still propagates).

What the error policy misses is that `OpenAICompatibleLLM.complete` is itself a never-raise method: on transport, status, JSON or shape errors it logs and returns `"I couldn't reach the language model just now, ..."`. That sentence contains no agent id, so it lands on the `_parse_agent is None` branch — right behaviour, wrong diagnosis. The reason recorded is "language model gave no usable agent" rather than "unavailable", `log.warning("llm routing failed")` never fires, and that branch emits no log of its own. A hosted model that is down produces no router log line at all, only a per-request PLAN reason.

### Menu truncation hides live keywords

`_MENU_KEYWORDS = 6` truncates the keyword list per agent. NATASHA ships seven routing keywords, so "meeting" never reaches the model even though `KeywordRouter` still matches it — the two layers disagree about what NATASHA covers, and the gap grows silently as agents gain keywords.

### What now crosses the data boundary

`docs/PROVIDERS.md` has a "What leaves the machine (data boundary)" section that enumerates, field by field with bounds, what `LLMAgent` sends to a hosted provider. That accounting is now incomplete. With `router=llm` the raw utterance is sent *before* an agent is chosen, so requests that previously never touched the provider do now: `FridayAgent`, `TonyAgent`, `NatashaAgent`, `WongAgent` and `JarvisAgent` are deterministic and LLM-free, yet their utterances are routed through the model. "Remember that ..." is the sharpest case — those facts are Fernet-encrypted at rest precisely because they are sensitive, and the utterance carrying one now crosses the wire for a routing decision.

The transport itself is no weaker: router calls go through the same `complete()` that refuses plaintext `http://` to a non-loopback host, and the `llm_plaintext_remote` config finding still gates that. It is the deployer-facing egress table that needs the sentence; the new env var itself is documented well in `.env.example` and PROVIDERS.md.

### Latency on the voice path

The routing call is serialized ahead of the agent, so for a reasoning agent there are now two sequential model round trips before the first spoken word. The router reuses the agent provider wholesale and inherits `llm_timeout_seconds` (default 30) and `llm_max_tokens` (512): a stalled provider can burn 30 seconds before keyword routing even starts. A routing classification needs a fraction of both.

### Trace, delegation and the disabled-agent fallback

`_plan` is a one-line change (`await`); the disabled-agent branch below it still rewrites any router's choice to JARVIS, `handle()` still emits all seven stages with `decision.reason` in PLAN, and `_parse_agent` only returns ids from `registry.all()`, so the `registry.get()` downstream cannot miss.

`_delegation_note` returns `None` unless `decision.explicit`, and LLM routes are `explicit=False`. The rule predates this change, but the change makes non-explicit specialist routing the common case, so the typical successful route answers in a specialist's voice with no hand-off announcement.

In dev and offline installs the PLAN reason on every request is now `"No specialist matched; JARVIS answers directly. (keyword fallback: no language model configured)"`. Accurate, but it is the default install's user-visible trace.

### Test coverage

All five required cases are present and assert more than the happy path: explicit-wins also asserts `llm.prompts == []`, proving the model was never consulted rather than just that the id matched; the paraphrase case first shows `KeywordRouter` gives JARVIS for "analyse this data", so it demonstrates the gap it closes; off-roster replies are parametrized over `ULTRON`/`VISIONARY`/`TONYS` and check the fallback agent and the reason marker; and the `EchoLLM` equivalence test compares agent, `explicit` and `task` across six utterances. `asyncio_mode = "auto"` is set in `pyproject.toml`, so the bare `async def` tests collect. Nothing in the diff is 3.11-only — the local `Optional[tuple[int, AgentId]]` annotation is never evaluated at runtime and is valid on 3.10 regardless.

There were no routing tests before this commit (nothing at `d4939a0~1` referenced `KeywordRouter`, `RouteDecision` or `routing_keywords`), so nothing existing was modified or deleted. Routing was only exercised indirectly, and still is: `tests/integration/test_workflows.py` drives `/assist` through the real container, which now builds `LLMRouter(EchoLLM())`, and its `"SHURI is currently disabled"` assertion keeps the disabled-agent fallback covered through the async router.

Not tested: a reply containing more than one roster id or a preamble naming one (issue 1); an empty or wake-word-only utterance (issue 2); that a provider failure logs no utterance.

</details>

<details>
<summary>Files changed</summary>

- `jarvis-backend/src/jarvis_backend/core/router.py` — `LLMRouter`, shared `match_explicit()`, `_parse_agent`, `build_router`; `route()` async, `KeywordRouter.decide()` extracted
- `jarvis-backend/src/jarvis_backend/core/supervisor.py` — `_plan` awaits the router
- `jarvis-backend/src/jarvis_backend/container.py` — one `build_llm` instance shared by agents and `build_router`
- `jarvis-backend/src/jarvis_backend/config.py` — `router: Literal["keyword", "llm"] = "llm"`
- `tests/unit/test_router.py` — new, 12 functions / 14 collected items
- `.env.example`, `ARCHITECTURE.md`, `SKILL.md`, `docs/PROVIDERS.md`, `docs/PRODUCTION_PLAN.md` — routing docs and the new env var

Full diff: `git diff d4939a0~1 d4939a0`

</details>
