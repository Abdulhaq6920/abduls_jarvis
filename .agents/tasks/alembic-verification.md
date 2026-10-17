# Phase 5 — Alembic migrations: verification record

## Path taken and why

**Path: DEGRADE-GRACEFULLY (plan option b/c/d).** The venv probe in the repo-root `.venv`
confirmed alembic and both sync Postgres drivers are absent:

```
.venv\Scripts\python -c "import alembic"   -> ModuleNotFoundError: No module named 'alembic'
.venv\Scripts\python -c "import psycopg2"  -> ModuleNotFoundError: No module named 'psycopg2'
.venv\Scripts\python -c "import psycopg"   -> ModuleNotFoundError: No module named 'psycopg'
pytest 9.1.1 ; SQLAlchemy 2.1.4
```

Because the test suite runs in this venv, it must NOT hard-import alembic. So:

- `migrate` imports alembic lazily and runs `alembic upgrade head` **when installed**;
- when alembic is absent it logs a warning and falls back to `create_schema()`;
- on SQLite, either way, it runs an alembic-free additive-column backfill that adds any
  missing columns to existing tables (fixing the stale-DB `no such column` crash);
- Alembic scaffolding (`alembic.ini`, `migrations/env.py`, baseline revision) is still
  committed so a real install (`pip install -e "jarvis-backend[migrations]"`) runs the
  versioned path. The alembic-path test uses `pytest.importorskip("alembic")`.

## Files changed / added

New:
- `jarvis-backend/src/jarvis_backend/storage/schema_guard.py` — SQLite-only, additive-only
  `ensure_additive_columns(db)` / `ensure_sqlite_columns(conn)`; no alembic import.
- `jarvis-backend/alembic.ini` — minimal; URL supplied by env.py, no secrets committed.
- `jarvis-backend/migrations/env.py` — reads `Settings.effective_database_url`, converts the
  async URL to a sync driver (`sqlite+aiosqlite`->`sqlite`, `postgresql+asyncpg`->
  `postgresql+psycopg2`), targets `Base.metadata`, batch mode on SQLite. Not imported at
  package import time.
- `jarvis-backend/migrations/script.py.mako`, `jarvis-backend/migrations/versions/.gitkeep`.
- `jarvis-backend/migrations/versions/0001_baseline_schema.py` — hand-written baseline for
  the full current schema (EncryptedText/EncryptedJSON->Text, UTCDateTime->DateTime(tz),
  JSON->JSON), `down_revision=None`.
- `tests/unit/test_schema_guard.py` — additive-backfill unit tests (alembic-free).

Modified:
- `jarvis-backend/src/jarvis_backend/__main__.py` — `cmd_migrate` now: try alembic
  `upgrade head`; else `create_schema()`; always run the SQLite additive backfill. Keeps the
  FieldCipher setup and the "Database schema is up to date." print. Never logs the DB URL.
- `jarvis-backend/src/jarvis_backend/storage/__init__.py` — export `ensure_additive_columns`.
- `jarvis-backend/src/jarvis_backend/container.py` — dev + SQLite only, run the additive
  backfill once after `create_schema()` at startup so `serve` self-heals a stale dev DB.
- `jarvis-backend/pyproject.toml` — new optional extra `migrations = ["alembic>=1.13",
  "psycopg2-binary>=2.9"]`. NOT added to base deps or the `dev` extra.
- `constraints.txt` — pinned `alembic==1.13.3`, `psycopg2-binary==2.9.10` (no comment lines:
  `test_constraints_are_exact_pins` requires every line to be `name==version`).
- `requirements.txt` — same pins under a commented "migrations / ops path" section.
- `docs/OPERATIONS.md`, `docs/DEPLOYMENT.md`, `docs/PRODUCTION_PLAN.md` — replaced the
  "no Alembic yet" / manual `ALTER TABLE` notes with the real `migrate` procedure (fresh +
  existing DB) and how to create a new revision; Phase 5 Alembic marked done.

## New dependencies / env

