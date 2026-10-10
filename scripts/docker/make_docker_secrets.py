"""Create the ./secrets files that docker-compose.yml mounts as Docker secrets.

    python scripts/docker/make_docker_secrets.py            # creates any that are missing
    python scripts/docker/make_docker_secrets.py --force    # regenerates EVERYTHING (see warning)

Never overwrites an existing file unless --force. Prints no secret values. The Postgres and
Redis passwords are generated once and embedded in the two connection-URL files, so the app
and the databases always agree.

WARNING: --force replaces JARVIS_DATA_KEY. Data encrypted with the old key becomes
unreadable. Back the old key up first.
"""
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2] / "secrets"
FORCE = "--force" in sys.argv


def write(name: str, value: str) -> bool:
    path = ROOT / name
    if path.exists() and not FORCE:
        return False
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return True


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8").strip()


def main() -> None:
    ROOT.mkdir(exist_ok=True)
    created = []
    plan = {
        "jarvis_jwt_secret": lambda: secrets.token_urlsafe(48),
        "jarvis_data_key": lambda: Fernet.generate_key().decode(),
        "jarvis_metrics_token": lambda: secrets.token_urlsafe(32),
        "postgres_password": lambda: secrets.token_urlsafe(24),
        "redis_password": lambda: secrets.token_urlsafe(24),
    }
    for name, make in plan.items():
        if write(name, make()):
            created.append(name)
    # Connection URLs are derived from the passwords above so they cannot drift apart.
    pg, rd = quote(read("postgres_password"), safe=""), quote(read("redis_password"), safe="")
    if write("jarvis_database_url", f"postgresql+asyncpg://jarvis:{pg}@postgres:5432/jarvis"):
        created.append("jarvis_database_url")
    if write("jarvis_redis_url", f"redis://:{rd}@redis:6379/0"):
        created.append("jarvis_redis_url")
    print(f"secrets/: created {', '.join(created) if created else 'nothing (all present)'}")
    print("BACK UP secrets/jarvis_data_key somewhere separate from the database.")


if __name__ == "__main__":
    main()
