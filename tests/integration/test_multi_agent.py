"""The full multi-agent path through the real app, with the model endpoint replaced by a script.

Real: HTTP API, auth, router, supervisor, agents, tool loop, policy gate, approvals, database,
metrics. Scripted: only the Hugging Face HTTP endpoint (httpx.MockTransport), so no network and no
cost. This is the test that proves the pieces work together, not just alone.
"""
import json

import httpx
import pytest
from jarvis_backend.app import create_app
from jarvis_backend.auth.passwords import hash_password
from jarvis_backend.llm.provider import OpenAICompatProvider
from pydantic import SecretStr

from tests.conftest import ADMIN_PASSWORD, ADMIN_USER
from tests.llm_fakes import wire, wire_call

KEY = "hf_test_key_0123456789abcdefghijklmnop"
METRICS = {"Authorization": "Bearer metrics-token-for-tests-0123456789"}


class Endpoint:
    """A pretend Hugging Face router. ``behave`` decides each reply from the request body."""

    def __init__(self):
        self.requests: list[dict] = []
        self.auth_headers: list[str] = []
        self.status = 200
        self.behave = lambda body: wire("ok")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.auth_headers.append(request.headers.get("authorization", ""))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "You have no remaining credits."})
        return httpx.Response(200, json=self.behave(body))


def system_of(body) -> str:
    return body["messages"][0]["content"]


def has_tool_result(body) -> bool:
    return any(m["role"] == "tool" for m in body["messages"])


def route_json(agent: str) -> dict:
    return wire(json.dumps({"agent": agent, "confidence": 0.9, "reason": "test"}))


@pytest.fixture
async def stack(settings, monkeypatch):
    endpoint = Endpoint()

    async def no_sleep(_):
        return None

    def make_provider(**kwargs):
        return OpenAICompatProvider(**kwargs, transport=httpx.MockTransport(endpoint), sleep=no_sleep)

    monkeypatch.setattr("jarvis_backend.llm.service.OpenAICompatProvider", make_provider)
    ai_settings = settings.model_copy(update={"hf_token": SecretStr(KEY)})

    app = create_app(ai_settings)
    async with app.router.lifespan_context(app):
        c = app.state.container
        assert c.llm.enabled
        await c.auth.users.create(ADMIN_USER, await hash_password(ADMIN_PASSWORD))
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")
        login = await http.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
        http.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield http, endpoint
        await http.aclose()


async def ask(http, text: str) -> dict:
    reply = await http.post("/api/assist", json={"session_id": "t", "utterance": text})
    assert reply.status_code == 200, reply.text
    return reply.json()


def chain(response: dict) -> list[str]:
    seen: list[str] = []
    for step in response["trace"]:
        if step["agent_id"] and step["agent_id"] not in seen:
            seen.append(step["agent_id"])
    return seen


async def test_supervisor_routes_delegates_and_answers_through_real_models(stack):
    http, endpoint = stack

    def behave(body):
        system = system_of(body)
        if "You route a spoken request" in system:
            return route_json("JARVIS")
        if "You are JARVIS" in system:
            if has_tool_result(body):
                return wire("DR. STRANGE suggests backing up first, then migrating, then verifying.")
            return wire("", [wire_call("delegate", {"agent": "DR_STRANGE", "task": "Plan a database migration weekend."})])
        if "You are DR. STRANGE" in system:
            return wire("First back up, then migrate, then verify.")
        raise AssertionError(f"unexpected prompt: {system[:60]}")

    endpoint.behave = behave
    response = await ask(http, "what should I prepare for the migration weekend")

    assert response["responding_agent"] == "JARVIS" and "backing up first" in response["speech"]
    assert chain(response) == ["JARVIS", "DR_STRANGE"]                      # the real hand-off is visible
    assert any("delegated to DR_STRANGE" in s["detail"] for s in response["trace"])
    assert all(h == f"Bearer {KEY}" for h in endpoint.auth_headers)          # the key reaches only the model host
    assert len(endpoint.requests) == 4                                       # route, JARVIS, DR. STRANGE, JARVIS

    status = (await http.get("/api/system/status")).json()
    assert status["llm"]["enabled"] and status["llm"]["calls"] == 4 and status["llm"]["budget"]["used"] > 0
    assert KEY not in json.dumps(status)

    metrics = (await http.get("/metrics", headers=METRICS)).text
    assert 'jarvis_llm_requests_total{model="openai/gpt-oss-20b:fastest",outcome="ok"} 1.0' in metrics
    assert "jarvis_llm_tokens_total" in metrics and "jarvis_llm_budget_used_ratio" in metrics
    assert KEY not in metrics


