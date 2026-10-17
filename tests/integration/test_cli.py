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
