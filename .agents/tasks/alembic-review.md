# Alembic migrations with a safe in-place SQLite schema upgrade

Phase 5 adds versioned Alembic migrations (`alembic.ini`, `migrations/env.py`, a hand-written baseline revision) and makes `migrate` repair a stale dev SQLite database in place. The crash this fixes is `no such column: users.mfa_secret`: `create_all` only ever creates missing *tables*, so a dev DB created before the MFA / step-up columns landed kept its old `users` shape and threw on the first query. Because the test venv has no alembic (confirmed by probe), `migrate` imports alembic lazily and degrades to `create_schema()` plus an additive-only SQLite column backfill, while the committed alembic scaffolding runs the real versioned path wherever alembic is installed. The approach matches the venv reality and is internally consistent with it.

Watch for: a stale SQLite DB that predates alembic has no `alembic_version` table, so if alembic is later installed, `alembic upgrade head` would attempt to recreate already-existing tables (possible, edge case outside the stated regression; the SQLite additive backfill is the safety net that is actually tested). Nothing in this list blocks the change.

**Verdict**: APPROVED

## High-level view

The path chosen is degrade-gracefully, not full-alembic-mandatory, because the probe showed alembic, psycopg2, and psycopg all absent from the venv the suite runs in. `migrate` therefore never hard-imports alembic: it imports it lazily, runs `upgrade head` when present, and otherwise logs a warning and falls back to `create_schema()`. On SQLite, either path then runs an additive column backfill. This is the design pivot the whole change rests on, and the implementation holds to it everywhere.

The stale-DB fix is the additive backfill in `storage/schema_guard.py`: it inspects each already-existing table and issues `ALTER TABLE ... ADD COLUMN` only for columns the model declares but the table lacks. It is SQLite-only, additive-only (never drop/rename), and idempotent. NOT-NULL additions (`mfa_enabled`, `mfa_state_version`) carry a scalar `DEFAULT`, so existing rows backfill rather than violate the constraint. The regression test reproduces the exact crash and proves it is gone, running without alembic.

Fresh installs still end at the full current schema on both backends: `create_all` builds every table on the fallback path, and the baseline revision builds the same schema on the alembic path. The baseline was reviewed column-for-column against `models.py` and matches, including the encrypted-type mapping (`EncryptedText`/`EncryptedJSON` → `Text`, `UTCDateTime` → `DateTime(timezone=True)`, `JSON` → `JSON`). Models and async runtime drivers are untouched, so field-level encryption is unaffected — the cipher runs in Python over a plain TEXT column.

Dependencies are isolated in an optional `migrations` extra (`alembic`, `psycopg2-binary`) and never pulled into base or `dev`, so the async runtime never imports a sync driver. The pins are present in all three dependency files. Docs replace the old manual `ALTER TABLE` note with the real `migrate` procedure, and the Phase 5 plan entry is marked done. The alembic-dependent test is guarded by `importorskip`, so collection stays clean in the alembic-less venv.

<details>
<summary>Issues (2)</summary>

1. **Pre-alembic SQLite + later alembic install** — a dev DB built by `create_all` has no `alembic_version` row; installing alembic afterward makes `upgrade head` try to recreate existing tables and fail. Edge case outside the stated regression; worth a one-line doc note or a `stamp`-on-first-run guard, non-blocking.
2. **Live alembic path not executed in verification** — the real `alembic upgrade head` / `--autogenerate` run was skipped (no alembic in the venv). Covered by the `importorskip`-guarded test wherever alembic is installed, and the baseline was reviewed by hand; acceptable but not independently confirmed here.

</details>

<details>
<summary>Details</summary>

### Why degrade-gracefully, not full alembic

The venv probe recorded in `alembic-verification.md` is the gate for the whole design: alembic, psycopg2, and psycopg are all `ModuleNotFoundError` in the `.venv` the suite runs in. A hard `import alembic` anywhere on the collection path would break the suite, so the lazy-import-with-fallback shape is forced by the environment rather than chosen for taste. `_run_alembic_upgrade` returns `False` on `ImportError` (and on a missing `alembic.ini`), and `cmd_migrate` only skips `create_schema()` when alembic actually ran. The additive backfill runs on both paths. This is consistent end to end: the alembic scaffolding is committed for real installs, the fallback keeps the alembic-less venv green, and no test imports alembic unguarded.

