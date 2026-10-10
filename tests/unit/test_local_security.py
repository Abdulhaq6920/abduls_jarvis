"""The local agent's own security boundary: it must hold even if the backend is hostile."""
import subprocess

import pytest
from jarvis_local_agent.backend_client import BackendClient, BackendError
from jarvis_local_agent.config import RuntimeConfig
from jarvis_local_agent.runtime import summarize_result
from jarvis_local_agent.security import LocalSecurityValidator
from jarvis_local_agent.tools import ToolExecutor, child_environment
from jarvis_shared import ToolRequest, ToolResult


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "notes.txt").write_text("hello")
    return root


@pytest.fixture
def validator(project):
    return LocalSecurityValidator([str(project)], ["notepad", "code"])


def req(category, action, **args) -> ToolRequest:
    return ToolRequest(request_id="r1", agent_id="TONY", category=category, action=action, args=args)


# ------------------------------------------------------------------------ terminal
def test_safe_command_in_allowed_dir_runs_without_approval(validator, project):
    v = validator.validate(req("terminal", "run_command", command="git status", cwd=str(project)))
    assert v.ok and not v.needs_approval and v.argv == ["git", "status"]


@pytest.mark.parametrize("command", ["rm -rf /", "del /s /q C:\\", "powershell -c whoami",
                                     "git status && rm x", "echo hi > f", "format C:"])
def test_dangerous_commands_are_hard_blocked_even_when_approved(validator, project, command):
    v = validator.validate(req("terminal", "run_command", command=command, cwd=str(project)))
    assert not v.ok
    assert v.allows(approved=True) is False   # a human grant must NOT unlock a hard block


def test_restricted_command_needs_approval_then_runs(validator, project):
    v = validator.validate(req("terminal", "run_command", command="pip install requests", cwd=str(project)))
    assert v.ok and v.needs_approval
    assert not v.allows(approved=False) and v.allows(approved=True)


def test_unknown_program_is_not_trusted(validator, project):
    v = validator.validate(req("terminal", "run_command", command="mystery-tool --go", cwd=str(project)))
    assert v.needs_approval and not v.allows(approved=False)


