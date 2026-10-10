"""End to end: dashboard user + backend + local runtime, with REAL command execution.

The local runtime talks to the backend through an in-process ASGI transport (the same app,
real auth, real database), and runs real subprocesses. Nothing about the security path is
mocked.
"""
import asyncio
import subprocess

import httpx
import pytest
from jarvis_backend.app import create_app
from jarvis_backend.auth.passwords import hash_password
from jarvis_backend.container import Container
from jarvis_local_agent.backend_client import BackendClient
from jarvis_local_agent.config import RuntimeConfig
from jarvis_local_agent.runtime import JarvisRuntime
from jarvis_shared import AssistRequest, ToolRequest

from tests.conftest import ADMIN_PASSWORD, ADMIN_USER


class SilentTTS:
    def __init__(self): self.said = []
    async def synthesize(self, text, voice_id, settings):
        from jarvis_shared.audio import AudioResult
        return AudioResult(voice_id=voice_id, spoken_text=text)
    def speak(self, result): self.said.append((result.voice_id, result.spoken_text))
    def stop(self): pass


class NoWake:
    name, paused, suspended = "none", False, False
    def start(self, on_wake): pass
    def stop(self): pass
    def pause(self): pass
    def resume(self): pass


@pytest.fixture
async def world(settings, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "readme.md").write_text("hi")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        c: Container = app.state.container
        await c.auth.users.create(ADMIN_USER, await hash_password(ADMIN_PASSWORD))
        device, secret = await c.auth.devices.create("e2e-pc", "test")

        transport = httpx.ASGITransport(app=app)
        human = httpx.AsyncClient(transport=transport, base_url="http://localhost")
        login = await human.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
        human.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

        config = RuntimeConfig(device_id=device.id, device_secret=secret, allowed_paths=[str(project)],
                               allowed_apps=["notepad"], approval_poll_seconds=0.05,
                               heartbeat_seconds=60, data_dir=tmp_path / "agent", _env_file=None)
        backend = BackendClient(config)
        backend._http = httpx.AsyncClient(transport=transport, base_url="http://localhost")
        tts = SilentTTS()
        runtime = JarvisRuntime(config, NoWake(), None, tts, backend, capture_utterance=lambda: "")
        runtime._loop = asyncio.get_running_loop()
        runtime._backend_online = True
        yield c, human, runtime, tts, project
        await runtime.shutdown()
        await human.aclose()


def request(text):
    return AssistRequest(session_id="e2e", utterance=text)


async def test_safe_command_really_runs_on_the_machine_and_is_spoken(world):
    _, _, runtime, tts, project = world
    (project / "new.txt").write_text("x")
    resp = await runtime.backend.assist(AssistRequest(
        session_id="e2e", utterance="ask TONY to check my project",
        client_context={"allowed_paths": [str(project)], "allowed_apps": []}))
    assert resp.tool_requests and resp.pending_approval_id is None
    outcomes = await runtime._run_local_tools(resp.tool_requests, approved=False)
    tool, result = outcomes[0]
    assert result.ok and "new.txt" in result.output     # REAL git ran in the REAL directory


async def test_approved_action_executes_only_after_a_human_grants_it(world):
    c, human, runtime, tts, project = world
    # An agent proposes a restricted command; the backend queues it instead of running it.
    tool = ToolRequest(request_id="t1", agent_id="TONY", category="terminal", action="run_command",
                       args={"command": "pip --version", "cwd": str(project)})
    approval = await c.approvals.create("TONY", "TONY wants to run `pip --version`", "RESTRICTED",
                                        tool.model_dump(mode="json"))

    task = asyncio.create_task(runtime._approval_loop())
    await asyncio.sleep(0.4)
    assert (await c.approvals.get(approval.id)).status == "PENDING"      # not executed: no grant yet

    r = await human.post(f"/api/approvals/{approval.id}/decision", json={"granted": True})
    assert r.status_code == 200
    # The runtime records the outcome with the backend FIRST and announces it second, so wait for
    # both conditions we assert on rather than stopping at the first.
    for _ in range(200):
        await asyncio.sleep(0.05)
        executed = (await c.approvals.get(approval.id)).status in ("EXECUTED", "FAILED")
        if executed and tts.said:
            break
    task.cancel()

    done = await c.approvals.get(approval.id)
    assert done.status == "EXECUTED" and "pip" in done.result["output"].lower()
    assert tts.said and "Approved action finished" in tts.said[-1][1]


async def test_denied_action_never_runs(world):
    c, human, runtime, tts, project = world
    marker = project / "should-not-exist.txt"
    tool = ToolRequest(request_id="t2", agent_id="FRIDAY", category="filesystem", action="write_file",
                       args={"path": str(marker), "content": "boom"})
    approval = await c.approvals.create("FRIDAY", "write a file", "RESTRICTED", tool.model_dump(mode="json"))
    await human.post(f"/api/approvals/{approval.id}/decision", json={"granted": False})
    task = asyncio.create_task(runtime._approval_loop())
    await asyncio.sleep(0.4)
    task.cancel()
    assert not marker.exists() and (await c.approvals.get(approval.id)).status == "DENIED"


async def test_a_hostile_backend_cannot_make_the_local_agent_run_dangerous_things(world):
    """Even a GRANTED approval for `rm -rf` is refused locally: a grant cannot unlock a hard block."""
    c, human, runtime, tts, project = world
    evil = ToolRequest(request_id="t3", agent_id="TONY", category="terminal", action="run_command",
                       args={"command": "rm -rf .", "cwd": str(project)})
    approval = await c.approvals.create("TONY", "totally fine", "RESTRICTED", evil.model_dump(mode="json"))
    await human.post(f"/api/approvals/{approval.id}/decision", json={"granted": True})

    task = asyncio.create_task(runtime._approval_loop())
    for _ in range(100):
        await asyncio.sleep(0.05)
        if (await c.approvals.get(approval.id)).status in ("EXECUTED", "FAILED"):
            break
    task.cancel()
    done = await c.approvals.get(approval.id)
    assert done.status == "FAILED" and (project / "readme.md").exists()
    assert "destructive" in done.result["error"].lower() or "shell-level" in done.result["error"].lower()


async def test_runtime_survives_a_revoked_device(world):
    c, human, runtime, tts, project = world
    devices = await c.auth.devices.list()
    await c.auth.revoke_device(devices[0].id, by="test")
    task = asyncio.create_task(runtime._approval_loop())
    await asyncio.sleep(0.3)
    assert not task.done()          # the loop keeps retrying quietly instead of crashing
    task.cancel()


async def test_heartbeat_reaches_the_dashboard(world):
    c, human, runtime, *_ = world
    runtime.state = runtime.state.__class__("LISTENING")
    await runtime._beat()
    state = (await human.get("/api/runtime")).json()
    assert state["connected"] and state["conversation_state"] == "LISTENING"