### The additive backfill and the stale-DB crash

`ensure_sqlite_columns` inspects each mapped table that already exists, diffs its live columns against the model, and emits `ALTER TABLE ... ADD COLUMN` for exactly the missing ones. The NOT-NULL handling is the subtle part and it is correct: SQLite rejects adding a NOT NULL column with no default against a table that has rows, so `_render_add_column` emits `NOT NULL DEFAULT <d>` when the model column carries a scalar default and otherwise falls back to a nullable add rather than emitting DDL SQLite would reject. `_scalar_default_sql` only renders plain constants (`default=False` → `0`, `default=0` → `0`) and returns `None` for callables like `utcnow`, so it never guesses a value for a timestamp. The additive set it backfills — `mfa_secret`, `mfa_enabled`, `mfa_last_counter`, `mfa_recovery_hashes`, `mfa_state_version`, `sessions.step_up_at` — is exactly the gap between the pre-MFA shape and the current model.

```
migrate ──► _run_alembic_upgrade(settings)
             ├─ alembic present ─► alembic upgrade head ─────────┐
             └─ absent/no ini   ─► warn, return False            │
                                                                 ▼
            async run(): if not ran_alembic: create_schema()  (missing TABLES)
                         ensure_additive_columns(db)           (missing COLUMNS, SQLite)
```

The regression test `test_migrate_upgrades_a_stale_db_without_the_no_such_column_crash` builds a current DB, rewrites `users` back to its pre-MFA DDL with a legacy row, re-runs `migrate`, then runs a `UserStore.by_username` that SELECTs `mfa_secret` — and it passes without alembic. The unit test goes further and asserts the pre-backfill query raises with `mfa_secret` in the message, then that the backfill adds exactly the five `users.*` columns and the legacy row survives with `mfa_enabled=False` / `mfa_state_version=0`. Idempotence is asserted directly (second run returns `[]`), and `test_migrate_is_idempotent_on_an_already_current_db` drives it through the CLI twice.

The one gap worth naming: this repair is keyed on tables that already exist and have no `alembic_version` bookkeeping. A dev who ran `create_all` first and then installs alembic would hit `alembic upgrade head` trying to `create_table("users")` on an existing table. That is outside the stated regression (which is explicitly the alembic-less path) and the additive backfill is the tested safety net for the SQLite dev case, so it is non-blocking — but a `stamp`-on-first-detect or a doc line would close it.

### Fresh install integrity on both backends

On the fallback path a brand-new DB has no tables, so `create_schema()` (`create_all`) builds the full schema and the backfill finds nothing to add. `test_migrate_fresh_db_builds_a_working_schema` proves a fresh `migrate` supports an insert+select round-trip including the `mfa_enabled`/`mfa_state_version` defaults. On the alembic path the baseline revision `0001_baseline` (`down_revision=None`) creates every table; `test_alembic_upgrade_path_is_available_when_installed` builds from scratch against a temp SQLite URL and asserts `mfa_secret`/`mfa_enabled`/`mfa_state_version` exist. Postgres fresh-install is not independently run here but uses the same `create_all` / baseline, and no Postgres-specific DDL was introduced.

### Baseline revision matches the models

I checked `0001_baseline_schema.py` against `models.py` column-for-column across all ten tables. The shapes line up, including the type mapping the ORM relies on: `EncryptedText` and `EncryptedJSON` both land as `sa.Text` (the Fernet cipher is applied in Python, so at the DB level these are plain TEXT and a migration needs no key), `UTCDateTime` as `sa.DateTime(timezone=True)`, and `JSON` as `sa.JSON`. Indexes match too — `ix_users_username` unique, the four `sessions` indexes, `ix_turns_session_created` composite, and the rest. Because the baseline is hand-written, it has to be kept in lockstep with the model on future changes — a maintenance obligation the file's docstring already names.

### Models and encryption unchanged

`models.py` is not in the diff, and the async drivers (`sqlite+aiosqlite`, `postgresql+asyncpg`) are untouched. The sync-driver conversion exists only inside the migration path, and `_sync_migration_url` in `__main__.py` and `_sync_url` in `env.py` must agree or the alembic and fallback paths would target different databases — they do agree (`sqlite+aiosqlite`→`sqlite`, `postgresql+asyncpg`→`postgresql+psycopg2`). The backfill adds `mfa_secret` as a plain nullable TEXT column, so the Python-side Fernet cipher is unaffected.

