# Implementation Plan — Phase 5 Database Migrations (Alembic + safe additive fallback)

## Context discovered during exploration (read this first)

**The venv probe decides the architecture. Recorded results (run in the repo-root `.venv`):**

| Probe | Command | Result |
|---|---|---|
| alembic | `.venv\Scripts\python -c "import alembic"` | **NOT importable** (`ModuleNotFoundError: No module named 'alembic'`) |
| psycopg2 | `.venv\Scripts\python -c "import psycopg2"` | **NOT importable** |
| psycopg (v3) | `.venv\Scripts\python -c "import psycopg"` | **NOT importable** |
| sqlite3 | `.venv\Scripts\python -c "import sqlite3"` | available (stdlib) |
| pytest | — | 9.1.1 available |

**Chosen path: the DEGRADE-GRACEFULLY design (task option b/c/d), because alembic is not in the test venv.**
Justification: tests run in this `.venv`, which has neither alembic nor a sync Postgres driver. Therefore:
- The test suite must NOT hard-import alembic at collection/runtime (would break collection).
- `migrate` must detect alembic's absence and fall back to today's `create_schema()` **plus** a new
  alembic-independent SQLite additive-column backfill that actually fixes the stale-DB crash.
- Alembic scaffolding (`alembic.ini`, `migrations/env.py`, a baseline `versions/` revision) is still
  created so a real install (`pip install -e "jarvis-backend[migrations]"`) runs `upgrade head`.
- The SQLite backfill uses the **already-installed async engine's sync inspector** (`run_sync` +
  `inspect`), never alembic and never a raw `sqlite3` import, so it works in the venv today.

**Baseline test status — IMPORTANT known deviation (not caused by this task):**
`pytest tests/unit tests/integration -q` with `JARVIS_ENV=dev` currently reports **312 passed, 1 failed,
9 skipped**, not the 313/9 the task quotes. The single failure is
`tests/unit/test_config_and_crypto.py::test_dev_allows_missing_secrets_but_reports_them`. Root cause:
a real `c:\Users\Lenovo\Desktop\JARVIS\abduls_jarvis\.env` (created earlier for the demo) sets
`JARVIS_JWT_SECRET` and `JARVIS_DATA_KEY`; pydantic-settings loads `.env`, so `Settings(env="dev")`
in that test sees the secrets *present* and emits no `jwt_secret_weak`/`data_key_missing` findings.
This is pre-existing and unrelated to migrations.
- To reproduce the real green baseline, run the suite with those two vars absent, e.g. temporarily
  rename `.env`, or run with `JARVIS_JWT_SECRET`/`JARVIS_DATA_KEY` cleared for the test process only.
- **Do NOT "fix" this test or touch `.env` as part of this task.** Simply confirm your changes add the
  expected number of new passing tests and introduce **no new failures** beyond this known one. If the
  reviewer runs with `.env` present, the acceptance target is "312 + N new passed, 1 pre-existing fail,
  9 skipped"; with `.env` absent it is "313 + N passed, 9 skipped".

**Key facts the implementation depends on:**
- `Settings.effective_database_url` (config.py) returns `sqlite+aiosqlite:///<data_dir>/jarvis.db`
  when `database_url` is empty, else `postgresql+asyncpg://...`. `Settings.is_sqlite` is the switch.
  Both are **async** drivers. Alembic is sync, so env.py must convert to a sync URL
  (`sqlite+aiosqlite` → `sqlite`; `postgresql+asyncpg` → `postgresql+psycopg2`) OR use alembic's async
  engine helper. Chosen: **sync URL conversion** (simpler, matches "alembic is sync").
- `Database.create_schema()` (storage/db.py) does `Base.metadata.create_all` inside
  `engine.begin()/run_sync`. SQLite path sets WAL/foreign_keys/busy_timeout PRAGMAs on connect.
- `cmd_migrate` (`__main__.py`) builds `Database(settings)`, `await db.create_schema()`, prints
  "Database schema is up to date." It already sets the FieldCipher. This is the single integration point.
