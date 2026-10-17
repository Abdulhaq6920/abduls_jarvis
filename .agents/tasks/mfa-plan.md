# Implementation Plan — Phase 4: TOTP MFA + Step-Up Re-Auth

Repository: `c:\Users\Lenovo\Desktop\JARVIS\abduls_jarvis` — edit IN PLACE on branch `main`, no worktree.
Python 3.10+ monorepo (jarvis-shared / jarvis-backend / jarvis-local-agent). Lint ruff (ruleset `S` on), tests pytest `asyncio_mode=auto`.

Run tests with: `$env:JARVIS_ENV="dev"; .\.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q`
Baseline on main is GREEN: **267 passed, 5 skipped** (5 skips = optional postgres pgserver tests, expected). Do not regress.

---

## Exploration findings the coder MUST rely on (do not re-derive)

- **`pyotp` is NOT installed** in the repo-root `.venv`, and the venv **lacks pip** (cannot be installed). `python -c "import pyotp"` → `ModuleNotFoundError`. `qrcode` is also absent.
  → **DECISION: implement RFC 6238 TOTP in stdlib** (`hmac` + `hashlib` + `base64` + `struct` + `secrets`), ~40 lines. The runtime code MUST NOT `import pyotp` (tests would fail). `pyotp` is still added to the dependency manifests per the task as the documented/intended dep, but it is never imported — a top-of-file comment must say so. Rationale: fail-closed verification cannot depend on an un-importable package.
- **QR: no image/QR library available.** CSP is `img-src 'self' data:` (see `middleware.py` `CSP`), so a server-produced `data:` URI `<img>` is allowed, but there is no pure-python QR lib and we must not add a heavy one.
  → **DECISION: always return the `otpauth://totp/<issuer>:<user>?secret=...&issuer=<issuer>&algorithm=SHA1&digits=6&period=30` URI + the base32 secret for manual entry.** Render a QR only as an OPTIONAL stdlib-drawn SVG `data:` URI if trivially feasible; otherwise manual entry (URI + secret text) is the shipped path. Manual entry MUST always work. No inline script, no CDN.
- **RiskLevel** (`jarvis_shared/security.py`): `SAFE`, `RESTRICTED`, `DANGEROUS`. The `ApprovalRow.risk` column (`models.py`) stores these strings; `ApprovalOut.risk` is a plain `str`.
- **Field encryption** (`crypto.py`): `EncryptedText` / `EncryptedJSON` SQLAlchemy `TypeDecorator`s backed by process-wide `FieldCipher` (Fernet). Use `EncryptedText` for the TOTP secret column exactly like `ApprovalRow.summary`.
- **Password hashing** (`auth/passwords.py`): argon2id via module-level `_hasher`, async `hash_password` / `verify_password` (to_thread, constant-effort dummy-hash). Mirror this to hash recovery codes — reuse `hash_password` / a dedicated argon2 verify; NEVER store recovery codes in plaintext.
- **Token codec** (`auth/tokens.py`): HS256, `ACCESS = "access"`, required claims `exp iat nbf iss aud sub jti sid typ role`, `verify()` rejects any `typ != ACCESS`. A new short-lived token kind MUST be minted here with its own `typ` and go through the SAME `jwt.decode(..., options={"require": _REQUIRED})` rigor. `current_principal` → `authenticate()` only ever calls `codec.verify()` which hard-rejects non-`access` typ, so an mfa_pending token can never authenticate a normal request.
- **AuthService** (`auth/service.py`): `login()` applies **three** rate limits (`login:pair:<ip>:<name>` at `limit`, `login:user:<name>` at `limit*4`, `login:ip:<ip>` at `limit*6`) then verifies password and calls `_open_session()`. `Principal` is `(subject, role, sid, kind)`. `RateLimiter.enforce(key, limit, window)` raises 429.
- **Stores** (`storage/identity.py`): `UserStore` (by_username / by_id / create / set_password / touch_login), `SessionStore`. `UserRow` / `SessionRow` in `storage/models.py`. Schema via `Base.metadata.create_all` (`db.py` `create_schema`) — **NO Alembic**: new columns/tables are picked up for FRESH DBs only; existing prod DBs need a manual `ALTER` (document this).
- **Approvals**: route `POST /api/approvals/{id}/decision` (`routes_approvals.py`, `admin_only`) → `ApprovalStore.resolve()`. `ApprovalDecision` schema (`schemas.py`) currently only `{granted: bool}`. The resolved `ApprovalOut` carries `.risk`.
- **Config** (`config.py`): `Settings` with `env_prefix="JARVIS_"`, `SecretStr`, `findings()` with `err()` (error in prod / warning in dev) and `warn()`. Add settings near the `--- authentication ---` block following `login_attempts_per_window` style.
- **Container** (`container.py`): builds `AuthService` and `ApprovalStore`; both get the stores they need here. `codec` is on the container (`c.codec`), used by tests via `client.app.state.container.codec`.
- **Dashboard** (`web/js/`): `dom.js` `el()` (createElement only, no innerHTML; `applyStyle` writes per-property for CSP), `api.js` (`login`, `api`, in-memory access token, single-flight refresh), `app.js` login form, `views/approvals.js` `decide()`, `views/system.js` account-security card pattern.
- **CLI** (`__main__.py`): subcommand pattern with `cmd_*` + argparse; `create-admin` shows the Container usage. Add an MFA enrollment command here.
- **Tests**: `tests/conftest.py` exposes `client`, `admin`, `agent`, `device` fixtures + `login()` / `bearer()` helpers and `ADMIN_USER/ADMIN_PASSWORD/JWT_SECRET`. `tests/integration/test_auth.py` is the pattern for HTTP auth tests. `async def test_` needs no decorator.
- Pinned current versions in `constraints.txt` / root `requirements.txt`: argon2-cffi 25.1.0, PyJWT 2.15.1, cryptography 50.0.2.