### Dependency isolation and pins

The `migrations` extra is declared in `pyproject.toml` as `["alembic>=1.13", "psycopg2-binary>=2.9"]` and is deliberately not folded into base `dependencies` or the `dev` extra, so a normal install never pulls a sync driver into the async runtime. Exact pins are present in `constraints.txt` (`alembic==1.13.3`, `psycopg2-binary==2.9.10`, added as bare `name==version` lines so `test_constraints_are_exact_pins`'s `name==version`-only rule holds) and in `requirements.txt` under a commented ops section. The verification record notes the constraints test initially failed on comment lines and was fixed by removing them; the committed diff reflects the pins-only form.

### Secret hygiene in the migration path

Neither `env.py` nor `alembic.ini` hardcodes a URL — the URL is derived at runtime from `Settings.effective_database_url`, so no connection string or password lives in a committed file. The `migrate` log lines are URL-free: `_run_alembic_upgrade` logs only `"alembic upgrade head complete"` / the fallback warning, and the final `schema ready` line logs `settings.describe()["database"]`, which is just `"sqlite"`/`"postgresql"`.

### Test coverage and what was not run

Covered: the additive backfill (fix, idempotence, current-schema no-op, non-SQLite no-op via a stub), the CLI fresh/stale/idempotent `migrate` flows, and the alembic baseline build (importorskip-guarded). The alembic-path test is correctly guarded so collection stays clean where alembic is absent.

Not independently verified here (and the review was instructed not to re-run the suites): the live `alembic upgrade head` and `--autogenerate` against a real alembic install were not executed — no throwaway env with alembic/pip was set up — so the versioned path is proven by the hand-reviewed baseline plus the importorskip test rather than an observed run. ruff was not run (not installed in the venv). The record's full-suite result (`2 failed, 324 passed` with the real `.env`, then `326 passed` with `.env` moved aside) attributes one failure to the pre-existing `.env`-driven `test_dev_allows_missing_secrets_but_reports_them` (unrelated: this change touches no config loading) and the other to the now-fixed constraints comment lines. That accounting is consistent with the diff.

### Python 3.10 and style

`schema_guard.py` uses `str | None` only in annotations under `from __future__ import annotations`, and `list[str]` appears as deferred annotations or runtime generics that 3.10 supports, so there is no 3.10 incompatibility. New files follow the existing per-file comment-forward style.

</details>

<details>
<summary>File map</summary>

- `jarvis-backend/src/jarvis_backend/storage/schema_guard.py` — new; SQLite-only additive `ADD COLUMN` backfill, no alembic import.
- `jarvis-backend/src/jarvis_backend/__main__.py` — `migrate` now tries lazy alembic `upgrade head`, else `create_schema()`, then the additive backfill; URL-free logs.
- `jarvis-backend/src/jarvis_backend/container.py` — dev+SQLite startup runs the backfill once so `serve` self-heals a stale DB.
- `jarvis-backend/src/jarvis_backend/storage/__init__.py` — export `ensure_additive_columns`.
- `jarvis-backend/alembic.ini` — minimal config; URL supplied by `env.py`, no secrets.
- `jarvis-backend/migrations/env.py` — derives a sync URL from `Settings`, batch mode on SQLite, targets `Base.metadata`.
- `jarvis-backend/migrations/versions/0001_baseline_schema.py` — hand-written baseline for the full current schema.
- `jarvis-backend/migrations/script.py.mako`, `migrations/versions/.gitkeep` — scaffolding.
- `jarvis-backend/pyproject.toml` — new optional `migrations` extra (not in base/dev).
- `constraints.txt`, `requirements.txt` — pin `alembic==1.13.3`, `psycopg2-binary==2.9.10`.
- `tests/unit/test_schema_guard.py`, `tests/integration/test_cli.py` — backfill + CLI migrate tests (alembic path importorskip-guarded).
- `docs/OPERATIONS.md`, `docs/DEPLOYMENT.md`, `docs/PRODUCTION_PLAN.md` — real `migrate` procedure replaces the manual `ALTER TABLE` note; Phase 5 marked done.

Full diff: `git diff HEAD~1` on branch `main`.

</details>