- `models.py`: `Base = DeclarativeBase`; `Base.metadata` is the authoritative metadata. The additive
  columns on existing tables are, from the OPERATIONS.md manual-ALTER note:
  - `users`: `mfa_secret` (EncryptedText→TEXT, nullable), `mfa_enabled` (Boolean, NOT NULL default
    False), `mfa_last_counter` (Integer, nullable), `mfa_recovery_hashes` (EncryptedJSON→TEXT,
    nullable), `mfa_state_version` (Integer, NOT NULL default 0).
  - `sessions`: `step_up_at` (UTCDateTime→TIMESTAMP, nullable).
  EncryptedText/EncryptedJSON are TEXT at the DB level; cipher is applied in Python, so the backfill
  adds plain TEXT columns and needs no data key.
- `tests/conftest.py` `db` fixture calls `create_schema()` directly (not the CLI). `tests/integration/
  test_cli.py` drives `main([...])`. `tests/integration/test_postgres.py` is auto-skipped (no pgserver).

**Dependency manifests to pin into:** `jarvis-backend/pyproject.toml` (add a `migrations` optional
extra), `constraints.txt`, root `requirements.txt`. Pin alembic `==1.14.1` and psycopg2-binary
`==2.9.10` (for the Postgres ops/migration path only; sqlite migrations use stdlib). These are the
latest stable at time of writing; the coder MUST confirm the exact available version if pip is used
and adjust the pin to a real published version.

---

# Implementation Plan

- [ ] 1. Create the alembic-independent SQLite additive-column safety module.
      Add `ensure_sqlite_columns(engine_or_sync_conn)` that, for SQLite only, inspects each mapped
      table via SQLAlchemy's sync `inspect(...).get_columns(table)` (run under `conn.run_sync`),
      compares against `Base.metadata.tables`, and issues `ALTER TABLE <t> ADD COLUMN <col> <type>`
      ONLY for additive missing columns (never drop/alter/rename). Render column DDL from the
      SQLAlchemy column's compiled type on the SQLite dialect; for NOT-NULL columns with a default
      (`mfa_enabled`, `mfa_state_version`) emit `NOT NULL DEFAULT <default>` so existing rows backfill
      (SQLite requires a default when adding NOT NULL). Expose an async wrapper
      `async def ensure_additive_columns(db: Database)` that no-ops on non-SQLite and runs the sync
      inspection+DDL inside `db.engine.begin()/run_sync` on SQLite. Keep it idempotent (skip columns
      that already exist). No alembic import anywhere in this module.
      Files: `jarvis-backend/src/jarvis_backend/storage/schema_guard.py` (new);
      export `ensure_additive_columns` from `jarvis-backend/src/jarvis_backend/storage/__init__.py`.
      Verify: `JARVIS_ENV=dev .venv\Scripts\python -m pytest tests/unit/test_schema_guard.py -q`
      (added in step 6) passes; `.venv\Scripts\python -c "import jarvis_backend.storage.schema_guard"`
      succeeds with alembic absent.

- [ ] 2. Create the Alembic scaffolding under the backend package so a real install can run it.
      Create `alembic.ini` (minimal: `script_location = migrations`, no hardcoded `sqlalchemy.url`
      — env.py supplies it from Settings) and a `migrations/` dir with `env.py`, `script.py.mako`,
      and `versions/`. `env.py` must: import `Settings`/`get_settings` and `Base.metadata`; derive a
      SYNC url from `settings.effective_database_url` by replacing `sqlite+aiosqlite` → `sqlite` and
      `postgresql+asyncpg` → `postgresql+psycopg2`; set `target_metadata = Base.metadata`; configure
      `context.configure(..., render_as_batch=settings.is_sqlite)` so SQLite uses batch mode for any
      future non-additive op; support both offline and online modes. Do NOT hardcode secrets. Place
      scaffolding at `jarvis-backend/migrations/` (sibling of `src/`) with `script_location` resolved
      relative to `alembic.ini`'s location; the `migrate` command (step 4) must locate `alembic.ini`
      via `importlib`/package path, not CWD.
      Files: `jarvis-backend/alembic.ini` (new), `jarvis-backend/migrations/env.py` (new),
      `jarvis-backend/migrations/script.py.mako` (new), `jarvis-backend/migrations/versions/.gitkeep`.
      Verify: file-structure review only here (alembic not installed); functional verification happens
      in step 10 (optional, install-gated) and via the fallback tests in steps 6–7. Confirm
      `.venv\Scripts\python -c "import jarvis_backend"` still imports (env.py is not imported at
      package import time).

