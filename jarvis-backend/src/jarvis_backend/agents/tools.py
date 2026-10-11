"""Tools an LLM-driven agent may call, with strict argument schemas.

Two kinds, and the difference is the whole security story:

* **internal** tools (recall, remember, delegate) run inside the backend, against our own
  stores, and return text to the model;
* **device** tools (open_app, list_dir, read_file, write_file, run_command) are *never executed
  here*. Calling one only produces a :class:`ToolRequest` that must clear the backend policy
  gate, optional human approval, and the device's own validator. The model is told the request
  was queued, not that it ran.

Arguments from the model are untrusted input: they are validated against the schema (type,
length, enum, no unknown keys) before anything else looks at them.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from jarvis_shared.agents import AgentId
from jarvis_shared.security import RiskLevel, ToolRequest

from jarvis_backend.llm.types import ToolSpec

Kind = Literal["internal", "device"]


@dataclass(frozen=True)
class AgentTool:
    spec: ToolSpec
    kind: Kind
    category: str = ""
    action: str = ""
    risk: RiskLevel = RiskLevel.SAFE
    needs_approval: bool = False


def _obj(properties: Dict[str, Any], required: Tuple[str, ...]) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


def _S(desc: str, maxlen: int = 500, minlen: int = 1) -> Dict[str, Any]:
    return {"type": "string", "description": desc, "minLength": minlen, "maxLength": maxlen}

_DELEGATE_TARGETS = [a.value for a in AgentId if a is not AgentId.JARVIS]

TOOLS: Dict[str, AgentTool] = {t.spec.name: t for t in (
    AgentTool(ToolSpec("recall", "Search the user's saved long-term memories for facts related to a query.",
                       _obj({"query": _S("What to look for.", 200)}, ("query",))), "internal"),
    AgentTool(ToolSpec("remember", "Save one short fact the user asked you to remember.",
                       _obj({"fact": _S("The fact, in one sentence.", 500)}, ("fact",))), "internal"),
    AgentTool(ToolSpec("delegate",
                       "Hand a self-contained sub-task to a specialist agent and receive its answer. "
                       "Use only when a specialist is clearly better placed than you.",
                       _obj({"agent": {"type": "string", "enum": _DELEGATE_TARGETS,
                                       "description": "Which specialist."},
                             "task": _S("The sub-task, fully self-contained.", 500)}, ("agent", "task"))),
              "internal"),
    AgentTool(ToolSpec("open_app", "Ask to open an application on the user's PC.",
                       _obj({"app": _S("Program name, e.g. notepad.", 40)}, ("app",))),
              "device", "application", "open_app", RiskLevel.RESTRICTED),
    AgentTool(ToolSpec("list_dir", "Ask to list a folder on the user's PC.",
                       _obj({"path": _S("Folder path.", 260)}, ("path",))),
              "device", "filesystem", "list_dir", RiskLevel.SAFE),
    AgentTool(ToolSpec("read_file", "Ask to read a text file on the user's PC.",
                       _obj({"path": _S("File path.", 260)}, ("path",))),
              "device", "filesystem", "read_file", RiskLevel.SAFE),
    AgentTool(ToolSpec("write_file", "Ask to create or overwrite a text file on the user's PC. Needs approval.",
                       _obj({"path": _S("File path.", 260), "content": _S("Full file content.", 20_000, 0)},
                            ("path", "content"))),
              "device", "filesystem", "write_file", RiskLevel.RESTRICTED, True),
    AgentTool(ToolSpec("run_command", "Ask to run one terminal command on the user's PC. Risky commands are blocked or need approval.",
                       _obj({"command": _S("The command line.", 300),
                             "cwd": _S("Working folder (optional).", 260)}, ("command",))),
              "device", "terminal", "run_command", RiskLevel.SAFE),
)}

# Least privilege: an agent sees only the tools its job needs.
AGENT_TOOLS: Dict[AgentId, Tuple[str, ...]] = {
    AgentId.JARVIS: ("delegate", "recall", "remember"),
    AgentId.FRIDAY: ("open_app", "list_dir", "read_file", "write_file"),
    AgentId.TONY: ("run_command", "list_dir", "read_file", "write_file"),
    AgentId.WONG: ("recall", "remember"),
    AgentId.VISION: ("recall",),
    AgentId.DR_STRANGE: ("recall",),
    AgentId.NICK_FURY: (),
    AgentId.SHURI: ("recall",),       # web tools arrive with WP-F1
    AgentId.NATASHA: ("recall",),     # mail/calendar tools arrive with WP-F4 / WP-F5
    AgentId.QUICKSILVER: ("recall",),
}


def tools_for(agent: AgentId) -> List[AgentTool]:
    return [TOOLS[name] for name in AGENT_TOOLS.get(agent, ())]


def validate_arguments(schema: Mapping[str, Any], args: Any) -> Optional[str]:
    """Return None if ``args`` satisfies the (small subset of) JSON Schema we use, else a reason."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    props: Mapping[str, Any] = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in args:
            return f"missing required argument '{name}'"
    for name, value in args.items():
        rule = props.get(name)
        if rule is None:
            return f"unknown argument '{name}'"
        if rule.get("type") == "string":
            if not isinstance(value, str):
                return f"'{name}' must be a string"
            if len(value) < rule.get("minLength", 0):
                return f"'{name}' is too short"
            if len(value) > rule.get("maxLength", 10**9):
                return f"'{name}' is too long (max {rule['maxLength']} characters)"
            if "enum" in rule and value not in rule["enum"]:
                return f"'{name}' must be one of: {', '.join(rule['enum'])}"
            if "\x00" in value:
                return f"'{name}' contains an invalid character"
    return None


def build_tool_request(tool: AgentTool, args: Mapping[str, Any], agent: AgentId,
                       context: Mapping[str, Any]) -> ToolRequest:
    """Turn a validated device-tool call into a ToolRequest for the policy gate."""
    clean = dict(args)
    if not clean.get("cwd"):
        clean.pop("cwd", None)  # an empty working folder means "not given"
    return ToolRequest(
        request_id=str(uuid.uuid4()), agent_id=agent.value, category=tool.category, action=tool.action,
        args=clean, declared_risk=tool.risk, requires_approval=tool.needs_approval,
        allowed_paths=[str(p) for p in context.get("allowed_paths") or []],
        allowed_apps=[str(a) for a in context.get("allowed_apps") or []],
        reason=f"Requested by {agent.value} through its language model.",
    )
