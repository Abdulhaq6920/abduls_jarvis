# TOTP MFA and step-up approval gating

Phase 4 adds opt-in TOTP enrollment, two-stage login, argon2-backed single-use recovery codes, and recent-factor gating for dangerous approval grants. The implementation isolates `mfa_pending` JWTs from access authentication, encrypts TOTP secrets at rest, and uses backend-independent compare-and-set transitions for enrollment, recovery redemption, and disablement. The latest pass fixes the previously reported stale-disable race and adds the missing CLI enrollment verification. One **confirmed** blocking race remains: a TOTP verified from a stale user snapshot is advanced without checking `mfa_state_version`, allowing an old factor generation to authenticate after rotation when the old and replacement counters align.

Watch for: **confirmed** — `advance_mfa_counter()` binds acceptance to the previous counter but not to the secret generation that produced the code.

**Verdict**: NEEDS_CHANGES

## High-level view

Password authentication for an MFA-enabled account stops before session creation and returns only a five-minute `mfa_pending` challenge. Normal authentication accepts only `typ=access`, and codec-level plus protected-route tests prove the pending token receives 401.

Enrollment, recovery redemption, and disablement use `mfa_state_version` compare-and-set transitions on SQLite and PostgreSQL. The TOTP counter transition is the exception: it compares only `mfa_last_counter`, creating the **confirmed** cross-generation acceptance race when stale verification lands after factor rotation.

Dangerous grants validate risk before mutation and require an inline factor or a session-bound step-up within 300 seconds for MFA admins. Unknown risks and invalid timestamps deny, mandatory MFA remains opt-in, agent/device callers remain 403, and local hard blocks remain independent of approval.

The TOTP secret uses `EncryptedText`; recovery material is returned once and persisted only as argon2 hashes inside `EncryptedJSON`. Audit events omit secrets and codes, while the dashboard uses the existing `el()`/`textContent`/CSSOM path without `innerHTML`, inline script, or third-party assets.

The coder records 312 passing tests and 9 expected PostgreSQL skips. The required token isolation, password-only denial, throttling, replay, recovery single-use, step-up, role separation, hard-block, CLI, and audit-redaction evidence is present; the **confirmed** residual gap is an interleaved rotation test proving stale TOTP verification cannot cross factor generations.

<details>
<summary>Issues (1)</summary>

1. **TOTP verification crosses factor generations** (**confirmed**) — the counter update does not check the `mfa_state_version` associated with the secret that verified the code, so an old factor can authenticate after rotation when counters align. Add the expected generation to the conditional update and test that stale-factor MFA login and step-up both deny after replacement enrollment.

</details>

<details>
<summary>Details</summary>

## Pending JWTs cannot enter normal authentication

The MFA password branch returns `MfaRequired` before `_open_session()`, emitting neither an access token nor a refresh cookie. `TokenCodec.verify()` requires `typ=access`, while challenge redemption requires `typ=mfa_pending`; `test_mfa_pending_token_is_rejected_on_protected_routes` presents the pending token to `/api/auth/me` and `/api/agents` and receives 401 through the real authentication stack.

## Encrypted enrollment and one-time recovery state

`UserRow.mfa_secret` is an `EncryptedText` column, and confirmation never includes the secret in its response. Recovery codes are shown once, hashed individually with argon2, and stored only as an encrypted list of hashes; redemption removes the matched hash through a checked state-version update. Enrollment confirmation binds enablement to the exact pending secret and version, while disablement now clears only the factor generation authenticated by the request.

## Stale TOTP verification can authenticate a replacement generation

`AuthService._check_second_factor()` verifies against the detached `user.mfa_secret` snapshot, then calls `advance_mfa_counter(user.id, counter, user.mfa_last_counter)`. `UserStore.advance_mfa_counter()` checks the user id, enabled state, prior counter, and strictly increasing counter, but not the snapshot's `mfa_state_version`. This is a **confirmed** fail-closed violation because rotation can replace the secret while leaving the globally time-derived confirmation counter equal to the stale snapshot's previous counter.

```text
request A: load generation G at version v, last_counter C
           verify G's code for C+1, pause before counter UPDATE
request B: disable G, enroll generation H, confirm H at counter C
           row is now generation H at version v+3, last_counter C
request A: UPDATE WHERE enabled AND last_counter=C -> succeeds
           old generation G is accepted against generation H's row
```