- [ ] 3. Author the baseline Alembic revision capturing the CURRENT full schema.
      Create `migrations/versions/0001_baseline_schema.py` with `down_revision = None`. Its `upgrade()`
      creates every current table (users incl. all `mfa_*` + `mfa_state_version`, sessions incl.
      `step_up_at`, devices, audit_log, approvals, turns, memories, agent_settings, job_runs, kv) with
      columns/indexes matching `models.py` exactly (EncryptedText/EncryptedJSON → `sa.Text`;
      UTCDateTime → `sa.DateTime(timezone=True)`; JSON → `sa.JSON`). `downgrade()` drops them. Hand-write
      it from `models.py` (autogenerate is unavailable without alembic installed). Keep it as the single
      source for a fresh alembic-driven install; `create_all` remains the fast path and must stay
      consistent with it.
      Files: `jarvis-backend/migrations/versions/0001_baseline_schema.py` (new).
      Verify: reviewed against `models.py` column-by-column (checklist in the PR description). Runtime
      verification is install-gated (step 10); the venv path never executes this file.

- [ ] 4. Rewire `cmd_migrate` in `__main__.py` to the three-tier upgrade with graceful degradation.
      Replace the body so it: (1) tries to import alembic and run `upgrade head` programmatically
      (`alembic.config.Config(<alembic.ini path>)` + `command.upgrade(cfg, "head")`, with the sync URL
      from Settings); (2) if alembic import fails, log a WARNING "alembic not installed; using
      create_schema + additive column backfill" and run `await db.create_schema()`; (3) ALWAYS, on
      SQLite, run `await ensure_additive_columns(db)` afterward so an existing stale dev DB gets the
      missing `mfa_*`/`step_up_at` columns whether or not alembic ran. Keep it idempotent and keep the
      existing FieldCipher setup and the final "Database schema is up to date." print. Never log secrets.
      Files: `jarvis-backend/src/jarvis_backend/__main__.py`.
      Verify: `JARVIS_ENV=dev .venv\Scripts\python -m pytest tests/integration/test_cli.py -q` passes
      (existing migrate/CLI tests + new ones from step 7); manual
      `JARVIS_ENV=dev .venv\Scripts\python -m jarvis_backend migrate` prints the ready line and logs the
      fallback warning.

- [ ] 5. (Optional hardening) Call the additive backfill on dev startup so `serve` self-heals a stale DB.
      In the serve/startup path (where `Database` is created — confirm via `app.py`/`container.py`
      before editing), when `settings.env == "dev"` and SQLite, invoke `ensure_additive_columns(db)`
      once during startup. Guard it so prod/Postgres is unaffected (prod uses explicit `migrate`). If
      the startup wiring is non-trivial or risks changing app init order, SKIP this step — step 4 already
      satisfies requirement 2 — and note the decision in the PR. Do not add a hard alembic dependency.
      Files: `jarvis-backend/src/jarvis_backend/app.py` and/or
      `jarvis-backend/src/jarvis_backend/container.py` (read first; edit only the DB-creation site).
      Verify: `JARVIS_ENV=dev .venv\Scripts\python -m pytest tests/unit tests/integration -q` — no new
      failures; `.venv\Scripts\python -m jarvis_backend serve` starts (smoke: Ctrl-C after bind).

