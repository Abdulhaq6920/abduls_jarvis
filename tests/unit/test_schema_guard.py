"""The alembic-free additive-column backfill (``storage/schema_guard``).

These reproduce the stale-DB crash directly: an existing SQLite ``users`` table created
before the MFA columns landed throws ``no such column: users.mfa_secret``. The backfill must
add the missing columns IN PLACE (no data loss), backfill NOT-NULL columns with their
defaults, and be idempotent. It must also be a no-op on a non-SQLite engine. No alembic.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from jarvis_backend.auth.passwords import hash_password
from jarvis_backend.config import Settings
from jarvis_backend.crypto import FieldCipher, set_cipher
from jarvis_backend.storage import Database, UserStore, ensure_additive_columns
from sqlalchemy import text

# The pre-MFA shape of the users table (what an old dev DB has): everything EXCEPT the
# mfa_* columns. Recreating it by hand is portable; SQLite cannot DROP COLUMN on old versions.
_OLD_USERS_DDL = """
CREATE TABLE users (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    username VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(16) NOT NULL,
    disabled BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    password_changed_at DATETIME NOT NULL,
    last_login_at DATETIME
)
"""


@pytest.fixture
def sqlite_settings(tmp_path) -> Settings:
    return Settings(
        env="dev", data_dir=tmp_path, log_to_file=False, run_scheduler=False,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'old.db').as_posix()}",
        jwt_secret="test-signing-key-" + "x7Kq9" * 8,
        data_key=Fernet.generate_key().decode(),
    )


async def _make_old_db(db: Database) -> None:
    """Replace the current ``users`` table with a pre-MFA one holding one row."""
    async with db.engine.begin() as conn:
        await conn.execute(text("DROP TABLE users"))
        await conn.execute(text(_OLD_USERS_DDL))
        await conn.execute(text(
            "INSERT INTO users (id, username, password_hash, role, disabled, "
            "created_at, password_changed_at) VALUES "
            "('u1', 'legacy', 'hash', 'admin', 0, '2024-01-01 00:00:00', '2024-01-01 00:00:00')"
        ))


async def test_backfill_fixes_the_stale_users_table(sqlite_settings):
    set_cipher(FieldCipher.from_setting(sqlite_settings.secret("data_key")))
    db = Database(sqlite_settings)
    try:
        await db.create_schema()
        await _make_old_db(db)  # now the DB is missing every mfa_* column

        # Sanity: querying mfa_secret BEFORE the backfill reproduces the exact crash.
        with pytest.raises(Exception) as excinfo:
            await UserStore(db).by_username("legacy")
        assert "mfa_secret" in str(excinfo.value)

        added = await ensure_additive_columns(db)
        assert set(added) == {
            "users.mfa_secret", "users.mfa_enabled", "users.mfa_last_counter",
            "users.mfa_recovery_hashes", "users.mfa_state_version",
        }

        # The crash is gone and the existing row survived with backfilled defaults.
        row = await UserStore(db).by_username("legacy")
        assert row is not None and row.username == "legacy"
        assert row.mfa_enabled is False          # NOT NULL DEFAULT 0
        assert row.mfa_state_version == 0        # NOT NULL DEFAULT 0
        assert row.mfa_secret is None            # nullable, no value yet
    finally:
        await db.dispose()


async def test_backfill_is_idempotent(sqlite_settings):
    set_cipher(FieldCipher.from_setting(sqlite_settings.secret("data_key")))
    db = Database(sqlite_settings)
    try:
        await db.create_schema()
        await _make_old_db(db)
        first = await ensure_additive_columns(db)
        assert first  # added something the first time
        second = await ensure_additive_columns(db)
        assert second == []  # a second run is a no-op
    finally:
        await db.dispose()


async def test_backfill_leaves_a_current_schema_untouched(sqlite_settings):
    """On a freshly created (already-current) DB there is nothing additive to do."""
    set_cipher(FieldCipher.from_setting(sqlite_settings.secret("data_key")))
    db = Database(sqlite_settings)
    try:
        await db.create_schema()
        await UserStore(db).create("owner", await hash_password("correct horse battery staple"))
        assert await ensure_additive_columns(db) == []
    finally:
        await db.dispose()


async def test_backfill_is_a_noop_on_non_sqlite():
    """A Postgres (non-SQLite) engine never runs the SQLite DDL path.

    We assert on the ``is_sqlite`` branch without a running Postgres: a tiny stub Database
    with ``is_sqlite=False`` must return [] without touching an engine.
    """
    class _NotSqlite:
        is_sqlite = False
        engine = None  # must never be used on this path

    assert await ensure_additive_columns(_NotSqlite()) == []
