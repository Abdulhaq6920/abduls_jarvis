"""Command line: ``python -m jarvis_backend <command>`` (or the ``jarvis-backend`` script).

    serve           run the HTTP API
    worker          run the scheduler + maintenance (exactly one instance)
    migrate         create/upgrade database tables (run once before production start)
    init-secrets    generate strong secrets into a private .env (never overwrites)
    check-config    validate configuration and print a secret-free summary
    create-admin    create the first (or another) dashboard user
    create-device   register a local agent; prints its secret ONCE
    enroll-mfa      enroll TOTP MFA for a user; prints recovery codes ONCE
    verify-audit    verify the audit log hash chain
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
import signal
import sys
from pathlib import Path
from typing import List, Optional

from jarvis_shared.log import get_logger, setup_logging

from jarvis_backend.config import Settings, get_settings, warn_if_env_file_is_world_readable

log = get_logger("jarvis.cli")


def _setup(service: str, settings: Settings) -> None:
    setup_logging(service, level=settings.log_level, fmt=settings.log_format,
                  log_dir=(settings.data_dir / "logs") if settings.log_to_file else None)
    problem = warn_if_env_file_is_world_readable()
    if problem:
        log.warning(problem)


# ------------------------------------------------------------------------------ commands
def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    _setup("api", settings)
    uvicorn.run(
        "jarvis_backend.app:create_app", factory=True,
        host=args.host or settings.host, port=args.port or settings.port,
        log_config=None,          # our logging setup owns formatting
        server_header=False,      # don't advertise the server software
        proxy_headers=False,      # X-Forwarded-* is handled by our own trusted-hop logic
        timeout_graceful_shutdown=15,
    )
    return 0


def cmd_worker(_: argparse.Namespace) -> int:
    from jarvis_backend.container import Container
    from jarvis_backend.jobs import JobScheduler
    from jarvis_backend.maintenance import run_maintenance

    settings = get_settings()
    _setup("worker", settings)

    async def run() -> None:
        container = await Container.create(settings)
        container.scheduler = JobScheduler(container.job_runner, settings.timezone,
                                           maintenance=lambda: run_maintenance(container))
        container.scheduler.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows event loop
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        log.info("worker running")
        await stop.wait()
        log.info("worker stopping")
        await container.close()

    asyncio.run(run())
    return 0


def _alembic_ini_path() -> Optional[Path]:
    """Locate the committed ``alembic.ini`` (sibling of the ``src/`` package tree).

    Resolved relative to this module so the migration path never depends on the current
    working directory. Returns None if it cannot be found (e.g. a slimmed install), which
    makes ``migrate`` fall back to the create_schema + additive path.
    """
    # .../jarvis-backend/src/jarvis_backend/__main__.py -> .../jarvis-backend/alembic.ini
    candidate = Path(__file__).resolve().parents[2] / "alembic.ini"
    return candidate if candidate.exists() else None


def _sync_migration_url(async_url: str) -> str:
    """App async URL -> the sync driver Alembic uses (mirrors migrations/env.py)."""
    if async_url.startswith("sqlite+aiosqlite"):
        return "sqlite" + async_url[len("sqlite+aiosqlite"):]
    if async_url.startswith("postgresql+asyncpg"):
        return "postgresql+psycopg2" + async_url[len("postgresql+asyncpg"):]
    return async_url


def _run_alembic_upgrade(settings: Settings) -> bool:
    """Run ``alembic upgrade head`` if alembic (and the scaffolding) are available.

    Returns True when the upgrade ran, False when alembic is not installed or the config
    cannot be located so the caller degrades to create_schema + the additive backfill.
    Never logs the database URL (it may embed a password).
    """
    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        log.warning("alembic not installed; using create_schema + additive column backfill")
        return False
    ini = _alembic_ini_path()
    if ini is None:
        log.warning("alembic installed but migrations config not found; "
                    "using create_schema + additive column backfill")
        return False
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _sync_migration_url(settings.effective_database_url))
    command.upgrade(cfg, "head")
    log.info("alembic upgrade head complete")
    return True


def cmd_migrate(_: argparse.Namespace) -> int:
    from jarvis_backend.crypto import FieldCipher, set_cipher
    from jarvis_backend.storage import Database, ensure_additive_columns

    settings = get_settings()
    _setup("migrate", settings)
    set_cipher(FieldCipher.from_setting(settings.secret("data_key")))

    ran_alembic = _run_alembic_upgrade(settings)

    async def run() -> None:
        db = Database(settings)
        try:
            # Fallback path (no alembic): create_all makes any missing TABLES. On either
            # path, the additive backfill then adds any missing COLUMNS to existing SQLite
            # tables so a stale dev DB (missing mfa_*/step_up_at) is repaired in place
            # without deleting data. Both steps are idempotent, so running twice is a no-op.
            if not ran_alembic:
                await db.create_schema()
            added = await ensure_additive_columns(db)
            if added:
                log.info("added missing columns", extra={"columns": added})
        finally:
            await db.dispose()

    asyncio.run(run())
    log.info("schema ready", extra={"database": settings.describe()["database"]})
    print("Database schema is up to date.")
    return 0


_SECRET_KEYS = {
    "JARVIS_JWT_SECRET": lambda: secrets.token_urlsafe(48),
    "JARVIS_DATA_KEY": lambda: __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode(),
    "JARVIS_METRICS_TOKEN": lambda: secrets.token_urlsafe(32),
    "POSTGRES_PASSWORD": lambda: secrets.token_urlsafe(24),
    "REDIS_PASSWORD": lambda: secrets.token_urlsafe(24),
}


def cmd_init_secrets(args: argparse.Namespace) -> int:
    path = Path(args.file)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.rstrip("\n").splitlines() if existing else ["# Generated by `init-secrets`. NEVER commit this file."]

    def value_of(line: str, key: str) -> Optional[str]:
        """The value if `line` assigns `key` (ignoring a trailing '# comment'), else None."""
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            return None
        name, _, rest = stripped.partition("=")
        return rest.split(" #", 1)[0].strip() if name.strip() == key else None

    added: List[str] = []
    for key, make in _SECRET_KEYS.items():
        index = next((i for i, ln in enumerate(lines) if value_of(ln, key) is not None), None)
        if index is None:
            lines.append(f"{key}={make()}")        # key missing entirely: append it
            added.append(key)
        elif value_of(lines[index], key) == "":
            lines[index] = f"{key}={make()}"       # present but EMPTY (the template): fill in place
            added.append(key)
        # else: a real value already exists -> never overwrite it
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(f"{path}: added {', '.join(added) if added else 'nothing (all secrets already present)'}.")
    print("Values were written to the file and are not shown. Back up JARVIS_DATA_KEY: without it,")
    print("encrypted conversations and memories cannot be recovered.")
    return 0


def _safe_config_error(exc: Exception) -> str:
    """Render a config error WITHOUT leaking any input value.

    Pydantic's ``str(ValidationError)`` echoes the raw input dict (including secrets such
    as ``JARVIS_JWT_SECRET`` / ``JARVIS_LLM_API_KEY``). We therefore print only the
    structured, value-free ``msg`` of each error -- our own validator messages already
    name the offending setting without its value.
    """
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        msgs = []
        for err in exc.errors():
            msg = str(err.get("msg", "")).strip()
            # Pydantic prefixes value-error messages with "Value error, "; drop it so the
            # output is just our safe findings. Never read err["input"] (the raw value).
            msgs.append(msg.removeprefix("Value error, "))
        return "\n".join(m for m in msgs if m) or "configuration is invalid"
    # Non-pydantic failures (e.g. a raised ValueError) carry only our own message text.
    return str(exc)


def cmd_check_config(_: argparse.Namespace) -> int:
    try:
        settings = get_settings()
    except Exception as exc:  # keep output value-free -- never print the raw input
        print(f"INVALID CONFIGURATION:\n{_safe_config_error(exc)}")
        return 1
    for key, value in settings.describe().items():
        print(f"  {key:15} {value}")
    findings = settings.findings()
    for f in findings:
        print(f"  [{f.level.upper()}] {f.message}")
    return 1 if any(f.level == "error" for f in findings) else 0


def _prompt_password(username: str) -> str:
    from jarvis_backend.auth.passwords import WeakPassword, check_policy

    while True:
        first = getpass.getpass("Password (min 12 chars): ")
        try:
            check_policy(first, username)
        except WeakPassword as exc:
            print(f"  {exc}")
            continue
        if first != getpass.getpass("Repeat password: "):
            print("  Passwords did not match.")
            continue
        return first


def cmd_create_admin(args: argparse.Namespace) -> int:
    from jarvis_backend.auth.passwords import hash_password
    from jarvis_backend.container import Container

    settings = get_settings()
    _setup("cli", settings)
    username = (args.username or input("Username: ")).strip().lower()
    if not username.replace("_", "").replace("-", "").replace(".", "").isalnum() or len(username) > 64:
        print("Username must be letters, digits, '.', '_' or '-' (max 64).")
        return 2
    password = _prompt_password(username)

    async def run() -> int:
        c = await Container.create(settings)
        try:
            if await c.auth.users.by_username(username):
                print(f"User '{username}' already exists.")
                return 1
            await c.auth.users.create(username, await hash_password(password), role="admin")
            await c.audit.record("user_created", user=username, via="cli")
            print(f"Created admin '{username}'.")
            return 0
        finally:
            await c.close()

    return asyncio.run(run())


def cmd_create_device(args: argparse.Namespace) -> int:
    from jarvis_backend.container import Container

    settings = get_settings()
    _setup("cli", settings)

    async def run() -> int:
        c = await Container.create(settings)
        try:
            row, secret = await c.auth.devices.create(args.name, created_by="cli")
            await c.audit.record("device_created", device_id=row.id, name=row.name, by="cli")
            print(f"Device '{row.name}' registered.\n")
            print("Put these in the local agent's .env (the secret is shown ONLY ONCE):")
            print(f"  JARVIS_DEVICE_ID={row.id}")
            print(f"  JARVIS_DEVICE_SECRET={secret}")
            return 0
        finally:
            await c.close()

    return asyncio.run(run())


def cmd_enroll_mfa(args: argparse.Namespace) -> int:
    """Enroll (or re-enroll) TOTP MFA for a user from the command line.

    Prints the otpauth URI + base32 secret for manual entry, waits for a 6-digit code to
    confirm, then prints the one-time recovery codes ONCE. No secrets go to the logs."""
    from jarvis_backend.auth.service import Principal
    from jarvis_backend.container import Container

    settings = get_settings()
    _setup("cli", settings)
    username = (args.username or input("Username: ")).strip().lower()

    async def run() -> int:
        c = await Container.create(settings)
        try:
            user = await c.auth.users.by_username(username)
            if user is None:
                print(f"User '{username}' does not exist.")
                return 1
            principal = Principal(subject=user.id, role=user.role, sid="cli", kind="user")
            # Independent re-auth: the current password is required before any secret is
            # disclosed, matching the API's stolen-session protection.
            password = getpass.getpass("Current password: ")
            try:
                secret, uri = await c.auth.start_mfa_enrollment(principal, password)
            except Exception:  # noqa: BLE001 - wrong password / already enabled: fail closed
                print("Could not start enrollment (wrong password, or MFA already enabled).")
                return 1
            print("\nAdd this account to your authenticator app:")
            print(f"  otpauth URI : {uri}")
            print(f"  secret      : {secret}   (manual entry; SHA1 / 6 digits / 30s)\n")
            code = input("Enter the 6-digit code from your app to confirm: ").strip()
            try:
                codes = await c.auth.confirm_mfa_enrollment(principal, code)
            except Exception:  # noqa: BLE001 - any failure means MFA is NOT enabled (fail closed)
                print("That code was not accepted. MFA was NOT enabled. Try again.")
                return 1
            print("\nMFA is now enabled. Save these one-time recovery codes somewhere safe")
            print("(each works once, and they are shown ONLY NOW):\n")
            for rc in codes:
                print(f"  {rc}")
            return 0
        finally:
            await c.close()

    return asyncio.run(run())


def cmd_verify_audit(_: argparse.Namespace) -> int:
    from jarvis_backend.container import Container

    settings = get_settings()
    _setup("cli", settings)

    async def run() -> int:
        c = await Container.create(settings)
        try:
            result = await c.audit.verify()
            print(f"checked={result.checked} ok={result.ok} {result.reason}")
            return 0 if result.ok else 1
        finally:
            await c.close()

    return asyncio.run(run())


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-backend", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.set_defaults(func=cmd_serve)
    sub.add_parser("worker", help="run the scheduler").set_defaults(func=cmd_worker)
    sub.add_parser("migrate", help="create database tables").set_defaults(func=cmd_migrate)
    secrets_p = sub.add_parser("init-secrets", help="generate secrets into .env")
    secrets_p.add_argument("--file", default=".env")
    secrets_p.set_defaults(func=cmd_init_secrets)
    sub.add_parser("check-config", help="validate configuration").set_defaults(func=cmd_check_config)
    admin = sub.add_parser("create-admin", help="create a dashboard user")
    admin.add_argument("--username")
    admin.set_defaults(func=cmd_create_admin)
    device = sub.add_parser("create-device", help="register a local agent")
    device.add_argument("--name", required=True)
    device.set_defaults(func=cmd_create_device)
    enroll = sub.add_parser("enroll-mfa", help="enroll TOTP MFA for a user")
    enroll.add_argument("--username")
    enroll.set_defaults(func=cmd_enroll_mfa)
    sub.add_parser("verify-audit", help="verify the audit chain").set_defaults(func=cmd_verify_audit)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