### Security invariants (restated — every item below must preserve these)
1. **Fail closed**: any ambiguity in MFA/step-up DENIES (missing/malformed code, un-decryptable secret, unknown state → reject).
2. TOTP secret **encrypted at rest** (`EncryptedText`), never logged, never returned after confirmation.
3. Recovery codes stored **only as argon2 hashes**, shown once, single-use.
4. **No MFA bypass**: `mfa_pending` / `step_up` tokens have distinct `typ` and are rejected by `current_principal`/`require_role` (they are not `typ=access`).
5. **Rate-limit the TOTP step** with the same three keys as password login; brute-forcing 6 digits must throttle.
6. **Constant-time compare** for TOTP + recovery codes (`hmac.compare_digest`); **reject replayed TOTP** within the same 30s step (store `mfa_last_counter`).
7. Step-up **adds a gate, never removes one**: a grant still cannot unlock a hard block; separation of duties intact (agent/device still 403 on `/decision`).
8. A stolen live admin session alone must NOT approve a DANGEROUS action when MFA is enabled.
9. Never log secrets/codes/recovery codes; `SecretStr` where applicable.

---

## Plan (ordered by dependency; each item leaves the tree buildable)

- [ ] 1. Add `pyotp` to the three dependency manifests (documented dep; code will NOT import it).
      Pin a current version (`pyotp==2.9.0`) in root `requirements.txt` (under the "Backend auth / security" block) and `constraints.txt` (alphabetical slot), and add `"pyotp>=2.9"` to `jarvis-backend/pyproject.toml` runtime `dependencies`. Add a one-line comment in `pyproject.toml` noting the stdlib fallback is the active implementation because the offline venv lacks pyotp.
      Files: `requirements.txt`, `constraints.txt`, `jarvis-backend/pyproject.toml`
      Verify: `$env:JARVIS_ENV="dev"; .\.venv\Scripts\python.exe -m pytest tests/unit/test_deployment_files.py -q` — passes (that test reads the manifests; confirm it still accepts them, update its expectations if it enumerates deps).