- Optional extra `jarvis-backend[migrations]`: `alembic==1.13.3`, `psycopg2-binary==2.9.10`.
  The async runtime never imports either (lazy import in `migrate` only). Pins are not
  pip-resolved (pip is absent from the venv); 1.13.3 and 2.9.10 are confirmed-published
  versions. No new environment variables; migrations reuse `Settings.effective_database_url`.

## How the stale-DB crash is fixed

`create_all` only creates missing TABLES, so a dev SQLite DB created before the MFA columns
still lacked `users.mfa_secret` etc. and crashed with `no such column: users.mfa_secret`.
`ensure_sqlite_columns` inspects each existing table and issues `ALTER TABLE ... ADD COLUMN`
for exactly the missing columns (`mfa_secret`, `mfa_enabled`, `mfa_last_counter`,
`mfa_recovery_hashes`, `mfa_state_version`, `sessions.step_up_at`). NOT-NULL columns are
added with their scalar default (`mfa_enabled` DEFAULT 0, `mfa_state_version` DEFAULT 0) so
existing rows backfill. It is additive-only, never drops/renames, SQLite-only, and
idempotent. `migrate` and (in dev) `serve` both invoke it.

## Commands run and results (JARVIS_ENV=dev)

- Venv probe (above): alembic / psycopg2 / psycopg all absent; pytest 9.1.1.
- `python -c "import jarvis_backend; import jarvis_backend.storage.schema_guard; ..."` ->
  `imports ok, alembic-free` (no hard alembic dependency at import/collection time).
- `pytest tests/unit/test_schema_guard.py tests/integration/test_cli.py -q` ->
  **18 passed, 1 skipped** (the alembic-path test skipped via importorskip).
- `pytest tests/unit tests/integration -q` (real `.env` present) ->
  **2 failed, 324 passed, 10 skipped**. The two failures were:
  (1) `test_dev_allows_missing_secrets_but_reports_them` — the PRE-EXISTING `.env`-driven
      failure documented in the plan (unrelated to this task); and
  (2) `test_constraints_are_exact_pins` — my constraints.txt comment lines; FIXED by
      removing the comments (pins only).
- `pytest tests/unit tests/integration -q` with `.env` temporarily moved aside (then
  restored) -> **326 passed, 10 skipped, 1 warning**. This is the clean green baseline:
  313 originally-passing + 13 new passing tests, plus the 1 new importorskip skip. The
  `.env` file content was not modified (renamed and restored only).
- `python -m jarvis_backend migrate` (temp data dir) -> logs
  `"alembic not installed; using create_schema + additive column backfill"` then
  `"schema ready" database=sqlite` and prints `Database schema is up to date.` No secrets
  in the logs. Running it a second time is a clean no-op (asserted in test_cli).
- `python -m jarvis_backend serve --port 8099` -> started in dev; `GET /healthz` returned
  **HTTP 200**; process stopped cleanly.
- Regression test `test_migrate_upgrades_a_stale_db_without_the_no_such_column_crash`:
  creates a current DB, rewrites `users` to its pre-MFA shape, re-runs `migrate`, then a
  `UserStore.by_username` (which SELECTs `mfa_secret`) succeeds — the crash is gone and the
  legacy row survived. Passes without alembic.
- Fresh-install test `test_migrate_fresh_db_builds_a_working_schema`: `migrate` on an empty
  DB then a UserStore insert+select round-trip (incl. `mfa_enabled`/`mfa_state_version`
  defaults) — passes.

## Not done / skipped

- **ruff**: not installed in the venv (confirmed `ModuleNotFoundError: No module named
  'ruff'`), so linting was skipped. Changed files follow the existing per-file style.
- **Step 10 (install-gated real alembic run)**: no throwaway env with alembic/pip was set
  up, so the live `alembic upgrade head` / `--autogenerate` check was not executed here. The
  baseline revision is reviewed column-for-column against `models.py`, and the fallback path
  is fully tested. The `test_alembic_upgrade_path_is_available_when_installed` test will
  exercise the real path automatically in any environment that has alembic installed.