def test_working_directory_outside_allowed_paths_is_blocked(validator, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert not validator.validate(req("terminal", "run_command", command="git status", cwd=str(outside))).ok


def test_command_without_cwd_is_blocked(validator):
    assert not validator.validate(req("terminal", "run_command", command="git status")).ok


def test_path_traversal_out_of_the_project_is_blocked(validator, project):
    sneaky = str(project / ".." / ".." / "Windows")
    assert not validator.validate(req("terminal", "run_command", command="git status", cwd=sneaky)).ok
    assert not validator.validate(req("filesystem", "read_file", path=str(project / ".." / "x"))).ok


# ----------------------------------------------------------------------- filesystem
def test_filesystem_rules(validator, project):
    assert validator.validate(req("filesystem", "read_file", path=str(project / "notes.txt"))).allows(False)
    assert validator.validate(req("filesystem", "write_file", path=str(project / "n.txt"))).needs_approval
    assert validator.validate(req("filesystem", "delete_file", path=str(project / "n.txt"))).needs_approval
    assert not validator.validate(req("filesystem", "read_file", path="C:\\Windows\\System32\\config\\SAM")).ok
    assert not validator.validate(req("filesystem", "explode", path=str(project))).ok


# ---------------------------------------------------------------------- application
@pytest.mark.parametrize("app", [r"C:\evil\notepad.exe", "..\\notepad", "/tmp/notepad", "evil", "", "notepad.exe\\x"])
def test_applications_must_be_named_and_allow_listed_not_paths(validator, app):
    """Regression: the old check used Path(app).stem, so C:\\evil\\notepad.exe passed."""
    assert not validator.validate(req("application", "open_app", app=app)).ok


def test_allow_listed_app_still_needs_approval(validator):
    v = validator.validate(req("application", "open_app", app="notepad"))
    if v.ok:                      # notepad exists on Windows; elsewhere it is "not found"
        assert v.needs_approval


# ----------------------------------------------------------------------- structure
def test_unknown_category_and_malformed_requests_are_blocked(validator):
    assert not validator.validate(req("rootkit", "install")).ok
    assert not validator.validate(ToolRequest(request_id="r", agent_id="X", category="terminal", action="")).ok


def test_keyboard_mouse_and_outbound_actions_always_need_approval(validator):
    for r in (req("keyboard", "type", text="x"), req("mouse", "click"), req("gmail", "send_email")):
        assert validator.validate(r).needs_approval


# ------------------------------------------------------------------------ executor
def test_executor_really_runs_git_status_in_a_repo(validator, project):
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    tool = req("terminal", "run_command", command="git status --short", cwd=str(project))
    verdict = validator.validate(tool)
    assert verdict.allows(False)
    result = ToolExecutor([str(project)]).execute(tool, verdict)
    assert result.ok and "notes.txt" in result.output     # the untracked file we created
    assert "changed file" in summarize_result(tool, result)


def test_executor_builtins_and_missing_programs(validator, project):
    ex = ToolExecutor([str(project)])
    listing = req("terminal", "run_command", command="dir", cwd=str(project))
    assert "notes.txt" in ex.execute(listing, validator.validate(listing)).output
    ghost = req("terminal", "run_command", command="no-such-program-xyz --v", cwd=str(project))
    verdict = validator.validate(ghost)
    out = ToolExecutor([str(project)]).execute(ghost, verdict)
    assert not out.ok and "not found" in out.error


def test_child_processes_never_inherit_secrets(monkeypatch):
    monkeypatch.setenv("JARVIS_DEVICE_SECRET", "super-secret-device-value")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-super-secret")
    monkeypatch.setenv("MY_API_KEY", "key-123")
    monkeypatch.setenv("HARMLESS", "ok")
    env = child_environment()
    assert "HARMLESS" in env and "PATH" in env or "Path" in env
    assert not {"JARVIS_DEVICE_SECRET", "AWS_SECRET_ACCESS_KEY", "MY_API_KEY"} & set(env)


def test_executor_output_is_scrubbed_and_capped(project):
    ex = ToolExecutor([str(project)])
    (project / "leak.txt").write_text("token=abcdef123456 " + "x" * 10_000)
    out = ex.execute(req("filesystem", "read_file", path=str(project / "leak.txt")),
                     LocalSecurityValidator([str(project)], []).validate(
                         req("filesystem", "read_file", path=str(project / "leak.txt"))))
    assert "abcdef123456" not in out.output and len(out.output) <= 4000


def test_executor_refuses_paths_outside_roots_on_its_own(project, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    tool = req("filesystem", "read_file", path=str(outside))
    ok_verdict = LocalSecurityValidator([str(tmp_path)], []).validate(tool)   # a (hypothetically) lax validator
    assert not ToolExecutor([str(project)]).execute(tool, ok_verdict).ok       # executor still says no


def test_summaries_never_claim_success_on_failure():
    tool = req("terminal", "run_command", command="git status")
    assert summarize_result(tool, ToolResult(request_id="r", ok=False, error="exit code 128")).startswith("That didn't work")


# --------------------------------------------------------------------- transport
def test_credentials_are_refused_over_plain_http_to_remote_hosts():
    with pytest.raises(BackendError):
        BackendClient(RuntimeConfig(backend_url="http://jarvis.example.com", _env_file=None))
    BackendClient(RuntimeConfig(backend_url="http://127.0.0.1:8077", _env_file=None))   # loopback is fine
    BackendClient(RuntimeConfig(backend_url="https://jarvis.example.com", _env_file=None))
    BackendClient(RuntimeConfig(backend_url="http://10.0.0.5", allow_insecure_backend=True, _env_file=None))


def test_device_secret_does_not_leak_via_repr():
    cfg = RuntimeConfig(device_id="d", device_secret="hunter2-secret-value", _env_file=None)
    assert "hunter2" not in repr(cfg) and "hunter2" not in str(cfg.model_dump())
