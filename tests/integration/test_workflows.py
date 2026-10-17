"""Agents, memory, approvals and observability, end to end through HTTP."""
import pytest

from tests.conftest import bearer


def say(client, headers, text, session="t"):
    r = client.post("/api/assist", json={"session_id": session, "utterance": text}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------- routing
def test_explicit_delegation_and_voice(client, admin):
    r = say(client, admin, "Hey JARVIS, ask SHURI to research LangGraph")
    assert r["responding_agent"] == "SHURI" and r["delegation_note"] == "SHURI will handle that."
    stages = [t["stage"] for t in r["trace"]]
    assert stages[0] == "OBSERVE" and stages[-1] == "REPORT"
    assert {"PLAN", "PROPOSE", "APPROVE", "EXECUTE", "VERIFY"} <= set(stages)


def test_agents_never_claim_work_they_did_not_do(client, admin):
    """Honesty: with search disabled / Gmail / LLM not connected, the agents must say so."""
    # SHURI's search is wired but DisabledSearch by default: it must say it found nothing /
    # isn't configured, never fabricate a finding or URL.
    shuri = say(client, admin, "ask SHURI to research agent security")["speech"]
    assert "nothing" in shuri.lower() or "not configured" in shuri.lower()
    assert "isn't connected" in say(client, admin, "check my Gmail")["speech"]
    # VISION now answers via the default offline EchoLLM: a real (if deterministic) reply,
    # never the old "no language model" stub.
    vision = say(client, admin, "ask VISION to analyse this")["speech"]
    assert vision and "no language model" not in vision.lower()


def test_word_boundary_routing(client, admin):
    assert say(client, admin, "open notepad")["responding_agent"] == "FRIDAY"  # not WONG via "note"


# ---------------------------------------------------------------------- agents page API
def test_agents_listing(client, admin):
    agents = client.get("/api/agents", headers=admin).json()
    assert len(agents) == 10
    jarvis = next(a for a in agents if a["spec"]["id"] == "JARVIS")
    assert jarvis["can_disable"] is False and jarvis["enabled"] is True
    assert all(a["voice"]["voice_id"] for a in agents)


def test_voice_edit_is_persisted_validated_and_resettable(client, admin):
    ok = client.put("/api/agents/shuri/voice", headers=admin, json={"pitch": 1.4, "speed": 1.2, "style": "bright"})
    assert ok.status_code == 200 and ok.json()["pitch"] == 1.4
    shown = next(a for a in client.get("/api/agents", headers=admin).json() if a["spec"]["id"] == "SHURI")
    assert shown["voice"]["style"] == "bright"
    assert client.put("/api/agents/SHURI/voice", headers=admin, json={"pitch": 9}).status_code == 422
    assert client.put("/api/agents/SHURI/voice", headers=admin, json={"voice_id": "bad id!"}).status_code == 422
    reset = client.delete("/api/agents/SHURI/voice", headers=admin).json()
    assert reset["pitch"] == 1.12 and reset["voice_id"] == "shuri_default"


def test_disabling_an_agent_reroutes_to_jarvis(client, admin):
    assert client.put("/api/agents/SHURI/enabled", headers=admin, json={"enabled": False}).status_code == 200
    r = say(client, admin, "ask SHURI to research tea")
    assert r["responding_agent"] == "JARVIS" and "SHURI is currently disabled" in r["speech"]
    assert client.put("/api/agents/JARVIS/enabled", headers=admin, json={"enabled": False}).status_code == 409
    client.put("/api/agents/SHURI/enabled", headers=admin, json={"enabled": True})
    assert say(client, admin, "ask SHURI to research tea")["responding_agent"] == "SHURI"


def test_unknown_agent_is_404_and_agent_role_cannot_edit(client, admin, agent):
    assert client.get("/api/agents/NOPE/activity", headers=admin).status_code == 404
    assert client.put("/api/agents/SHURI/voice", headers=agent, json={"pitch": 1.1}).status_code == 403
    assert client.get("/api/voices", headers=agent).status_code == 200  # the runtime needs to read voices


def test_agent_activity_and_stats(client, admin):
    say(client, admin, "ask TONY to check my project")
    say(client, admin, "ask TONY to check my project")
    tony = next(a for a in client.get("/api/agents", headers=admin).json() if a["spec"]["id"] == "TONY")
    assert tony["stats"]["turns"] == 2 and tony["stats"]["avg_latency_ms"] is not None
    assert len(client.get("/api/agents/TONY/activity", headers=admin).json()) == 2


# ---------------------------------------------------------------------------- memory
def test_remember_and_recall_by_voice_persist_encrypted(client, admin):
    assert "Noted" in say(client, admin, "Hey JARVIS, remember that my car is blue")["speech"]
    recalled = say(client, admin, "ask WONG what do you remember about my car")
    assert recalled["responding_agent"] == "WONG" and "blue" in recalled["speech"]
    assert "nothing stored" in say(client, admin, "ask WONG what do you remember about dragons")["speech"] \
        or "anything stored" in say(client, admin, "ask WONG what do you remember about dragons")["speech"]


def test_memory_crud_and_guarded_wipe(client, admin, agent):
    created = client.post("/api/memory", headers=admin, json={"text": "Dentist on Friday", "tags": ["health"]})
    assert created.status_code == 201
    assert client.get("/api/memory?q=dentist", headers=admin).json()[0]["text"] == "Dentist on Friday"
    assert client.get("/api/memory", headers=agent).status_code == 403   # personal data: admin only
    assert client.delete("/api/memory").status_code == 401
    assert client.delete("/api/memory", headers=admin).status_code == 400  # needs explicit confirm
    assert client.delete(f"/api/memory/{created.json()['id']}", headers=admin).status_code == 204
    assert client.delete("/api/memory/not-real", headers=admin).status_code == 404


def test_conversation_history_reaches_the_agents_context(client, admin):
    say(client, admin, "hello there", session="convo")
    seen = []
    from jarvis_backend.agents import runtime
    original = runtime.JarvisAgent.handle

    async def spy(self, task, context):
        seen.append(context["history"])
        return await original(self, task, context)
    runtime.JarvisAgent.handle = spy
    try:
        say(client, admin, "and again", session="convo")
    finally:
        runtime.JarvisAgent.handle = original
    assert seen[0] and seen[0][0]["user"] == "hello there"


# --------------------------------------------------------------------------- approvals
def trigger_email_approval(client, headers):
    r = say(client, headers, "ask NATASHA to send a reply")
    assert r["pending_approval_id"] and "approval" in r["speech"].lower()
    return r["pending_approval_id"]


def test_dangerous_actions_are_blocked_not_queued(client, admin):
    from jarvis_backend.core.policy import PolicyEngine
    from jarvis_shared import ToolRequest
    verdict = PolicyEngine().evaluate(ToolRequest(request_id="1", agent_id="TONY", category="terminal",
                                                  action="run_command", args={"command": "rm -rf C:\\"}))
    assert verdict.allow is False and verdict.requires_approval is False


def test_safe_tool_requests_are_returned_for_local_execution(client, admin):
    r = say(client, admin, "ask TONY to check my project")
    assert r["pending_approval_id"] is None
    tool = r["tool_requests"][0]
    assert tool["category"] == "terminal" and tool["args"]["command"] == "git status --short"


def test_full_approval_lifecycle_with_separation_of_duties(client, admin, agent):
    approval_id = trigger_email_approval(client, agent)  # the local agent triggered it
    base = f"/api/approvals/{approval_id}"

    # The agent can see work to do only AFTER a human grants it, and can never grant it itself.
    assert client.get("/api/approvals/granted", headers=agent).json() == []
    assert client.post(f"{base}/decision", headers=agent, json={"granted": True}).status_code == 403
    assert client.post(f"{base}/claim", headers=agent).status_code == 409          # not granted yet
    assert client.post(f"{base}/claim", headers=admin).status_code == 403          # humans don't execute

    pending = client.get("/api/approvals?status=PENDING", headers=admin).json()
    assert pending[0]["id"] == approval_id and "send email" in pending[0]["summary"]

    assert client.post(f"{base}/decision", headers=admin, json={"granted": True}).json()["status"] == "GRANTED"
    assert client.post(f"{base}/decision", headers=admin, json={"granted": False}).status_code == 409  # one decision only

    work = client.get("/api/approvals/granted", headers=agent).json()
    assert [w["id"] for w in work] == [approval_id] and work[0]["request"]["action"] == "send_email"

    assert client.post(f"{base}/claim", headers=agent).json()["status"] == "EXECUTING"
    assert client.post(f"{base}/claim", headers=agent).status_code == 409          # cannot execute twice
    done = client.post(f"{base}/result", headers=agent, json={"ok": True, "output": "sent"})
    assert done.json()["status"] == "EXECUTED"
    assert client.post(f"{base}/result", headers=agent, json={"ok": True}).status_code == 409


def test_denied_approval_can_never_be_executed(client, admin, agent):
    approval_id = trigger_email_approval(client, admin)
    client.post(f"/api/approvals/{approval_id}/decision", headers=admin, json={"granted": False})
    assert client.post(f"/api/approvals/{approval_id}/claim", headers=agent).status_code == 409
    assert client.get("/api/approvals/granted", headers=agent).json() == []


def test_approval_decisions_are_attributed_in_the_audit_log(client, admin):
    approval_id = trigger_email_approval(client, admin)
    client.post(f"/api/approvals/{approval_id}/decision", headers=admin, json={"granted": True})
    entries = client.get("/api/audit?event=approval_granted", headers=admin).json()
    assert entries and entries[0]["data"]["by"] and entries[0]["data"]["approval_id"] == approval_id


# ---------------------------------------------------------------------- observability
def test_audit_chain_verifies_and_stores_no_utterance_text(client, admin):
    say(client, admin, "remember that the vault code is 000-SECRET-111")
    verified = client.get("/api/audit/verify", headers=admin).json()
    assert verified["ok"] is True and verified["checked"] >= 4  # started, login, observe, plan, report...
    events = {e["event"] for e in client.get("/api/audit?limit=500", headers=admin).json()}
    assert {"observe", "plan", "report", "login_succeeded"} <= events
    assert "SECRET" not in client.get("/api/audit?limit=500", headers=admin).text


def test_status_reports_runtime_memory_cache_and_findings(client, admin):
    status = client.get("/api/system/status", headers=admin).json()
    rt = status["runtime"]
    assert status["database_ok"] and status["agents"] == 10
    assert rt["process"]["rss_mb"] > 0 and rt["uptime_seconds"] >= 0
    assert rt["cache"]["backend"] == "local" and rt["cache"]["local_items"] <= rt["cache"]["local_item_cap"]
    assert rt["event_loop_lag_ms"] >= 0 and isinstance(status["config_findings"], list)


def test_metrics_need_their_own_token_and_expose_key_series(client, admin):
    say(client, admin, "hello")
    assert client.get("/metrics", headers=admin).status_code == 401   # a user JWT is not a metrics token
    body = client.get("/metrics", headers=bearer("metrics-token-for-tests-0123456789")).text
    for series in ("jarvis_http_requests_total", "jarvis_process_rss_bytes", "jarvis_event_loop_lag_seconds",
                   "jarvis_cache_fallback_active", "jarvis_pending_approvals", "jarvis_assist_seconds"):
        assert series in body, series
    assert "utterance" not in body and "hello" not in body.replace("jarvis_", "")


def test_runtime_heartbeat_flows_from_agent_to_dashboard(client, admin, agent):
    assert client.get("/api/runtime", headers=admin).json() == {"connected": False}
    hb = {"conversation_state": "LISTENING", "microphone_paused": False, "wake_word": "hey jarvis"}
    assert client.post("/api/runtime/heartbeat", headers=agent, json=hb).status_code == 204
    state = client.get("/api/runtime", headers=admin).json()
    assert state["connected"] is True and state["conversation_state"] == "LISTENING"
    assert client.post("/api/runtime/heartbeat", headers=admin, json=hb).status_code == 403


def test_websocket_requires_a_valid_admin_token_in_the_first_message(client, admin, agent):
    from starlette.websockets import WebSocketDisconnect
    token = admin["Authorization"].split()[1]
    with client.websocket_connect("ws://localhost/ws/events") as ws:
        ws.send_json({"token": token})
        assert ws.receive_json()["type"] == "hello"
    for bad in ({"token": "garbage"}, {"token": agent["Authorization"].split()[1]}):  # forged / wrong role
        with client.websocket_connect("ws://localhost/ws/events") as ws:
            ws.send_json(bad)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    with pytest.raises(WebSocketDisconnect):  # hostile Origin (cross-site WebSocket hijack)
        with client.websocket_connect("ws://localhost/ws/events", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_json()
