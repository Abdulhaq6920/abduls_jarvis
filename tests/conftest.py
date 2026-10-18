"""Shared fixtures. Every test gets its own throw-away SQLite database and secrets."""
from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from jarvis_backend.app import create_app
from jarvis_backend.auth.passwords import hash_password
from jarvis_backend.config import Settings
from jarvis_backend.crypto import FieldCipher, set_cipher
from jarvis_backend.storage import Database, UserStore

ADMIN_USER, ADMIN_PASSWORD = "owner", "correct horse battery staple"
JWT_SECRET = "test-signing-key-" + "x7Kq9" * 8


@pytest.fixture(autouse=True, scope="session")
def _isolate_from_local_env_file():
    """Stop every test from reading a developer's real, git-ignored .env.

    Settings() declares env_file=".env", so a Settings(env="dev", ...) built in a test would
    silently inherit whatever the developer has configured locally (e.g. JARVIS_SEARCH_PROVIDER
    or JARVIS_LLM_* turned on). That made tests asserting CODE DEFAULTS ("...defaults_to_disabled")
    fail on a machine that has those features enabled -- a test-isolation bug, not a code bug.
    Neutralise the default env_file for the whole test session so tests see only the values they
    pass explicitly (and real OS env vars), regardless of any local .env.
    """
    original = dict(Settings.model_config)
    Settings.model_config["env_file"] = None
    try:
        yield
    finally:
        Settings.model_config.clear()
        Settings.model_config.update(original)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        env="dev", data_dir=tmp_path, log_to_file=False, run_scheduler=False,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        jwt_secret=JWT_SECRET, data_key=Fernet.generate_key().decode(),
        metrics_token="metrics-token-for-tests-0123456789",
        allowed_hosts="localhost,127.0.0.1",
    )


@pytest.fixture(autouse=True)
def _reset_cipher():
    yield
    set_cipher(FieldCipher([]))


@pytest.fixture
async def db(settings):
    set_cipher(FieldCipher.from_setting(settings.secret("data_key")))
    database = Database(settings)
    await database.create_schema()
    yield database
    await database.dispose()


@pytest.fixture
def client(settings):
    """A running app with one admin user already created."""
    async def seed() -> None:
        set_cipher(FieldCipher.from_setting(settings.secret("data_key")))
        database = Database(settings)
        await database.create_schema()
        await UserStore(database).create(ADMIN_USER, await hash_password(ADMIN_PASSWORD))
        await database.dispose()

    asyncio.run(seed())
    with TestClient(create_app(settings), base_url="http://localhost") as test_client:
        yield test_client


def login(client: TestClient, username: str = ADMIN_USER, password: str = ADMIN_PASSWORD) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(client) -> dict:
    return bearer(login(client))


@pytest.fixture
def device(client, admin) -> dict:
    """A registered local-agent device: its id, one-time secret, and a live token."""
    created = client.post("/api/devices", json={"name": "test-pc"}, headers=admin).json()
    token = client.post("/api/auth/device-token",
                        json={"device_id": created["id"], "secret": created["secret"]}).json()
    return {"id": created["id"], "secret": created["secret"], "headers": bearer(token["access_token"])}


@pytest.fixture
def agent(device) -> dict:
    """Authorization headers for the local-agent role."""
    return device["headers"]