- [ ] 2. Create the stdlib TOTP module `jarvis-backend/src/jarvis_backend/auth/totp.py`.
      Pure-stdlib RFC 6238 (`import base64, hmac, hashlib, secrets, struct, time`). Functions: `generate_secret() -> str` (base32, 20 random bytes), `totp_now(secret, counter=None) -> str` (6 digits, SHA1, 30s period), `current_counter(ts=None) -> int`, `verify(secret, code, *, window=1, last_counter=None) -> Optional[int]` returning the matched counter (so caller can enforce no-replay) or `None`, using `hmac.compare_digest` on the 6-digit strings, and `otpauth_uri(secret, account, issuer) -> str`. Module docstring states it is stdlib (not pyotp) and why. Follow `from __future__ import annotations` + `typing.Optional` style; keep ruff-`S`-clean (no `random`, use `secrets`).
      Files: `jarvis-backend/src/jarvis_backend/auth/totp.py`
      Verify: added in item 14's unit tests; interim `.\.venv\Scripts\python.exe -c "from jarvis_backend.auth import totp; s=totp.generate_secret(); c=totp.totp_now(s); assert totp.verify(s,c) is not None; print('ok')"`.

- [ ] 3. Add a `STEP_UP`/`MFA_PENDING` token kind to `auth/tokens.py`.
      Add `MFA_PENDING = "mfa_pending"` and `STEP_UP = "step_up"` constants. Add `TokenCodec.issue_special(*, sub, sid, typ, ttl_seconds, role="admin") -> str` that mints a JWT with the given `typ` using the full required-claims payload, and `verify_special(token, *, expected_typ) -> Claims` that reuses the same `jwt.decode(..., options={"require": _REQUIRED})` path and raises `TokenError` unless `typ == expected_typ`. Do NOT relax `verify()` — it must keep rejecting everything that is not `typ=access`.
      Files: `jarvis-backend/src/jarvis_backend/auth/tokens.py`
      Verify: covered by item 14 unit tests (`verify()` rejects an mfa_pending token; `verify_special` accepts only its own typ).

- [ ] 4. Add the MFA DB columns to `UserRow` and the step-up timestamp to `SessionRow` in `storage/models.py`.
      On `UserRow`: `mfa_secret: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)` (base32 TOTP secret, field-encrypted), `mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)`, `mfa_last_counter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)` (replay guard), `mfa_recovery_hashes: Mapped[Optional[list]] = mapped_column(EncryptedJSON, nullable=True)` (list of argon2 hash strings). On `SessionRow`: `step_up_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)`. Match surrounding column style exactly.
      Files: `jarvis-backend/src/jarvis_backend/storage/models.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_storage.py -q` — passes (create_all builds the new columns on fresh SQLite).

- [ ] 5. Extend `UserStore` and `SessionStore` in `storage/identity.py` with MFA/step-up persistence.
      `UserStore`: `set_mfa_pending(user_id, secret)` (store secret, `mfa_enabled=False`), `enable_mfa(user_id, recovery_hashes)` (`mfa_enabled=True`), `disable_mfa(user_id)` (null secret/counter/recovery, `mfa_enabled=False`), `set_mfa_counter(user_id, counter)`, `consume_recovery_hash(user_id, index)` (remove one hash atomically). `SessionStore`: `mark_step_up(session_id, at=None)` and ensure `get()` already returns `step_up_at`. Keep the `async with self._db.session()` / `update(...).where(...)` idioms.
      Files: `jarvis-backend/src/jarvis_backend/storage/identity.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_storage.py -q` — passes (add store-level assertions here or defer to item 14).

- [ ] 6. Add MFA/step-up settings to `config.py` and a prod `warn()` finding.
      Under `--- authentication ---`: `mfa_issuer: str = "JARVIS"`, `mfa_recovery_codes: int = 10`, `step_up_window_seconds: int = 300`, `approval_require_mfa: bool = False`, `approval_step_up_risk: str = "DANGEROUS"` (threshold: `DANGEROUS` only by default; `RESTRICTED` would also require step-up when set to `RESTRICTED`). In `findings()`, add a `warn()` (NOT `err()` — must not block startup) when `is_prod` and the config indicates no admin has MFA — since `findings()` has no DB access, implement the "no admin has MFA" surfacing as a check in `routes_system` status/`check-config` runtime path (item 12) and keep `findings()` limited to a static warning that `approval_require_mfa` is off if that is desired. Add the new vars to `describe()` only if non-secret (they are).
      Files: `jarvis-backend/src/jarvis_backend/config.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_config_and_crypto.py -q` — passes.

