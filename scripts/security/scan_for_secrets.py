"""Pre-publish secret scan. Run before the first push and before any release.

    python scripts/security/scan_for_secrets.py

It checks every file git would commit for
  1. the EXACT values held on this machine: ./secrets/*, secret-looking keys in .env files, and
     ~/.jarvis/dev_secrets.json (the strongest test: catches a pasted real secret
     even when it looks innocent), and
  2. common credential shapes (private keys, JWTs, cloud keys, passwords inside URLs).

It prints file names and the kind of finding, NEVER the secret itself. Exit code 1 on any
finding, so it can also gate a CI job or a git hook.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".db", ".pyc", ".zip"}

PATTERNS = {
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "OpenAI/Anthropic-style key": re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{24,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
}
# user:password@host where the password is a real-looking value, not a template/test placeholder
URL_PASSWORD = re.compile(r"[a-z+]+://[^\s:/@]+:([^\s@*/]{6,})@")
PLACEHOLDER_HINT = re.compile(r"(?i)pass(word)?|secret|example|changeme|xxxx|\*{3,}|\$\{|<|\bpw\b|\bu:p\b|redacted|hunter2")


def candidate_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if line and (ROOT / line).is_file()]


SECRET_NAME = re.compile(r"(?i)(secret|key|token|password|passwd)")


def real_secret_values() -> dict[str, str]:
    """Every real secret this machine holds: ./secrets/*, secret-looking keys in any .env file,
    and the dev keys in ~/.jarvis. A tracked file containing one of these values is a leak."""
    values: dict[str, str] = {}

    def add(label: str, value: str) -> None:
        value = value.strip().strip("\"'")
        if len(value) >= 16:
            values[label] = value

    secrets_dir = ROOT / "secrets"
    for path in secrets_dir.glob("*") if secrets_dir.is_dir() else []:
        add(f"secrets/{path.name}", path.read_text(encoding="utf-8", errors="ignore"))
    for env_file in [*ROOT.glob(".env"), *ROOT.glob("*/.env"), *ROOT.glob("docker/.env.docker")]:
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            key, sep, rest = line.partition("=")
            if sep and not line.lstrip().startswith("#") and SECRET_NAME.search(key):
                add(f"{env_file.relative_to(ROOT).as_posix()}:{key.strip()}", rest.split(" #")[0])
    dev = Path.home() / ".jarvis" / "dev_secrets.json"
    if dev.exists():
        import json
        for key, value in json.loads(dev.read_text(encoding="utf-8")).items():
            add(f"~/.jarvis/dev_secrets.json:{key}", str(value))
    return values


def main() -> int:
    secrets = real_secret_values()
    findings: list[str] = []
    files = candidate_files()
    for path in files:
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(ROOT).as_posix()
        for name, value in secrets.items():
            if value in text:
                findings.append(f"{rel}: contains the REAL value of {name}")
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{rel}: looks like a {label}")
        for match in URL_PASSWORD.finditer(text):
            if not PLACEHOLDER_HINT.search(match.group(1)):
                findings.append(f"{rel}: URL with an embedded password")
    print(f"scanned {len(files)} files against {len(secrets)} real secret values and {len(PATTERNS) + 1} patterns")
    for line in sorted(set(findings)):
        print("  FOUND  " + line)
    print("RESULT: " + ("NOT SAFE TO PUBLISH" if findings else "clean"))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
