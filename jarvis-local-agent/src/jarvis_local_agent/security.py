"""Local security validator -- the real security boundary.

Every tool request flows: agent -> backend policy (gate 1) -> THIS validator (gate 2) ->
executor. The validator is independent of the backend: it re-classifies every request with
the shared classifier and enforces allow-lists that only this machine knows. It can
REJECT something the backend allowed; it never loosens a decision.

``validate`` answers one question: may this run, must a human approve it first, or never?
``approved=True`` means the backend can prove a human granted it; that satisfies
"needs approval" but can NEVER override a hard block (dangerous, out-of-allow-list, malformed).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from jarvis_shared.commands import classify_command
from jarvis_shared.security import CommandCategory, RiskLevel, ToolRequest

_VALID_CATEGORIES = {"filesystem", "terminal", "application", "browser", "screen",
                     "keyboard", "mouse", "gmail", "calendar"}
_READ_ACTIONS = {"read_file", "list_dir"}
_WRITE_ACTIONS = {"write_file", "move_file"}
_MUTATING_INTEGRATION = {"send_email", "post", "delete", "write"}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool                # False => hard block, never executable
    risk: RiskLevel
    needs_approval: bool    # True => executable only with a human grant
    reason: str
    argv: Optional[List[str]] = None  # parsed command, so the executor runs exactly what was judged
    builtin: bool = False

    def allows(self, approved: bool) -> bool:
        return self.ok and (approved or not self.needs_approval)


def _block(reason: str, risk: RiskLevel = RiskLevel.DANGEROUS) -> ValidationResult:
    return ValidationResult(False, risk, False, reason)


class LocalSecurityValidator:
    def __init__(self, allowed_paths: List[str], allowed_apps: List[str]) -> None:
        self.allowed_paths = [Path(p).resolve() for p in allowed_paths]
        self.allowed_apps = {a.lower().removesuffix(".exe") for a in allowed_apps}

    def validate(self, req: ToolRequest) -> ValidationResult:
        if req.category not in _VALID_CATEGORIES:
            return _block(f"Unknown tool category '{req.category}'.")
        if not req.action or not isinstance(req.args, dict):
            return _block("Malformed tool request.")
        if req.category == "terminal":
            return self._terminal(req)
        if req.category == "filesystem":
            return self._filesystem(req)
        if req.category == "application":
            return self._application(req)
        if req.category in {"keyboard", "mouse", "screen"}:
            return ValidationResult(True, RiskLevel.RESTRICTED, True, f"{req.category} control needs approval.")
        if req.action in _MUTATING_INTEGRATION:
            return ValidationResult(True, RiskLevel.RESTRICTED, True, "Outbound or mutating action needs approval.")
        return ValidationResult(True, RiskLevel.SAFE, False, "Read-only integration action.")

    # ------------------------------------------------------------------- terminal
    def _terminal(self, req: ToolRequest) -> ValidationResult:
        assessment = classify_command(str(req.args.get("command", "")))
        if assessment.category == CommandCategory.DANGEROUS:
            return _block(assessment.reason)
        cwd = req.args.get("cwd")
        if cwd is not None and not self._path_allowed(str(cwd)):
            return _block("Working directory is outside the allowed paths.")
        if cwd is None and not assessment.builtin:
            return _block("A working directory inside an allowed path is required.")
        if assessment.category == CommandCategory.SAFE:
            return ValidationResult(True, RiskLevel.SAFE, False, assessment.reason,
                                    argv=assessment.argv, builtin=assessment.builtin)
        return ValidationResult(True, RiskLevel.RESTRICTED, True, assessment.reason, argv=assessment.argv)

    # ----------------------------------------------------------------- filesystem
    def _filesystem(self, req: ToolRequest) -> ValidationResult:
        path = req.args.get("path")
        if not path:
            return _block("Missing path.")
        if not self._path_allowed(str(path)):
            return _block("Path is outside the allowed paths.")
        if req.action in _READ_ACTIONS:
            return ValidationResult(True, RiskLevel.SAFE, False, "Read-only filesystem access.")
        if req.action in _WRITE_ACTIONS:
            return ValidationResult(True, RiskLevel.RESTRICTED, True, "File modification needs approval.")
        if req.action == "delete_file":
            return ValidationResult(True, RiskLevel.DANGEROUS, True, "File deletion needs approval.")
        return _block(f"Unknown filesystem action '{req.action}'.")

    # ---------------------------------------------------------------- application
    def _application(self, req: ToolRequest) -> ValidationResult:
        app = str(req.args.get("app", "")).strip().lower()
        # A bare program name only. A path (C:\evil\notepad.exe) would make the allow-list
        # check on the *name* meaningless, because Windows would run whatever is at the path.
        if not app or any(ch in app for ch in "\\/:") or app.startswith("."):
            return _block("Applications must be named, not given as a path.")
        name = app.removesuffix(".exe")
        if name not in self.allowed_apps:
            return _block(f"'{name}' is not on the allowed applications list.", RiskLevel.RESTRICTED)
        if shutil.which(name) is None:
            return _block(f"'{name}' was not found on this PC.", RiskLevel.RESTRICTED)
        return ValidationResult(True, RiskLevel.RESTRICTED, True, "Launching an app needs approval.")

    # -------------------------------------------------------------------- helpers
    def _path_allowed(self, path: str) -> bool:
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return False
        for root in self.allowed_paths:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False
