"""Security contracts: tool requests, risk levels, command categories.

These cross the local-agent <-> backend boundary. The *local* agent is the final
authority: it re-validates every ToolRequest against its own policy and may reject
even a request the backend approved.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    RESTRICTED = "RESTRICTED"
    DANGEROUS = "DANGEROUS"


class CommandCategory(str, Enum):
    """Coarse classification of a requested shell command."""

    SAFE = "SAFE"
    RESTRICTED = "RESTRICTED"
    DANGEROUS = "DANGEROUS"
    UNKNOWN = "UNKNOWN"


class ToolRequest(BaseModel):
    """A request from an agent/LLM to perform a local operation.

    The LLM never gets raw shell/fs/input control; it can only emit ToolRequests of a
    known `category` with structured `args`. The local validator decides whether it
    runs, is blocked, or needs approval.
    """

    request_id: str = Field(min_length=1, max_length=64)
    agent_id: str = Field(max_length=32)
    category: str = Field(max_length=32)  # filesystem/terminal/application/...
    action: str = Field(max_length=64)    # e.g. "read_file", "run_command", "open_app"
    args: Dict[str, Any] = Field(default_factory=dict)
    # Declared risk from the backend; the local validator recomputes and takes the max.
    declared_risk: RiskLevel = RiskLevel.SAFE
    requires_approval: bool = False
    allowed_paths: List[str] = Field(default_factory=list)
    allowed_apps: List[str] = Field(default_factory=list)
    reason: str = ""


class ToolResult(BaseModel):
    """Outcome of a local tool execution (or a rejection)."""

    request_id: str
    ok: bool
    output: str = ""
    error: str = ""
    risk_level: RiskLevel = RiskLevel.SAFE
    blocked: bool = False
    needs_approval: bool = False
    approval_id: Optional[str] = None
