"""Shell command classification (single implementation, used by backend AND local agent).

Design: commands are never run through a shell. We parse a command into an argv and
classify it by *exact* program + allow-listed flags, instead of matching substrings.

    SAFE        read-only and on the allow-list (``git status``, ``python --version`` ...)
    RESTRICTED  known mutating commands, AND anything we do not recognise
    DANGEROUS   destructive programs, shell interpreters, shell metacharacters

Unknown commands are RESTRICTED (need human approval), never SAFE. A flag that is not
on a SAFE command's allow-list demotes it to RESTRICTED -- this is what stops
``git diff --output=somefile`` from being treated as a read-only command.

Both the backend policy engine and the local validator call this module, but each
applies its own path/app allow-lists on top, so neither trusts the other's verdict.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from jarvis_shared.security import CommandCategory

# Characters that only have meaning to a shell. We never use a shell, so their presence
# means the command was written for one (chaining, redirection, substitution).
SHELL_METACHARACTERS = frozenset("&|;<>`$()\n\r^%")

# Programs that destroy data or hand control to an arbitrary interpreter.
DANGEROUS_PROGRAMS: FrozenSet[str] = frozenset({
    "rm", "rmdir", "rd", "del", "erase", "format", "mkfs", "dd", "diskpart", "fdisk",
    "shutdown", "reboot", "halt", "poweroff", "reg", "regedit", "chmod", "chown",
    "takeown", "icacls", "cipher", "vssadmin", "bcdedit", "net", "sc", "schtasks",
    "cmd", "powershell", "pwsh", "bash", "sh", "zsh", "wsl", "mshta", "wscript",
    "cscript", "rundll32", "curl", "wget", "certutil", "bitsadmin",
})

_DANGEROUS_TEXT = re.compile(
    r"\b(drop|truncate)\s+(database|table|schema)\b"
    r"|--force\b|\s-rf\b|\s-fr\b|\s/s\s+/q\b"
    r"|remove-item|format-volume|clear-disk|invoke-expression|\biex\b",
    re.IGNORECASE,
)

# program -> {subcommand: allowed extra flags}. An empty subcommand key means the
# program takes flags directly.
_SAFE_RULES: Dict[str, Dict[str, FrozenSet[str]]] = {
    "python": {"": frozenset({"--version", "-v"})},
    "pip": {"": frozenset({"--version"})},
    "node": {"": frozenset({"--version", "-v"})},
    "npm": {"": frozenset({"--version", "-v"})},
    "whoami": {"": frozenset()},
    "git": {
        "status": frozenset({"-s", "--short", "-b", "--branch", "--porcelain"}),
        "diff": frozenset({"--stat", "--name-only", "--name-status", "--cached",
                           "--staged"}),
        "log": frozenset({"--oneline", "--stat", "--decorate"}),
    },
    "docker": {"ps": frozenset({"-a", "--all"})},
}
_LOG_COUNT_FLAG = re.compile(r"^-\d{1,3}$")

# Internal, side-effect-free commands implemented by the executor (they are not
# programs on PATH on Windows, so they cannot be spawned).
BUILTIN_COMMANDS: FrozenSet[str] = frozenset({"dir", "ls", "pwd"})

_RESTRICTED_PREFIXES: Tuple[Tuple[str, ...], ...] = (
    ("pip", "install"), ("pip", "uninstall"), ("npm", "install"), ("npm", "run"),
    ("git", "add"), ("git", "commit"), ("git", "push"), ("git", "pull"),
    ("git", "checkout"), ("git", "merge"), ("git", "rebase"), ("git", "reset"),
    ("docker", "build"), ("docker", "run"), ("docker", "compose"),
    ("mkdir",), ("copy",), ("move",), ("cp",), ("mv",), ("touch",),
)


@dataclass(frozen=True)
class CommandAssessment:
    """Result of classifying a command string."""

    category: CommandCategory
    argv: List[str] = field(default_factory=list)
    reason: str = ""
    builtin: bool = False

    @property
    def program(self) -> str:
        return self.argv[0].lower() if self.argv else ""


def split_command(command: str) -> List[str]:
    """Tokenise without a shell. Windows-friendly (keeps backslashes in paths)."""
    tokens = shlex.split(command, posix=False)
    cleaned: List[str] = []
    for tok in tokens:
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        cleaned.append(tok)
    return cleaned


def _program_name(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".com"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def classify_command(command: str) -> CommandAssessment:
    """Classify ``command``. Never raises; malformed input is DANGEROUS."""
    text = (command or "").strip()
    if not text:
        return CommandAssessment(CommandCategory.DANGEROUS, reason="Empty command.")
    if len(text) > 1000:
        return CommandAssessment(CommandCategory.DANGEROUS, reason="Command too long.")
    if any(ord(ch) < 32 for ch in text):
        return CommandAssessment(CommandCategory.DANGEROUS,
                                 reason="Control characters are not allowed.")
    if any(ch in SHELL_METACHARACTERS for ch in text):
        return CommandAssessment(
            CommandCategory.DANGEROUS,
            reason="Shell metacharacters (chaining/redirection/substitution) are not allowed.",
        )
    try:
        argv = split_command(text)
    except ValueError:
        return CommandAssessment(CommandCategory.DANGEROUS, reason="Unparseable command.")
    if not argv:
        return CommandAssessment(CommandCategory.DANGEROUS, reason="Empty command.")

    program = _program_name(argv[0])
    args = [a.lower() for a in argv[1:]]

    if program in DANGEROUS_PROGRAMS or _DANGEROUS_TEXT.search(" " + text):
        return CommandAssessment(CommandCategory.DANGEROUS, argv,
                                 f"'{program}' is a destructive or shell-level command.")

    if program in BUILTIN_COMMANDS and not args:
        return CommandAssessment(CommandCategory.SAFE, argv, "Read-only builtin.",
                                 builtin=True)

    safe = _match_safe(program, args)
    if safe is not None:
        return CommandAssessment(CommandCategory.SAFE, argv, safe)

    for prefix in _RESTRICTED_PREFIXES:
        if (program, *args[: len(prefix) - 1]) == prefix:
            return CommandAssessment(CommandCategory.RESTRICTED, argv,
                                     "Modifies state; requires approval.")

    return CommandAssessment(CommandCategory.RESTRICTED, argv,
                             "Unrecognised command; requires approval.")


def _match_safe(program: str, args: List[str]) -> Optional[str]:
    rules = _SAFE_RULES.get(program)
    if rules is None:
        return None
    # Programs that take flags directly (python --version).
    if "" in rules:
        if len(args) == 1 and args[0] in rules[""]:
            return "Read-only allow-listed command."
        if not args and not rules[""]:
            return "Read-only allow-listed command."
        return None
    if not args or args[0] not in rules:
        return None
    allowed = rules[args[0]]
    for flag in args[1:]:
        if flag in allowed:
            continue
        if program == "git" and args[0] == "log" and _LOG_COUNT_FLAG.match(flag):
            continue
        return None
    return "Read-only allow-listed command."
