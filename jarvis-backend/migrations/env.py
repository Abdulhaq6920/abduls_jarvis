"""Alembic migration environment for jarvis-backend.

The application runs on ASYNC drivers (``sqlite+aiosqlite`` / ``postgresql+asyncpg``), but
Alembic is synchronous, so this env derives a SYNC URL from the same ``Settings`` the app
uses and never hardcodes a connection string or a secret. SQLite batch mode is enabled so
any future non-additive change (SQLite cannot ALTER columns in place) is rewritten safely.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from jarvis_backend.config import get_settings
from jarvis_backend.storage.models import Base

# Alembic Config object (reads values from alembic.ini).
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url(async_url: str) -> str:
    """Convert the app's async database URL to the sync driver Alembic uses.

    ``sqlite+aiosqlite`` -> ``sqlite`` (stdlib); ``postgresql+asyncpg`` -> ``postgresql+psycopg2``
    (installed via the ``migrations`` extra). Anything else is passed through unchanged.
    """
    if async_url.startswith("sqlite+aiosqlite"):
        return "sqlite" + async_url[len("sqlite+aiosqlite"):]
    if async_url.startswith("postgresql+asyncpg"):
        return "postgresql+psycopg2" + async_url[len("postgresql+asyncpg"):]
    return async_url


def _resolve_url() -> str:
    # An explicit -x url=... (or a value already on the config) wins, so a throwaway test
    # environment can point migrations at a temp DB without touching Settings/.env.
    override = config.get_main_option("sqlalchemy.url")
    if override:
        return override
    return _sync_url(get_settings().effective_database_url)


def run_migrations_offline() -> None:
    url = _resolve_url()
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _resolve_url()
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            render_as_batch=url.startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
