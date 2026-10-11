"""Agent tool schemas: strict validation and least-privilege tool sets."""
import pytest
from jarvis_backend.agents.tools import (
    AGENT_TOOLS,
    TOOLS,
    build_tool_request,
    tools_for,
    validate_arguments,
)
from jarvis_shared.agents import AgentId
from jarvis_shared.security import RiskLevel

RECALL = TOOLS["recall"].spec.parameters
WRITE = TOOLS["write_file"].spec.parameters
DELEGATE = TOOLS["delegate"].spec.parameters


@pytest.mark.parametrize("args,expected", [
    ({"query": "car"}, None),
    ({}, "missing required argument 'query'"),
    ({"query": "car", "extra": 1}, "unknown argument 'extra'"),
    ({"query": 5}, "'query' must be a string"),
    ({"query": ""}, "'query' is too short"),
    ({"query": "x" * 201}, "too long"),
    ({"query": "a\x00b"}, "invalid character"),
    ("not a dict", "arguments must be a JSON object"),
])
def test_recall_arguments(args, expected):
    result = validate_arguments(RECALL, args)
    assert (result is None) if expected is None else (expected in (result or ""))


def test_write_file_allows_empty_content_but_not_missing_path():
    assert validate_arguments(WRITE, {"path": "a.txt", "content": ""}) is None
    assert "missing" in validate_arguments(WRITE, {"content": "x"})


def test_delegate_target_is_restricted_to_known_specialists():
    assert validate_arguments(DELEGATE, {"agent": "SHURI", "task": "research x"}) is None
    assert "one of" in validate_arguments(DELEGATE, {"agent": "ROOT", "task": "x"})
    assert "one of" in validate_arguments(DELEGATE, {"agent": "JARVIS", "task": "x"})  # no self-delegation


def test_only_the_supervisor_can_delegate_and_only_operators_touch_the_pc():
    delegators = [a for a, names in AGENT_TOOLS.items() if "delegate" in names]
    assert delegators == [AgentId.JARVIS]
    can_run_commands = [a for a, names in AGENT_TOOLS.items() if "run_command" in names]
    assert can_run_commands == [AgentId.TONY]
    assert "open_app" in AGENT_TOOLS[AgentId.FRIDAY] and "open_app" not in AGENT_TOOLS[AgentId.SHURI]
    assert tools_for(AgentId.NICK_FURY) == []          # the security reviewer has no tools at all
    assert all(name in TOOLS for names in AGENT_TOOLS.values() for name in names)


def test_every_schema_forbids_unknown_keys():
    assert all(t.spec.parameters["additionalProperties"] is False for t in TOOLS.values())


def test_device_tools_become_requests_with_the_right_risk():
    ctx = {"allowed_paths": ["C:/work"], "allowed_apps": ["notepad"]}
    open_app = build_tool_request(TOOLS["open_app"], {"app": "notepad"}, AgentId.FRIDAY, ctx)
    assert (open_app.category, open_app.action, open_app.declared_risk) == ("application", "open_app", RiskLevel.RESTRICTED)
    assert open_app.allowed_apps == ["notepad"] and open_app.agent_id == "FRIDAY"

    write = build_tool_request(TOOLS["write_file"], {"path": "C:/work/a.txt", "content": "x"}, AgentId.TONY, ctx)
    assert write.requires_approval and write.declared_risk == RiskLevel.RESTRICTED and write.allowed_paths == ["C:/work"]

    run = build_tool_request(TOOLS["run_command"], {"command": "git status", "cwd": ""}, AgentId.TONY, ctx)
    assert run.args == {"command": "git status"}        # an empty cwd means "not given"
