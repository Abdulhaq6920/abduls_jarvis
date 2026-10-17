"""Alembic-independent, additive-only schema repair for an existing SQLite dev DB.

``create_all`` only creates missing TABLES, never missing COLUMNS. A dev database created
before the MFA / step-up columns landed therefore throws ``no such column: users.mfa_secret``
on the first query. This module brings such a database up to the current model shape WITHOUT
alembic and WITHOUT deleting data, by inspecting each mapped table and issuing
``ALTER TABLE ... ADD COLUMN`` only for columns that are genuinely missing.

Strictly additive and SQLite-only:

* It never drops, renames, or alters an existing column, and never touches a table that is
  not already present (``create_all`` handles brand-new tables).
* It only runs on SQLite. Postgres schema evolution goes through alembic (``migrate``); on a
  non-SQLite engine this is a no-op so prod is unaffected.
* It is idempotent: a column that already exists is skipped, so running it twice is a no-op.

No alembic import appears here, so it works in an environment where alembic is absent.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.schema import Column

from jarvis_backend.storage.models import Base

if TYPE_CHECKING:  # avoid an import cycle at runtime (db imports nothing from here)
    from jarvis_backend.storage.db import Database


def _render_add_column(dialect, table_name: str, column: Column) -> str:
    """``ALTER TABLE <t> ADD COLUMN <name> <type> [NOT NULL DEFAULT <d>]`` for SQLite.

    SQLite forbids adding a NOT NULL column without a DEFAULT (existing rows need a value),
    so a NOT NULL column with a scalar default is emitted with that default; the few additive
    columns we backfill (``mfa_enabled``, ``mfa_state_version``) both carry one.
    """
    col_type = column.type.compile(dialect=dialect)
    sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}'
    if not column.nullable:
        default_sql = _scalar_default_sql(column)
        if default_sql is None:
            # No usable default for a NOT NULL add: fall back to a nullable add rather than
            # emit DDL SQLite will reject. This keeps the step safe and never destructive.
            return sql
        sql += f" NOT NULL DEFAULT {default_sql}"
    return sql


def _scalar_default_sql(column: Column) -> str | None:
    """Render a column's Python scalar default as a SQL literal, or None if not scalar.

    Only plain constants are handled (the additive NOT NULL columns use ``default=False`` /
    ``default=0``); callables such as ``utcnow`` are not applicable to the additive set and
    return None so the caller adds the column as nullable instead of guessing a value.
    """
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def ensure_sqlite_columns(conn: Connection) -> list[str]:
    """Add any additive columns missing from EXISTING SQLite tables; return what was added.

    Runs synchronously against a live DBAPI connection (call it through ``run_sync``). For
    each mapped table that already exists, it compares the live columns against the model and
    issues an additive ``ADD COLUMN`` for every column the model declares but the table lacks.
    Returns a list of ``"table.column"`` strings describing what it added (for logging).
    """
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # brand-new table: create_all already made it in full
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            conn.exec_driver_sql(_render_add_column(conn.dialect, table.name, column))
            added.append(f"{table.name}.{column.name}")
    return added


async def ensure_additive_columns(db: "Database") -> list[str]:
    """Async wrapper: no-op on non-SQLite; runs the additive backfill on SQLite.

    Postgres migrations go through alembic, so this only ever touches SQLite. Returns the
    list of ``"table.column"`` strings that were added (empty when nothing was missing).
    """
    if not db.is_sqlite:
        return []
    async with db.engine.begin() as conn:
        return await conn.run_sync(ensure_sqlite_columns)