- [ ] 7. Add MFA/step-up request & response schemas to `schemas_auth.py`.
      `MfaEnrollOut` (`secret: str`, `otpauth_uri: str`), `MfaVerifyIn` (`code: str = Field(min_length=6, max_length=10)` — allows recovery codes too), `MfaEnableOut` (`recovery_codes: List[str]` shown once), `MfaDisableIn` (`current_password`, `code`), `MfaLoginIn` (`mfa_token: str`, `code: str`), `MfaStatusOut` (`enabled: bool`). Extend `TokenOut` is unnecessary; add a distinct `MfaRequiredOut` (`mfa_required: bool = True`, `mfa_token: str`, `expires_in: int`) returned by login when MFA is enabled. Also add `step_up_token`/`code` field to the approval decision path via a NEW `schemas.py` change (item 9), not here. Cap all code fields; use `pattern=r"^[0-9A-Za-z\-]{6,14}$"` for codes.
      Files: `jarvis-backend/src/jarvis_backend/schemas_auth.py`
      Verify: import-only (`.\.venv\Scripts\python.exe -c "import jarvis_backend.schemas_auth"`); full coverage in item 14.

- [ ] 8. Implement MFA logic in `AuthService` (`auth/service.py`): enrollment, verify-at-login, step-up, recovery, disable.
      Add methods: `start_mfa_enrollment(principal) -> (secret, otpauth_uri)` (generate secret, `set_mfa_pending`, build URI via `totp.otpauth_uri` with `settings.mfa_issuer`); `confirm_mfa_enrollment(principal, code) -> List[str]` (verify TOTP against pending secret with no-replay via `mfa_last_counter`; on success generate N recovery codes, store argon2 hashes via `hash_password`, `enable_mfa`, set counter, return plaintext codes ONCE); `disable_mfa(principal, current_password, code)` (require BOTH a correct password AND a valid TOTP-or-recovery code, else 401 — never password alone); modify `login()` so that when `user.mfa_enabled` it does NOT call `_open_session` but returns a signal object carrying a freshly minted `mfa_pending` token (`codec.issue_special` typ=`mfa_pending`, ttl ~5 min, sub=user id, sid=a random nonce) — rate limits already applied before this branch; add `complete_mfa_login(mfa_token, code, ip, user_agent) -> IssuedTokens` that re-applies the SAME three rate-limit keys, verifies the mfa_pending token via `verify_special`, loads the user, checks TOTP (no-replay) OR consumes a single-use recovery hash (`hmac.compare_digest` against argon2 verify), then calls `_open_session`; add `verify_step_up(principal, code) -> None` (verify TOTP/recovery, `sessions.mark_step_up`) and `require_step_up(principal, risk) -> None` used by approvals (see item 10) that FAILS CLOSED. Return a new `MfaRequired` dataclass (mirroring `IssuedTokens`) so the route can branch. Audit every outcome (`mfa_enabled`, `mfa_login_succeeded`, `mfa_failed`, `recovery_code_used`, `step_up_verified`, `mfa_disabled`) with NO code/secret in the payload. Non-MFA users keep the current single-step path unchanged.
      Files: `jarvis-backend/src/jarvis_backend/auth/service.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_auth.py -q` — existing tests still pass (non-MFA login unchanged); new MFA tests added in item 14.

- [ ] 9. Add step-up fields to the approval decision schema.
      In `schemas.py` extend `ApprovalDecision` with `code: Optional[str] = Field(default=None, max_length=14, pattern=r"^[0-9A-Za-z\-]{6,14}$")` (a TOTP/recovery code supplied inline with the grant). Keep `granted: bool`.
      Files: `jarvis-backend/src/jarvis_backend/schemas.py`
      Verify: `.\.venv\Scripts\python.exe -c "import jarvis_backend.schemas"`; covered by item 14.

