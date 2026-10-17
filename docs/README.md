# JARVIS Documentation

Start here. The docs are layered — pick the one that matches what you're doing.

## Read in this order

1. **[Project README](../README.md)** — what JARVIS is and how to run it in 5 minutes.
2. **[PRODUCTION_PLAN.md](PRODUCTION_PLAN.md)** — the product, the design pillars, current state, and
   the ordered roadmap to production. Read this before building anything.
3. **[SKILL.md](../SKILL.md)** — the capability map: every agent, every tool category, how a spoken
   request becomes action, and how to add more skills.
4. **[ARCHITECTURE.md](../ARCHITECTURE.md)** — the deep technical reference for all three packages.

## By task

| I want to… | Read |
|---|---|
| Understand the whole system fast | [PRODUCTION_PLAN.md](PRODUCTION_PLAN.md) → [ARCHITECTURE.md](../ARCHITECTURE.md) |
| Know what each agent can do | [SKILL.md](../SKILL.md) |
| Understand the security model | [SECURITY.md](SECURITY.md) |
| Deploy it (local → Postgres/Redis → Docker) | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Run it day to day (health, metrics, backups, rotation) | [OPERATIONS.md](OPERATIONS.md) |
| Swap dev stubs for real engines (wake/STT/TTS/LLM) | [PROVIDERS.md](PROVIDERS.md) |
| Add an agent / tool / provider safely | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| Run it on Docker for Windows | [DOCKER_WINDOWS.md](DOCKER_WINDOWS.md) |
| See how the spec criteria are met | [SUCCESS_CRITERIA.md](SUCCESS_CRITERIA.md) |
| Demo it | [DEMO.md](DEMO.md) |

## The one-paragraph version

JARVIS is a voice-first desktop assistant. A local agent listens for "Hey JARVIS", converts speech
to text on your PC, and sends **only text** to a FastAPI backend. A 7-stage supervisor routes the
request to a themed specialist agent, decides what (if anything) may run on your PC, and speaks the
answer back. Two independent security gates plus a human approval queue mean no single compromised
component can act on its own. Microphone audio never leaves the PC.

## Document map

```
README.md (root)        5-minute start + feature table
SKILL.md (root)         capability / skill map (agents, tools, extension recipes)
ARCHITECTURE.md (root)  deep technical reference (all 3 packages)
CONTRIBUTING.md (root)  how to extend without breaking the guarantees

docs/
  README.md             this index
  PRODUCTION_PLAN.md    product + design pillars + roadmap to prod
  SECURITY.md           threat model + what each defence is tested by
  DEPLOYMENT.md         three run modes + scaling
  OPERATIONS.md         day-2 runbook: health, metrics, backups, rotation, incidents
  PROVIDERS.md          swapping dev stubs for production engines
  DOCKER_WINDOWS.md     Docker on Windows specifics
  SUCCESS_CRITERIA.md   spec-criteria coverage
  DEMO.md               demo walkthrough
```
