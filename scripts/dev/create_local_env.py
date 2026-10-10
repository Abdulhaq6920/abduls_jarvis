"""Create the git-ignored `.env` files for running JARVIS directly on this PC (no Docker).

    python scripts/dev/create_local_env.py

* Root `.env`        backend settings + generated secrets.
* jarvis-local-agent/.env   voice agent settings (no secrets; the device secret goes in the
                            Windows Credential Manager, not in a file).

Safe to re-run: it never overwrites an existing value. If this PC already has data in
~/.jarvis (encrypted with the keys in ~/.jarvis/dev_secrets.json), those SAME keys are reused so
that data stays readable. Nothing secret is printed.
"""
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
DEV_SECRETS = Path.home() / ".jarvis" / "dev_secrets.json"


def parse(path: Path) -> dict:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, rest = line.partition("=")
            values[key.strip()] = rest.split(" #")[0].strip()
    return values


def fill(path: Path, wanted: dict) -> list:
    """Set `KEY=value` in-place for keys that are missing or empty; never touch real values."""
    lines = path.read_text(encoding="utf-8").splitlines()
    current = parse(path)
    changed = []
    for key, value in wanted.items():
        if current.get(key):
            continue
        for i, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
                lines[i] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
        changed.append(key)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return changed


def restrict(path: Path) -> None:
    """Owner-only access (Windows ACL / POSIX mode) so other local users cannot read secrets."""
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME", "")
            os.system(f'icacls "{path}" /inheritance:r /grant:r "{user}:(R,W)" >nul 2>&1')  # noqa: S605
        else:
            os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> int:
    stored = json.loads(DEV_SECRETS.read_text(encoding="utf-8")) if DEV_SECRETS.exists() else {}
    reused = [k for k in ("jwt_secret", "data_key") if stored.get(k)]

    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
    changed = fill(env, {
        "JARVIS_JWT_SECRET": stored.get("jwt_secret") or secrets.token_urlsafe(48),
        "JARVIS_DATA_KEY": stored.get("data_key") or Fernet.generate_key().decode(),
        "JARVIS_METRICS_TOKEN": secrets.token_urlsafe(32),
        "JARVIS_PUBLIC_ORIGIN": "http://localhost:8077",
    })
    restrict(env)
    print(f".env: filled {', '.join(changed) if changed else 'nothing (already complete)'}")
    if reused:
        print(f"      reused existing local keys for: {', '.join(reused)} (keeps current data readable)")

    agent = ROOT / "jarvis-local-agent" / ".env"
    if not agent.exists():
        shutil.copy(ROOT / "jarvis-local-agent" / ".env.example", agent)
    if "\\you\\" in parse(agent).get("JARVIS_ALLOWED_PATHS", ""):  # the template's placeholder, not a choice
        agent.write_text(agent.read_text(encoding="utf-8").replace(
            parse(agent)["JARVIS_ALLOWED_PATHS"], ""), encoding="utf-8", newline="\n")
    fill(agent, {"JARVIS_ALLOWED_PATHS": str(ROOT)})
    restrict(agent)
    print(f"jarvis-local-agent/.env: ready. JARVIS may touch only: {parse(agent).get('JARVIS_ALLOWED_PATHS')}")
    print("Still yours to provide: dashboard login (create-admin), device id/secret (after registering).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