- [ ] 10. Enforce step-up on high-risk grants in `routes_approvals.py` `decide()`.
      After resolving role=admin and BEFORE `c.approvals.resolve(...)` for a GRANT (`body.granted is True`): load the pending approval (`c.approvals.get`) to read `.risk`; if the risk meets the configured threshold (`DANGEROUS`, or also `RESTRICTED` when `settings.approval_step_up_risk == "RESTRICTED"`) then: if the admin has MFA enabled, require a valid recent step-up — either an inline `body.code` verified now (`c.auth.verify_step_up`) OR a `session.step_up_at` within `settings.step_up_window_seconds`; on failure raise `401`/`403` with a clear reason and audit `approval_step_up_denied`. If the admin does NOT have MFA: if `settings.approval_require_mfa` is True → deny with a message to enroll MFA; else allow but audit `approval_high_risk_no_mfa` (warning surfaced). Fail closed on any ambiguity. Denials/claims by agent role stay 403 (unchanged — `admin_only` already enforces). Do not change the non-grant (deny) path. Add a dedicated step-up endpoint in routes_auth (item 11) that sets `step_up_at` so the UI can pre-authorize.
      Files: `jarvis-backend/src/jarvis_backend/api/routes_approvals.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_auth.py tests/integration/test_workflows.py -q` — existing pass; new step-up tests in item 14.

- [ ] 11. Add MFA + step-up routes to `routes_auth.py`.
      New routes under `/api/auth` (all `admin_only` except `mfa/login`): `POST /mfa/enroll` → `MfaEnrollOut` (start enrollment); `POST /mfa/verify` (confirm enrollment with a code) → `MfaEnableOut` (recovery codes once); `POST /mfa/disable` (`MfaDisableIn`) → 204; `GET /mfa/status` → `MfaStatusOut`; `POST /mfa/step-up` (`MfaVerifyIn`) → 204 (marks `step_up_at`); and modify `POST /login` so the handler branches: if `c.auth.login` returns a `MfaRequired`, return `MfaRequiredOut` (HTTP 200, no refresh cookie set) instead of `TokenOut`; add `POST /mfa/login` (`MfaLoginIn`, no auth) → `TokenOut` + `_set_refresh_cookie`, calling `c.auth.complete_mfa_login`. Rate-limit mutating MFA endpoints with `c.limiter.enforce` like device routes. Keep `response_model` typing honest (login returns a union; use `response_model=None` + explicit return typing or a shared superset model — prefer returning distinct Pydantic models with `response_model` on the dedicated routes and `response_model=None` on `/login`).
      Files: `jarvis-backend/src/jarvis_backend/api/routes_auth.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_auth.py -q` — existing + new pass.

- [ ] 12. Surface MFA posture in `check-config` / system status and add the `enroll-mfa` CLI command.
      In `__main__.py` add `cmd_enroll_mfa` (`create-admin`-style: look up the user, start enrollment, print the otpauth URI + secret for manual entry, prompt for a code to confirm, print the recovery codes ONCE) wired as subcommand `enroll-mfa --username`. In the system status path (`routes_system`) and/or `check-config`, add a value-free warning line when `env=prod` and no admin user has `mfa_enabled` (query `UserStore`); never block startup. Do NOT print secrets to logs.
      Files: `jarvis-backend/src/jarvis_backend/__main__.py`, `jarvis-backend/src/jarvis_backend/api/routes_system.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_cli.py tests/integration/test_end_to_end.py -q` — existing pass; add a CLI MFA test in item 14.