- [ ] 6. Add unit tests for the additive backfill (alembic-FREE, must run in the venv).
      New `tests/unit/test_schema_guard.py`: build a Settings pointing at a temp SQLite file; create an
      OLD `users` table missing the `mfa_*` columns (e.g. run `create_schema()` then
      `ALTER TABLE users DROP COLUMN mfa_secret ...` is not portable on SQLite — instead create a minimal
      old-schema `users` table by hand with only the pre-MFA columns, or create_all then use a raw
      `CREATE TABLE users_old` strategy). Simplest robust approach: open a raw connection, `DROP TABLE
      users`, recreate `users` with only id/username/password_hash/role/disabled/created_at/
      password_changed_at/last_login_at, insert one row. Then call `ensure_additive_columns(db)` and
      assert: a `UserStore(db).by_username(...)` / select touching `mfa_secret` + `mfa_state_version`
      succeeds (no "no such column"), the existing row has `mfa_enabled=0`/`mfa_state_version=0`
      backfilled, and a second call is a no-op (idempotent). Add a case asserting non-SQLite returns
      without error (use a mock/branch, do not require Postgres). NO alembic import.
      Files: `tests/unit/test_schema_guard.py` (new).
      Verify: `JARVIS_ENV=dev .venv\Scripts\python -m pytest tests/unit/test_schema_guard.py -q` passes.

- [ ] 7. Add the exact stale-DB regression test driving the `migrate` CLI (alembic-FREE path).
      New test in `tests/integration/test_cli.py` (reuse the `workdir` fixture): seed secrets, run
      `main(["migrate"])` once to create a fresh DB, then simulate an OLD DB by recreating a pre-MFA
      `users` table (as in step 6) in the same SQLite file, then run `main(["migrate"])` AGAIN and assert
      it returns 0 and that a subsequent `UserStore` query selecting `mfa_secret` succeeds (reproduces
      and fixes `no such column: users.mfa_secret`). Also assert running `migrate` twice on an already-
      current DB is a no-op (returns 0, no error). Guard any alembic-dependent branch with
      `pytest.importorskip("alembic")` in a SEPARATE test; the additive-safety assertions run WITHOUT
      alembic so they execute in the venv.
      Files: `tests/integration/test_cli.py`.
      Verify: `JARVIS_ENV=dev .venv\Scripts\python -m pytest tests/integration/test_cli.py -q` passes.

- [ ] 8. Add a fresh-install `migrate` test proving a working schema end to end.
      New test (in `tests/integration/test_cli.py`): on an empty data dir, run `main(["create-admin",
      ...])`-style seeding is heavy; instead run `main(["migrate"])` on an empty DB, then open a
      `Container`/`UserStore` on the same settings and do an insert+select round-trip
      (`UserStore(db).create(...)` then `by_username(...)` returns the row), proving all tables (incl.
      `mfa_*`) exist. Keep it alembic-free so it runs in the venv.
      Files: `tests/integration/test_cli.py`.
      Verify: `JARVIS_ENV=dev .venv\Scripts\python -m pytest tests/integration/test_cli.py -q` passes.

- [ ] 9. Add dependency pins for the alembic/ops path (does not affect async runtime).
      In `jarvis-backend/pyproject.toml` add an optional extra:
      `migrations = ["alembic>=1.13", "psycopg2-binary>=2.9"]` (psycopg2-binary only needed for the
      Postgres sync migration driver; sqlite migrations use stdlib). In `constraints.txt` and root
      `requirements.txt` add pinned lines `alembic==1.14.1` and `psycopg2-binary==2.9.10` under a clearly
      commented "migrations / ops path (not imported by the async runtime)" section. CONFIRM both pins
      are real published versions before finalizing (adjust if pip resolves differently). Do NOT add
      alembic to the backend's base `dependencies` or to the `dev` extra used by the test venv — tests
      must keep passing with alembic absent.
      Files: `jarvis-backend/pyproject.toml`, `constraints.txt`, `requirements.txt`.
      Verify: `JARVIS_ENV=dev .venv\Scripts\python -m pytest tests/unit tests/integration -q` — still
      no new failures (nothing new imported at runtime). Ruff clean on changed Python files.

