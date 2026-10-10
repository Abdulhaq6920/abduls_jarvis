"""Tool executor: runs a request that the LocalSecurityValidator has ALREADY cleared.

Defence in depth: even though the validator cleared it, the executor still
* never uses a shell -- commands run as an argv list the validator produced;
* gives child processes a scrubbed environment, so a spawned program cannot read this
  agent's credentials (``JARVIS_*``) or any other secret-looking variable;
* caps runtime and output size;
* re-checks filesystem paths against the allow-list on its own.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from jarvis_shared.log import scrub
from jarvis_shared.security import RiskLevel, ToolRequest, ToolResult

from jarvis_local_agent.security import ValidationResult

TIMEOUT_SECONDS = 30
MAX_OUTPUT = 4000
_SECRET_NAME = re.compile(r"(token|secret|password|passwd|api[_-]?key|credential|^JARVIS_)", re.I)


def child_environment() -> Dict[str, str]:
    """The current environment minus anything that looks like a secret."""
    return {k: v for k, v in os.environ.items() if not _SECRET_NAME.search(k)}


def _result(req: ToolRequest, ok: bool, output: str = "", error: str = "",
            risk: RiskLevel = RiskLevel.SAFE) -> ToolResult:
    return ToolResult(request_id=req.request_id, ok=ok, output=scrub(output)[:MAX_OUTPUT],
                      error=scrub(error)[:500], risk_level=risk)


class ToolExecutor:
    def __init__(self, allowed_paths: Optional[List[str]] = None) -> None:
        self._roots = [Path(p).resolve() for p in (allowed_paths or [])]

    def execute(self, req: ToolRequest, verdict: ValidationResult) -> ToolResult:
        try:
            if req.category == "terminal":
                return self._terminal(req, verdict)
            if req.category == "filesystem":
                return self._filesystem(req)
            if req.category == "application":
                return self._application(req)
            if req.category in {"gmail", "calendar", "browser"}:
                # SEAM: real OAuth-backed integrations plug in here. Until then be explicit.
                return _result(req, False, error=f"{req.category} integration is not connected yet.")
            return _result(req, False, error=f"{req.category} control is not implemented.")
        except subprocess.TimeoutExpired:
            return _result(req, False, error=f"Timed out after {TIMEOUT_SECONDS}s.")
        except Exception as exc:  # a tool must never crash the runtime
            return _result(req, False, error=f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------- terminal
    def _terminal(self, req: ToolRequest, verdict: ValidationResult) -> ToolResult:
        cwd = req.args.get("cwd") or os.getcwd()
        argv = verdict.argv or []
        if verdict.builtin:
            return self._builtin(req, argv[0].lower(), cwd)
        program = shutil.which(argv[0])  # resolves npm -> npm.cmd etc., no shell needed
        if program is None:
            return _result(req, False, error=f"'{argv[0]}' was not found on this PC.")
        proc = subprocess.run([program, *argv[1:]], cwd=cwd, capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS, shell=False, env=child_environment(),
                              stdin=subprocess.DEVNULL)
        output = (proc.stdout or "") + (proc.stderr or "")
        return _result(req, proc.returncode == 0, output.strip() or "(no output)",
                       "" if proc.returncode == 0 else f"exit code {proc.returncode}", verdict.risk)

    def _builtin(self, req: ToolRequest, name: str, cwd: str) -> ToolResult:
        if name == "pwd":
            return _result(req, True, str(Path(cwd).resolve()))
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in Path(cwd).iterdir())
        return _result(req, True, "\n".join(entries[:200]) or "(empty)")

    # ----------------------------------------------------------------- filesystem
    def _inside_roots(self, path: Path) -> bool:
        resolved = path.resolve()
        return any(resolved == root or root in resolved.parents for root in self._roots)

    def _filesystem(self, req: ToolRequest) -> ToolResult:
        path = Path(str(req.args["path"]))
        if self._roots and not self._inside_roots(path):
            return _result(req, False, error="Path is outside the allowed paths.")
        if req.action == "read_file":
            return _result(req, True, path.read_text(encoding="utf-8", errors="replace"))
        if req.action == "list_dir":
            return _result(req, True, "\n".join(sorted(p.name for p in path.iterdir())[:200]))
        if req.action == "write_file":
            path.write_text(str(req.args.get("content", "")), encoding="utf-8")
            return _result(req, True, f"Wrote {path.name}", risk=RiskLevel.RESTRICTED)
        return _result(req, False, error=f"Unsupported filesystem action '{req.action}'.")

    # ---------------------------------------------------------------- application
    def _application(self, req: ToolRequest) -> ToolResult:
        name = str(req.args["app"]).lower().removesuffix(".exe")
        program = shutil.which(name)
        if program is None:
            return _result(req, False, error=f"'{name}' was not found on this PC.")
        subprocess.Popen([program], shell=False, env=child_environment(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return _result(req, True, f"Launched {name}", risk=RiskLevel.RESTRICTED)