- [ ] 13. Dashboard: login code entry, enrollment/disable UI, and high-risk step-up prompt (CSP-compliant, no innerHTML/inline-script/CDN).
      In `api.js`: make `login()` detect `mfa_required` in the JSON and return it (don't set token); add `mfaLogin(mfaToken, code)` posting to `/api/auth/mfa/login`. In `app.js` login form: when login returns `mfa_required`, swap the form to a 6-digit code field (`inputmode="numeric"`, `maxLength:10`, allow recovery codes) that calls `mfaLogin`. In `views/system.js` account-security card: add an "MFA" section — status, an "Enable MFA" button that calls `/api/auth/mfa/enroll`, renders the secret as selectable text + the otpauth URI (and, if item 2 produced an SVG `data:` QR, an `<img src="data:image/svg+xml,...">` built via `el('img',{src})` — allowed by `img-src data:`), a code field to confirm (`/mfa/verify`) that then shows recovery codes once in a `.secret-box` (reuse device-secret pattern), and a "Disable MFA" button prompting for password + code. In `views/approvals.js` `decide()`: when granting and the response is a step-up `401`/`403`, prompt for a code (window.prompt or an inline field) and retry the decision with `body.code`, or first call `/api/auth/mfa/step-up`. All elements built with `el()`/`textContent` only; styles via `applyStyle`.
      Files: `jarvis-backend/src/jarvis_backend/web/js/api.js`, `.../web/js/app.js`, `.../web/js/views/system.js`, `.../web/js/views/approvals.js` (and `.../web/css/app.css` if a style class is needed)
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_auth.py::test_security_headers_and_no_store -q` — CSP header unchanged; manually confirm no new file contains `innerHTML`, `<script>` inline, or a CDN URL (grep as a sanity check, not as the test).

- [ ] 14. Add the full MFA/step-up test matrix (new file `tests/integration/test_mfa.py` + unit tests in `tests/unit/`).
      Mirror `test_auth.py` using `client`/`admin`/`login`/`bearer`. Unit (`tests/unit/test_totp.py`): TOTP generate/verify round-trip, window tolerance, wrong code rejected, replay (same counter) rejected via `last_counter`, otpauth URI shape; token unit tests: `codec.verify()` REJECTS an `mfa_pending` token (no bypass), `verify_special` accepts only its own typ. Integration matrix:
        (a) enroll → verify → `mfa_status.enabled` true; secret never returned after confirm;
        (b) login with MFA returns `mfa_required` + `mfa_token`, NOT access/refresh; `mfa_token` is rejected by `/api/auth/me` (no bypass);
        (c) `/mfa/login` with correct TOTP returns real tokens + refresh cookie; wrong code 401; responses non-enumerating;
        (d) the three rate limits apply to the MFA step (loop 6 → 429, correct code still 429 while throttled);
        (e) recovery code logs in once, second use fails (single-use);
        (f) replayed TOTP within the same step is rejected;
        (g) disable requires password AND code (password-only 401; wrong code 401; both correct 204; after disable, login is single-step again);
        (h) step-up: admin WITH MFA granting a `DANGEROUS` approval without step-up → 401/403; with inline `code` or after `/mfa/step-up` within window → 200; a stale/expired step-up → denied; a `SAFE` grant needs no step-up;
        (i) `approval_require_mfa=True` + admin without MFA → high-risk grant denied; default (False) → allowed with audit;
        (j) separation of duties: agent/device role still 403 on `/decision` (reuse existing assertion);
        (k) nothing secret appears in `/api/audit` output.
      Files: `tests/integration/test_mfa.py`, `tests/unit/test_totp.py` (and additions to `tests/unit/test_config_and_crypto.py` for the new settings if useful)
      Verify: `$env:JARVIS_ENV="dev"; .\.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q` — all new tests pass AND the baseline 267 still pass (expect 267 + new passed, 5 skipped).

- [ ] 15. Documentation and env surface.
      Update `docs/SECURITY.md` (flip "No MFA/passkeys" gap → TOTP MFA implemented; "Approvals are not step-up authenticated" → step-up added, note the fail-closed/no-replay/recovery-code model). `docs/PRODUCTION_PLAN.md` (mark Phase 4 done + checklist). `docs/OPERATIONS.md` (MFA enrollment via dashboard and `enroll-mfa` CLI, recovery-code storage/one-time display, lost-authenticator runbook using recovery codes or admin `disable-mfa`, and the manual `ALTER TABLE` note for existing prod DBs since there is no Alembic yet — Phase 5). `SKILL.md` and `.env.example` (new `JARVIS_MFA_ISSUER`, `JARVIS_MFA_RECOVERY_CODES`, `JARVIS_STEP_UP_WINDOW_SECONDS`, `JARVIS_APPROVAL_REQUIRE_MFA`, `JARVIS_APPROVAL_STEP_UP_RISK`). No secrets in examples.
      Files: `docs/SECURITY.md`, `docs/PRODUCTION_PLAN.md`, `docs/OPERATIONS.md`, `SKILL.md`, `.env.example`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_deployment_files.py -q` — passes (this test checks `.env.example`/docs consistency; update its expected keys if it enumerates env vars).

- [ ] 16. Final full-suite gate.
      Run the whole suite once more to confirm no regression and clean up any temp files.
      Files: (none)
      Verify: `$env:JARVIS_ENV="dev"; .\.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q` — GREEN, baseline 267 still passing, 5 skipped unchanged, all new MFA/step-up tests passing.

---

## Notes / assumptions
- The 5 skipped postgres tests require `pgserver` (absent) and are expected to stay skipped — do not treat them as failures.
- `response_model` for `/login` must become a union-friendly form (`response_model=None` with explicit return) because the body is now either `TokenOut` or `MfaRequiredOut`.
- `otpauth` URI uses the default SHA1/6-digit/30s params so any standard app (Google Authenticator / Authy / Microsoft Authenticator) works.
- Keep every edit ruff-`S`-clean: use `secrets`/`hmac.compare_digest`, never `random`; no plaintext secret/code logging; `SecretStr` for any new secret-bearing setting (the new settings here are non-secret, so plain `str`/`int`/`bool` is correct and matches the issuer/window style).

## Verification note (first iteration — implemented and tested)

TOTP path taken: **stdlib RFC 6238** (`jarvis-backend/src/jarvis_backend/auth/totp.py`). Confirmed
`python -c "import pyotp"` fails in the repo-root `.venv` (no pip, no pyotp, no qrcode, no ruff), so
the runtime never imports pyotp; `pyotp==2.9.0` is still declared in `requirements.txt`,
`constraints.txt`, and `jarvis-backend/pyproject.toml` as the documented dependency only.

QR path: no QR/image lib available → shipped path is the `otpauth://totp/JARVIS:<user>?...` URI +
base32 secret for manual entry (dashboard renders both as selectable text; CSP unchanged).

What was built (per plan items 1–15):
- Stdlib TOTP (generate/verify with window + no-replay via `last_counter`, otpauth URI).
- `TokenCodec.issue_special`/`verify_special` + `MFA_PENDING` typ; `verify()` still rejects any
  non-access typ (proven by unit test — mfa_pending can never authenticate).
- `UserRow.mfa_secret` (EncryptedText), `mfa_enabled`, `mfa_last_counter`, `mfa_recovery_hashes`
  (EncryptedJSON of argon2 hashes); `SessionRow.step_up_at`. No Alembic → documented manual ALTER.
- `AuthService`: enrollment start/confirm, two-step MFA login (password → mfa_pending → code),
  recovery codes (single-use argon2), disable (password AND code), step-up verify + `ensure_step_up`.
- Routes: `/api/auth/mfa/{status,enroll,verify,disable,step-up,login}`; `/login` returns
  `MfaRequiredOut` for MFA accounts (no cookie). Approvals `/decision` enforces step-up on high-risk.
- Config: `JARVIS_MFA_ISSUER`, `JARVIS_MFA_RECOVERY_CODES`, `JARVIS_STEP_UP_WINDOW_SECONDS`,
  `JARVIS_APPROVAL_REQUIRE_MFA`, `JARVIS_APPROVAL_STEP_UP_RISK`. Prod `status` warns (never blocks)
  when no admin has MFA.
- CLI `enroll-mfa --username`. Dashboard: login code entry, enroll/disable screens, high-risk
  step-up prompt — CSP-compliant (no innerHTML/inline script/CDN; verified by grep).
- Docs: SECURITY.md, PRODUCTION_PLAN.md (Phase 4 done), OPERATIONS.md (enrollment + recovery +
  lost-authenticator runbook + manual ALTER), SKILL.md, .env.example.

Commands run (env `JARVIS_ENV=dev`, `.\.venv\Scripts\python.exe`):
- `pytest tests/unit tests/integration -q` → **289 passed, 5 skipped** (5 = expected postgres skips;
  baseline 267 + 22 new MFA/step-up tests). 1 pre-existing deprecation warning, unrelated.
- TOTP smoke: generate→verify round-trip ok, same-step replay rejected.
- App factory builds with no MFA configured; `check-config`/Settings load the new vars with the
  documented defaults; CLI help lists `enroll-mfa`.

New tests: `tests/unit/test_totp.py` (TOTP round-trip, window, wrong code, replay, URI shape,
mfa_pending rejected by `verify`, `verify_special` typ enforcement); `tests/integration/test_mfa.py`
(enroll requires correct code; login needs second factor; mfa_pending rejected on protected routes;
recovery code once; TOTP replay; rate-limited MFA step; disable needs password+code; high-risk grant
needs step-up [inline or endpoint], SAFE needs none; non-MFA admin allowed; require-mfa blocks;
separation of duties agent 403; no secret/recovery code in audit).

## Verification note (second iteration — review findings fixed)

All six findings from `.agents/tasks/mfa-review.json` / `mfa-review.md` are addressed:

1. **MFA re-enrollment bypass** — `AuthService.start_mfa_enrollment` now raises 409 while
   `mfa_enabled` is True, so a live/stolen session cannot clear an active factor and fall
   back to the non-MFA high-risk path. Rotation requires `disable_mfa` first (password + a
   current code), which keeps the old factor live. Test: `test_cannot_re_enroll_while_mfa_enabled`.
2. **Non-atomic one-time state** — `UserStore.advance_mfa_counter` is a compare-and-set
   (`WHERE mfa_last_counter == previous`, new counter stored only if the UPDATE hits one
   row); `consume_recovery_hash` keys on the matched HASH under a `SELECT … FOR UPDATE` row
   lock and removes by value; `enable_mfa` is conditional on the still-pending state and
   returns False to the loser so concurrent confirms cannot each reveal a distinct recovery
   set. Tests: `test_recovery_code_consumed_exactly_once`, `test_totp_counter_advance_rejects_stale_previous`.
3. **Unknown approval risk fails open** — `_enforce_high_risk_step_up` validates `pending.risk`
   against the complete `RiskLevel` enum FIRST and denies+audits (`reason=unknown_risk`) any
   value outside it. Test: `test_unknown_risk_cannot_be_granted`.
4. **Future step-up timestamp** — `ensure_step_up` now requires `0 <= age <= window`, so a
   future-dated `step_up_at` fails closed. Tests: `test_future_dated_step_up_is_refused`,
   `test_stale_step_up_is_refused_but_fresh_within_window_succeeds`.
5. **Verification matrix / API alignment** — `MfaDisableIn.code` is Optional so a
   password-only disable is DENIED with 401 by the service (not a 422 bounce);
   `complete_mfa_login` returns ONE non-enumerating 401 body for an expired/invalid
   challenge, a bad account, and a wrong code. Tests: `test_disable_with_password_only_is_refused_401`,
   `test_mfa_login_failures_are_non_enumerating`, plus the stale/expired step-up cases above.
6. **Unmasked MFA-disable password** — `web/js/views/system.js` disable flow replaced the
   `window.prompt()` with a CSP-compliant inline form using `<input type="password"
   autocomplete="current-password">` and a separate bounded code input (built via `el()`,
   no innerHTML / inline script / CDN).

Commands run (env `JARVIS_ENV=dev`, `.\.venv\Scripts\python.exe`):
- `pytest tests/integration/test_mfa.py -q` → 23 passed.
- `pytest tests/unit tests/integration -q` → **297 passed, 5 skipped** (5 = expected
  postgres skips; prior 289 + 8 new/retargeted finding tests). 1 pre-existing unrelated
  deprecation warning.
- App factory builds with no MFA configured (`step_up_window=300`, `require_mfa=False`).

## Reviewer gate contract (unchanged by this plan)
The implement-and-review loop's reviewer writes `c:\Users\Lenovo\Desktop\JARVIS\abduls_jarvis\.agents\tasks\mfa-review.json` with `verdict` = `APPROVED` as the loop's last child when all items above are satisfied and the full suite is green.