- [ ] 10. (Install-gated, optional) Prove the real Alembic path if a throwaway venv with alembic exists.
      In a separate throwaway environment only (never the repo `.venv`), `pip install alembic`, then from
      `jarvis-backend/` run `alembic upgrade head` against a temp SQLite URL and confirm the baseline
      revision creates the full schema; and `alembic revision --autogenerate -m "noop"` produces an empty
      diff against current models (proves env.py metadata wiring). This validates steps 2–3 without
      touching the test venv. If no such environment is available, SKIP and note it; the fallback path is
      already fully tested by steps 6–8.
      Files: none (verification only).
      Verify: `alembic upgrade head` succeeds and `--autogenerate` yields no operations, OR documented as
      skipped.

- [ ] 11. Update the docs to the real `migrate` procedure and mark Phase 5 Alembic done.
      - `docs/OPERATIONS.md`: replace the "Schema note (no Alembic yet)" block and its SQL `ALTER TABLE`
        snippet with: fresh install and existing-DB upgrade both run `python -m jarvis_backend migrate`;
        explain that it runs Alembic `upgrade head` when installed and otherwise falls back to
        `create_schema` + the additive SQLite backfill; document creating a new revision after a model
        change (`pip install -e "jarvis-backend[migrations]"`, then `alembic revision --autogenerate -m
        "<change>"`, review, commit, `migrate`). Keep the `mfa_state_version` default-0 note.
      - `docs/DEPLOYMENT.md`: in section 2, keep `migrate` once for fresh installs and add an
        "upgrading an existing database" line pointing at the same command; note the Postgres migration
        path needs the `migrations` extra (psycopg2-binary) installed.
      - `docs/PRODUCTION_PLAN.md`: change the Phase 5 "Add Alembic migrations" bullet to `[x]` done and
        update the "Note (no Alembic yet)" lines to reference the shipped `migrate` upgrade procedure.
      Files: `docs/OPERATIONS.md`, `docs/DEPLOYMENT.md`, `docs/PRODUCTION_PLAN.md`.
      Verify: docs review; grep confirms no remaining "no Alembic yet" / "until Phase 5" wording.

- [ ] 12. Full regression gate.
      Run the complete suite and confirm no new failures versus the recorded baseline. Account for the
      known pre-existing `.env`-driven failure (see Context): with `.env` present expect
      `312 + N passed, 1 pre-existing failed, 9 skipped`; with the two secret env vars cleared/`.env`
      renamed expect `313 + N passed, 9 skipped`, where N is the count of tests added in steps 6–8.
      Then run ruff on all changed files.
      Files: none (verification only).
      Verify: `JARVIS_ENV=dev .venv\Scripts\python -m pytest tests/unit tests/integration -q` — only the
      known pre-existing failure may remain; all new tests pass. `ruff check` clean on changed files
      (ruff is not in the venv — run via `pipx`/throwaway env or note it was skipped if unavailable).

---

## Notes, assumptions, and gaps
- **Assumption (version pins):** alembic `1.14.1` and psycopg2-binary `2.9.10` are placeholders for the
  latest stable; since pip is absent from the test venv, the coder must confirm real published versions
  before committing the pins. The async runtime never imports either, so a wrong pin cannot break tests.
- **Assumption (sync Postgres driver):** psycopg2-binary chosen over psycopg(v3) because it is the
  conventional, broadly-available sync driver for alembic + Postgres and matches the `postgresql+psycopg2`
  URL in env.py. The app runtime keeps `postgresql+asyncpg`.
- **Decision (no FEAT restructure):** this is one focused, cohesive change; the workflow's single-plan
  implement-and-review loop is the right shape, so the remaining steps were not restructured.
- **Gap (step 5 wiring):** startup self-heal depends on the exact `Database` construction site in
  app/container; the coder must read those before editing and may skip step 5 if it risks init order.
  Requirement 2 is already met by step 4's `migrate`.
- **Encryption untouched:** EncryptedText/EncryptedJSON are TEXT at the DB level; the backfill and the
  baseline revision add TEXT columns only — no data key needed, cipher stays Python-side.
- **Do not** change model shapes, async runtime drivers, or the pre-existing failing config test / `.env`.
