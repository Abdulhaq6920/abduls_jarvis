"""The command line: first-run flow, secret generation, and config checking."""
import re
import shutil
from pathlib import Path

import pytest
from jarvis_backend.__main__ import main

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    for name in ("JARVIS_JWT_SECRET", "JARVIS_DATA_KEY", "JARVIS_METRICS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def env_values(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = value.split(" #")[0].strip()
    return out


def test_init_secrets_fills_the_empty_template_in_place(workdir):
    shutil.copy(ROOT / ".env.example", workdir / ".env")
    assert main(["init-secrets"]) == 0
    values = env_values(workdir / ".env")
    assert len(values["JARVIS_JWT_SECRET"]) >= 32 and len(values["JARVIS_DATA_KEY"]) == 44
    assert values["JARVIS_ENV"] == "dev"            # unrelated settings untouched
    assert (workdir / ".env").read_text().count("JARVIS_JWT_SECRET") == 1   # filled, not duplicated


def test_init_secrets_never_overwrites_existing_values(workdir):
    (workdir / ".env").write_text("JARVIS_JWT_SECRET=keep-this-existing-secret-value-0123456789\nJARVIS_DATA_KEY=\n")
    main(["init-secrets"])
    values = env_values(workdir / ".env")
    assert values["JARVIS_JWT_SECRET"] == "keep-this-existing-secret-value-0123456789"
    assert values["JARVIS_DATA_KEY"]                  # the empty one WAS filled
    before = (workdir / ".env").read_text()
    main(["init-secrets"])
    assert (workdir / ".env").read_text() == before   # idempotent


def test_init_secrets_works_when_no_env_file_exists(workdir):
    main(["init-secrets"])
    assert {"JARVIS_JWT_SECRET", "JARVIS_DATA_KEY", "JARVIS_METRICS_TOKEN"} <= set(env_values(workdir / ".env"))


def test_check_config_reports_without_ever_printing_secrets(workdir, capsys):
    shutil.copy(ROOT / ".env.example", workdir / ".env")
    main(["init-secrets"])
    secret = env_values(workdir / ".env")["JARVIS_JWT_SECRET"]
    from jarvis_backend.config import get_settings
    get_settings.cache_clear()
    assert main(["check-config"]) == 0
    out = capsys.readouterr().out
    assert secret not in out and "set(len=" in out
    get_settings.cache_clear()


def test_check_config_fails_loudly_on_unsafe_production(workdir, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_ENV", "prod")
    monkeypatch.setenv("JARVIS_JWT_SECRET", "short")
    from jarvis_backend.config import get_settings
    get_settings.cache_clear()
    assert main(["check-config"]) == 1
    out = capsys.readouterr().out
    assert "INVALID CONFIGURATION" in out and "short" not in out.replace("shortcut", "")
    get_settings.cache_clear()


def test_check_config_never_prints_the_llm_api_key(workdir, monkeypatch, capsys):
    # A production config that fails validation must not echo JARVIS_LLM_API_KEY into the
    # sanitized output (pydantic's raw ValidationError rendering would otherwise leak it).
    sentinel = "sk-REGRESSION-SENTINEL-0123456789"
    monkeypatch.setenv("JARVIS_ENV", "prod")
    monkeypatch.setenv("JARVIS_JWT_SECRET", "short")  # forces a validation failure
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("JARVIS_LLM_BASE_URL", "http://api.example.com/v1")  # remote plaintext
    monkeypatch.setenv("JARVIS_LLM_API_KEY", sentinel)
    from jarvis_backend.config import get_settings
    get_settings.cache_clear()
    assert main(["check-config"]) == 1
    out = capsys.readouterr().out
    assert "INVALID CONFIGURATION" in out
    assert sentinel not in out
    get_settings.cache_clear()


# ----------------------------------------------------------------- enroll-mfa CLI
#
# The `enroll-mfa` command is a separate secret-bearing surface: it prompts for the current
# password (getpass), prints the setup secret + otpauth URI ONCE, confirms a TOTP, and
# prints one-time recovery codes ONCE. These tests prove wrong-password nondisclosure,
# a successful hashed-only enrollment, and that the setup/recovery material is one-time.

def _seed_admin(workdir, monkeypatch, password="correct horse battery staple"):
    """Generate secrets and create an admin via the CLI; returns the username."""
    import jarvis_backend.__main__ as cli
    from jarvis_backend.config import get_settings

    shutil.copy(ROOT / ".env.example", workdir / ".env")
    assert main(["init-secrets"]) == 0
    get_settings.cache_clear()
    monkeypatch.setattr(cli.getpass, "getpass", lambda *a, **k: password)
    assert main(["create-admin", "--username", "owner"]) == 0
    get_settings.cache_clear()
    return "owner"


def _read_user(username="owner"):
    """Load a user row through a fresh Container on the CLI's own settings."""
    import asyncio

    from jarvis_backend.config import get_settings
    from jarvis_backend.container import Container

    async def run():
        c = await Container.create(get_settings())
        try:
            return await c.auth.users.by_username(username)
        finally:
            await c.close()

    return asyncio.run(run())


def _pending_secret_sync(username="owner"):
    """Read + decrypt the pending ``mfa_secret`` directly from SQLite WITHOUT an event loop.

    The code prompt fires inside the CLI's own running loop, so ``asyncio.run`` cannot be
    nested; a plain ``sqlite3`` read of the stored (encrypted) secret sidesteps that."""
    import sqlite3

    from jarvis_backend.config import get_settings
    from jarvis_backend.crypto import FieldCipher

    settings = get_settings()
    db_path = settings.data_dir / "jarvis.db"
    cipher = FieldCipher.from_setting(settings.secret("data_key"))
    conn = sqlite3.connect(db_path)
    try:
        stored = conn.execute("SELECT mfa_secret FROM users WHERE username = ?",
                              (username,)).fetchone()[0]
    finally:
        conn.close()
    return cipher.decrypt(stored)


def test_enroll_mfa_wrong_password_discloses_no_setup_material(workdir, monkeypatch, capsys):
    """A wrong current password must stop enrollment BEFORE any secret is shown, leave MFA
    disabled, and never leak the setup secret or a recovery code."""
    import jarvis_backend.__main__ as cli
    from jarvis_backend.config import get_settings
    _seed_admin(workdir, monkeypatch)
    capsys.readouterr()  # drop seeding output

    monkeypatch.setattr(cli.getpass, "getpass", lambda *a, **k: "the-wrong-password")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "owner")
    get_settings.cache_clear()
    assert main(["enroll-mfa", "--username", "owner"]) == 1
    out = capsys.readouterr().out
    assert "otpauth://" not in out and "secret" not in out.lower()
    assert "recovery" not in out.lower()
    get_settings.cache_clear()

    user = _read_user()
    assert user.mfa_enabled is False and user.mfa_secret is None
    get_settings.cache_clear()


def test_enroll_mfa_enrolls_with_a_valid_code_and_persists_only_hashes(workdir, monkeypatch, capsys):
    """A correct password + a valid TOTP enables MFA, prints recovery codes ONCE, and stores
    only argon2 hashes (never the plaintext secret or codes)."""
    import jarvis_backend.__main__ as cli
    from jarvis_backend.auth import totp
    from jarvis_backend.auth.passwords import verify_hash
    from jarvis_backend.config import get_settings
    password = "correct horse battery staple"
    _seed_admin(workdir, monkeypatch, password)
    capsys.readouterr()

    monkeypatch.setattr(cli.getpass, "getpass", lambda *a, **k: password)

    # The code prompt happens AFTER the pending secret is stored, so read it back from the
    # DB and return a valid TOTP for it (the test cannot know the random secret in advance).
    def code_from_pending(*_a, **_k):
        return totp.totp_now(_pending_secret_sync())

    monkeypatch.setattr("builtins.input", code_from_pending)
    get_settings.cache_clear()
    assert main(["enroll-mfa", "--username", "owner"]) == 0
    out = capsys.readouterr().out
    get_settings.cache_clear()

    # The setup secret + otpauth URI were shown exactly once as the manual-entry path.
    assert out.count("otpauth://totp/JARVIS:owner") == 1
    recovery = re.findall(r"^\s{2}([0-9a-f]{10})\s*$", out, re.MULTILINE)
    assert len(recovery) == 10, out  # recovery codes printed once

    user = _read_user()
    assert user.mfa_enabled is True
    # Only HASHES are persisted: the stored list is argon2 hashes, never the plaintext codes,
    # and each printed code verifies against exactly one stored hash.
    stored = user.mfa_recovery_hashes
    assert len(stored) == 10 and all(h.startswith("$argon2") for h in stored)
    assert all(code not in stored for code in recovery)

    import asyncio

    async def _matches(code: str) -> bool:
        for h in stored:
            if await verify_hash(h, code):
                return True
        return False

    for code in recovery:
        assert asyncio.run(_matches(code)) is True  # each printed code maps to a stored hash
    get_settings.cache_clear()


def test_env_template_has_no_real_secrets():
    """The committed example must stay a template: every secret field is empty."""
    values = env_values(ROOT / ".env.example")
    for key in ("JARVIS_JWT_SECRET", "JARVIS_DATA_KEY", "JARVIS_METRICS_TOKEN", "POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        assert values[key] == "", f"{key} must be empty in .env.example"
    assert not re.search(r"eyJ[A-Za-z0-9_-]{10,}", (ROOT / ".env.example").read_text())


def test_gitignore_and_dockerignore_exclude_env_files():
    for name in (".gitignore", ".dockerignore"):
        lines = (ROOT / name).read_text().splitlines()
        assert ".env" in lines and "!.env.example" in lines, name
        # docker/.env.docker and the generated secret files must be covered too
        assert ".env.*" in lines and "secrets/" in lines, name


def test_the_template_for_docker_settings_is_committable_but_the_real_file_is_not():
    import fnmatch
    assert fnmatch.fnmatch(".env.docker", ".env.*")             # real file: ignored
    assert not fnmatch.fnmatch("env.docker.example", ".env.*")  # template: tracked
    text = (ROOT / "docker" / "env.docker.example").read_text()
    assert not re.search(r"(?im)^(?!#)\s*\w*(password|secret|token|key)\w*\s*=\s*\S+", text)