The alignment is feasible when rotation disables the old factor with a recovery code, which does not advance the TOTP counter, and both generations use the same wall-clock counter. `complete_mfa_login()` and `verify_step_up()` trust the successful update directly, so the old factor can open a session or stamp a fresh dangerous-action step-up. Disablement performs a later generation check, but login and step-up do not.

Pass the snapshot's `mfa_state_version` into `advance_mfa_counter()` and include it in the conditional update; ordinary TOTP use need not increment that version. Add an orchestrated test that loads generation G, rotates to H with the same stored counter, resumes G's counter advance, and proves both MFA login and step-up deny while H remains intact.

## Verification evidence leaves one rotation case uncovered

The recorded full run is `312 passed, 9 skipped`, with the skips identified as environment-dependent PostgreSQL cases. The matrix covers the pending-token 401, no tokens or cookie after password-only MFA login, TOTP throttling and same-step replay, recovery-code single use, password-plus-factor disablement, dangerous and safe approval paths, stale/future step-up, mandatory-MFA configuration, agent 403, local hard blocks, audit redaction, and CLI enrollment nondisclosure and hashed persistence.

Not tested: the **confirmed** stale-generation TOTP interleaving above. Existing counter tests use one generation, proving duplicate-counter exclusion but not that the accepted counter belongs to the current encrypted secret.

</details>

<details>
<summary>File map</summary>

- `.agents/tasks/mfa-plan.md` — Phase 4 contract, prior findings, and recorded verification passes.
- `.env.example` — non-secret MFA and step-up settings.
- `SKILL.md` — operational MFA capability guidance.
- `constraints.txt` — pinned PyOTP declaration.
- `requirements.txt` — root PyOTP declaration.
- `docs/OPERATIONS.md` — enrollment, recovery, lost-device, and schema-migration runbooks.
- `docs/PRODUCTION_PLAN.md` — Phase 4 completion status.
- `docs/SECURITY.md` — MFA and approval step-up posture.
- `jarvis-backend/pyproject.toml` — backend PyOTP declaration and stdlib-runtime note.
- `jarvis-backend/src/jarvis_backend/__main__.py` — password-gated interactive `enroll-mfa` command.
- `jarvis-backend/src/jarvis_backend/api/routes_approvals.py` — risk-aware second-factor gate before grants.
- `jarvis-backend/src/jarvis_backend/api/routes_auth.py` — MFA enrollment, challenge redemption, disablement, status, and step-up routes.
- `jarvis-backend/src/jarvis_backend/api/routes_system.py` — production no-admin-MFA warning.
- `jarvis-backend/src/jarvis_backend/auth/passwords.py` — argon2 recovery-code verification helper.
- `jarvis-backend/src/jarvis_backend/auth/service.py` — MFA lifecycle, login branching, replay defense, and step-up behavior.
- `jarvis-backend/src/jarvis_backend/auth/tokens.py` — distinct pending-token issuance and verification.
- `jarvis-backend/src/jarvis_backend/auth/totp.py` — stdlib RFC 6238 generation and constant-time verification.
- `jarvis-backend/src/jarvis_backend/config.py` — opt-in MFA and step-up defaults.
- `jarvis-backend/src/jarvis_backend/schemas.py` — inline approval-code contract.
- `jarvis-backend/src/jarvis_backend/schemas_auth.py` — MFA request and response contracts.
- `jarvis-backend/src/jarvis_backend/storage/identity.py` — MFA state transitions and session step-up persistence.
- `jarvis-backend/src/jarvis_backend/storage/models.py` — encrypted factor state, version, and step-up timestamp.
- `jarvis-backend/src/jarvis_backend/web/js/api.js` — browser MFA challenge redemption.
- `jarvis-backend/src/jarvis_backend/web/js/app.js` — second-factor login form.
- `jarvis-backend/src/jarvis_backend/web/js/views/approvals.js` — high-risk approval-code prompt.
- `jarvis-backend/src/jarvis_backend/web/js/views/system.js` — enrollment, recovery, and disablement UI.
- `tests/integration/test_cli.py` — CLI enrollment disclosure and hashed-persistence coverage.
- `tests/integration/test_mfa.py` — HTTP MFA, concurrency, and step-up matrix.
- `tests/integration/test_postgres.py` — PostgreSQL MFA transition race checks.
- `tests/unit/test_storage.py` — SQLite state-version compare-and-set checks.
- `tests/unit/test_totp.py` — RFC 6238 behavior and pending-token type isolation.

Full diff: `git diff 661a55f..HEAD`

</details>