async def test_conversation_is_remembered_across_turns(stack):
    http, endpoint = stack
    endpoint.behave = lambda body: route_json("VISION") if "You route" in system_of(body) else wire("Noted.")
    await ask(http, "ask vision to analyse the sales trend")
    await ask(http, "ask vision to compare it with last year")
    last = endpoint.requests[-1]["messages"]
    assert [m["role"] for m in last[1:]] == ["user", "assistant", "user"]
    assert "sales trend" in last[1]["content"] and "last year" in last[-1]["content"]


async def test_device_tools_still_go_through_the_policy_gate_and_need_approval(stack):
    http, endpoint = stack

    def behave(body):
        if has_tool_result(body):
            return wire("I've asked to open notepad; it needs your approval.")
        return wire("", [wire_call("open_app", {"app": "notepad"})])

    endpoint.behave = behave
    response = await ask(http, "ask friday to open notepad")
    assert response["responding_agent"] == "FRIDAY"
    assert response["pending_approval_id"] and response["tool_requests"] == []   # nothing was auto-dispatched
    approvals = (await http.get("/api/approvals")).json()
    assert [(a["agent_id"], a["status"]) for a in approvals] == [("FRIDAY", "PENDING")]


async def test_a_dangerous_command_from_the_model_is_blocked_not_queued(stack):
    http, endpoint = stack
    endpoint.behave = lambda body: (wire("Done.") if has_tool_result(body)
                                    else wire("", [wire_call("run_command", {"command": "rm -rf /"})]))
    response = await ask(http, "ask tony to clean up everything")
    assert "blocked by security policy" in response["speech"]
    assert response["tool_requests"] == [] and response["pending_approval_id"] is None
    assert (await http.get("/api/approvals")).json() == []


async def test_a_model_trying_to_reach_a_tool_it_does_not_have_gets_nothing(stack):
    http, endpoint = stack
    endpoint.behave = lambda body: (wire("I can't run commands.") if has_tool_result(body)
                                    else wire("", [wire_call("run_command", {"command": "whoami"})]))
    response = await ask(http, "ask friday to run whoami")                       # FRIDAY has no terminal tool
    assert response["tool_requests"] == [] and response["pending_approval_id"] is None
    tool_msgs = [m for m in endpoint.requests[-1]["messages"] if m["role"] == "tool"]
    assert "do not have a tool named 'run_command'" in tool_msgs[0]["content"]


async def test_out_of_credits_degrades_gracefully_and_stops_hammering_the_service(stack):
    http, endpoint = stack
    endpoint.status = 402
    first = await ask(http, "what is the plan for tomorrow")
    assert "out of credits" in first["speech"]                                   # honest, spoken reason
    hits_after_first = len(endpoint.requests)
    second = await ask(http, "ask vision to analyse this")
    assert "out of credits" in second["speech"]                                  # breaker open, reason kept
    assert len(endpoint.requests) == hits_after_first                            # no further network calls

    status = (await http.get("/api/system/status")).json()["llm"]
    assert status["circuit"] == "open" and status["last_error"]["kind"] in ("credits", "circuit_open")


async def test_rule_based_fast_paths_never_spend_tokens(stack):
    http, endpoint = stack
    endpoint.behave = lambda body: (_ for _ in ()).throw(AssertionError("model must not be called"))
    response = await ask(http, "ask wong to remember that my locker code is on the fridge")
    assert response["responding_agent"] == "WONG" and "Noted" in response["speech"]
    assert endpoint.requests == []
