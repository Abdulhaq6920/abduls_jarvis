"""Login, sessions, and device credentials.

Threat-driven design notes:

* **Brute force / credential stuffing**: three rate limits on login (per IP+user, per
  user, per IP). A hard account lock is deliberately NOT used -- it would let an attacker
  lock the real owner out. A successful login clears the IP+user counter.
* **User enumeration**: unknown user, wrong password, and disabled account all return the
  same 401 and cost the same time (dummy-hash verification).
* **Refresh-token theft**: refresh tokens rotate on every use and keep their ORIGINAL
  expiry (no endless sliding session). Presenting an already-rotated token after the grace
  window means two parties hold the same token, so the whole token family is revoked.
* **Immediate revocation**: access tokens are checked against live session state on every
  request (Redis-cached, database as the source of truth).
* **Password change** revokes every session of that user.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException, status
from jarvis_shared.log import get_logger

from jarvis_backend.auth import totp
from jarvis_backend.auth.passwords import (
    WeakPassword,
    check_policy,
    hash_password,
    needs_rehash,
    verify_hash,
    verify_password,
)
from jarvis_backend.auth.tokens import MFA_PENDING, Claims, TokenCodec, TokenError
from jarvis_backend.cache import Cache
from jarvis_backend.config import Settings
from jarvis_backend.ratelimit import RateLimiter
from jarvis_backend.storage import AuditLog, DeviceStore, SessionStore, UserStore, utcnow

log = get_logger(__name__)

ADMIN, AGENT = "admin", "agent"
REFRESH_REUSE_GRACE = timedelta(seconds=10)
_ACTIVE_TTL, _REVOKED_TTL = 30, 900  # cache seconds; revoked outlives any access token


def _unauthorized(detail: str = "Invalid credentials.") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    sid: str
    kind: str  # "user" | "device"


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_secret: Optional[str]
    username: str
    role: str


@dataclass(frozen=True)
class MfaRequired:
    """Login's answer when the account has MFA: a short-lived single-purpose token the
    caller must present (with a TOTP/recovery code) to a second endpoint. It is NOT an
    access token and cannot authenticate any protected route."""

    mfa_token: str
    expires_in: int


MFA_PENDING_TTL = 300  # seconds the mfa_pending token is valid (one login attempt)
# One non-enumerating message for every MFA-login failure (expired challenge, bad account,
# wrong code): the client must not be able to distinguish the cause.
_MFA_LOGIN_FAIL = "Your sign-in could not be completed. Start again."


class AuthService:
    def __init__(self, *, settings: Settings, users: UserStore, sessions: SessionStore,
                 devices: DeviceStore, codec: TokenCodec, cache: Cache, limiter: RateLimiter,
                 audit: AuditLog) -> None:
        self._s, self.users, self.sessions, self.devices = settings, users, sessions, devices
        self._codec, self._cache, self._limiter, self._audit = codec, cache, limiter, audit

    async def _enforce_login_limits(self, name: str, ip: str) -> str:
        """The three login rate limits, applied to both password and the MFA step. Returns
        the per-pair key so a success can clear it."""
        limit, window = self._s.login_attempts_per_window, self._s.login_window_seconds
        pair_key = f"login:pair:{ip}:{name}"
        await self._limiter.enforce(pair_key, limit=limit, window=window)
        await self._limiter.enforce(f"login:user:{name}", limit=limit * 4, window=window)
        await self._limiter.enforce(f"login:ip:{ip}", limit=limit * 6, window=window)
        return pair_key

    # ----------------------------------------------------------------------- login
    async def login(self, username: str, password: str, ip: str,
                    user_agent: str) -> "IssuedTokens | MfaRequired":
        name = username.strip().lower()[:64]
        pair_key = await self._enforce_login_limits(name, ip)

        user = await self.users.by_username(name)
        valid = await verify_password(user.password_hash if user else None, password)
        if not (valid and user and not user.disabled):
            await self._audit.record("login_failed", ip=ip, username_hash=_short_hash(name),
                                     reason="unknown_user" if user is None else "bad_credentials")
            raise _unauthorized("Invalid username or password.")

        if needs_rehash(user.password_hash):
            await self.users.touch_login(user.id, await hash_password(password))

        if user.mfa_enabled:
            # Password alone must NOT yield access/refresh tokens: hand back a single-purpose
            # token the caller redeems with a second factor. The pair-limit stays armed so
            # the MFA step is throttled too; it is cleared only on full success.
            token = self._codec.issue_special(sub=user.id, sid=secrets.token_urlsafe(16),
                                               typ=MFA_PENDING, ttl_seconds=MFA_PENDING_TTL)
            await self._audit.record("mfa_challenge_issued", ip=ip, user=user.username)
            return MfaRequired(mfa_token=token, expires_in=MFA_PENDING_TTL)

        await self._cache.delete("rl:" + pair_key)
        await self.users.touch_login(user.id)
        issued = await self._open_session(user.id, user.username, user.role, None,
                                          timedelta(days=self._s.refresh_token_days), ip, user_agent)
        await self._audit.record("login_succeeded", ip=ip, user=user.username, sid=issued[1])
        return issued[0]

    # ------------------------------------------------------------------------- mfa
    async def _check_second_factor(self, user, code: Optional[str]) -> bool:
        """True if ``code`` is a valid TOTP (no replay) or an unused recovery code.

        On a TOTP match the last-used counter is advanced; on a recovery match that code is
        consumed. Fails CLOSED on a missing secret, a missing/empty code, or any ambiguity."""
        if not user.mfa_secret or not code:
            return False
        counter = totp.verify(user.mfa_secret, code, last_counter=user.mfa_last_counter)
        if counter is not None:
            # Compare-and-set: only the request that actually advances the stored counter
            # from the value it read wins. A racing duplicate with the same step loses the
            # UPDATE and is rejected -- no replay, even on a concurrent backend. The counter
            # is also pinned to the generation (mfa_state_version) whose secret verified this
            # code, so a code checked against a stale snapshot cannot authenticate a factor
            # that was rotated (disabled + re-enrolled) in the meantime -- it fails closed.
            return await self.users.advance_mfa_counter(
                user.id, counter, user.mfa_last_counter,
                expected_version=user.mfa_state_version)
        for stored in list(user.mfa_recovery_hashes or []):
            if await verify_hash(stored, code):
                # Consume by the matched hash so two parallel logins cannot both spend one
                # code: whichever transaction removes it first wins; the other finds it gone.
                return await self.users.consume_recovery_hash(user.id, stored)
        return False

    async def start_mfa_enrollment(self, principal: Principal,
                                   current_password: str) -> tuple[str, str]:
        """Generate a fresh (unconfirmed) secret and the otpauth URI for an admin.

        Requires the admin's CURRENT PASSWORD before any secret is disclosed: a stolen
        bearer token alone must not be able to provision an attacker-controlled factor
        and then satisfy a mandatory high-risk step-up gate (independent re-auth proof).

        Enrollment is also REFUSED while MFA is already enabled: a live session (possibly
        stolen) must not be able to clear an active factor and fall back to the non-MFA
        approval path. Rotating a factor requires disabling it first (password + a valid
        current code), which keeps the old factor live until the holder proves it."""
        user = await self.users.by_id(principal.subject)
        if user is None:
            raise _unauthorized()
        if not await verify_password(user.password_hash, current_password):
            await self._audit.record("mfa_enrollment_denied", user=user.username,
                                     reason="password")
            raise _unauthorized("Current password is incorrect.")
        if user.mfa_enabled:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "MFA is already enabled. Disable it first (requires your password "
                                "and a current code) to enroll a new authenticator.")
        secret = totp.generate_secret()
        # Conditional (fail-closed) write: refused if MFA was enabled between the check
        # above and here (a concurrent confirmation). This closes the stale-enrollment
        # race where a request that began while the account was disabled would otherwise
        # commit mfa_enabled=False and clear a factor another transaction just activated.
        if not await self.users.set_mfa_pending(user.id, secret):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "MFA is already enabled. Disable it first (requires your password "
                                "and a current code) to enroll a new authenticator.")
        uri = totp.otpauth_uri(secret, account=user.username, issuer=self._s.mfa_issuer)
        await self._audit.record("mfa_enrollment_started", user=user.username)
        return secret, uri

    async def confirm_mfa_enrollment(self, principal: Principal, code: str) -> list[str]:
        """Confirm a 6-digit code against the pending secret, then enable MFA and return
        one-time recovery codes (shown once; only hashes are stored)."""
        user = await self.users.by_id(principal.subject)
        if user is None or not user.mfa_secret or user.mfa_enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "No pending MFA enrollment to confirm.")
        matched = totp.verify(user.mfa_secret, code)
        if matched is None:
            await self._audit.record("mfa_enroll_failed", user=user.username)
            raise _unauthorized("That code is not valid. Check the time on your device and retry.")
        codes = [secrets.token_hex(5) for _ in range(self._s.mfa_recovery_codes)]
        hashes = [await hash_password(c) for c in codes]
        # Persist the matched confirmation counter as last_counter: the SAME code must not
        # be replayable at the first login or a step-up within its 30s step. Replay
        # protection therefore begins at enablement, not at the first authentication.
        # enable_mfa is a version compare-and-set bound to the still-pending state AND the
        # exact secret just verified, so a concurrent re-enrollment that swapped the secret
        # cannot be enabled on this proof, and two concurrent confirmations cannot each
        # reveal a distinct recovery set: the loser's stale-version UPDATE touches no row,
        # returns False, and fails closed. Portable to SQLite (no SELECT ... FOR UPDATE).
        if not await self.users.enable_mfa(user.id, hashes, expected_secret=user.mfa_secret,
                                           last_counter=matched):
            raise HTTPException(status.HTTP_409_CONFLICT, "No pending MFA enrollment to confirm.")
        await self._audit.record("mfa_enabled", user=user.username)
        return codes

    async def disable_mfa(self, principal: Principal, current_password: str,
                          code: Optional[str]) -> None:
        """Require BOTH a correct password AND a valid second factor (never password alone)."""
        user = await self.users.by_id(principal.subject)
        if user is None or not user.mfa_enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "MFA is not enabled.")
        if not await verify_password(user.password_hash, current_password):
            await self._audit.record("mfa_disable_failed", user=user.username, reason="password")
            raise _unauthorized("Current password is incorrect.")
        # The factor GENERATION this request authenticates against, pinned BEFORE the second
        # factor is checked. The clear below is bound to it: if a concurrent disable+
        # re-enroll rotated the factor (each step bumps mfa_state_version), the version has
        # moved on and the conditional clear touches 0 rows -- we fail closed instead of
        # erasing the replacement (stale-disable race). Binding on the encrypted secret is
        # impossible (non-deterministic Fernet ciphertext never matches in SQL), so the
        # plain-integer state version is the portable CAS key.
        authenticated_version = user.mfa_state_version
        if not await self._check_second_factor(user, code):
            await self._audit.record("mfa_disable_failed", user=user.username, reason="second_factor")
            raise _unauthorized("That code is not valid.")
        # Re-read to find the generation as it stands now. The ONLY version change THIS
        # request can have caused is its own recovery-code consume (+1); a TOTP match leaves
        # it unchanged. So an advance of 0 or 1 from the authenticated version is this
        # request's own effect and is the generation it proved. Anything else means a
        # concurrent disable+re-enroll (three bumps) rotated the factor underneath us ->
        # fail closed rather than clear the replacement.
        current = await self.users.by_id(user.id)
        if (current is None or not current.mfa_enabled
                or current.mfa_state_version not in (authenticated_version, authenticated_version + 1)):
            await self._audit.record("mfa_disable_failed", user=user.username, reason="stale_factor")
            raise _unauthorized("That code is not valid.")
        if not await self.users.disable_mfa(user.id, expected_version=current.mfa_state_version):
            # Lost a race between the re-read and the clear: a rotation landed in that
            # window. The CAS touched no row; refuse rather than wipe the replacement.
            await self._audit.record("mfa_disable_failed", user=user.username, reason="stale_factor")
            raise _unauthorized("That code is not valid.")
        await self._audit.record("mfa_disabled", user=user.username)

    async def complete_mfa_login(self, mfa_token: str, code: str, ip: str,
                                 user_agent: str) -> IssuedTokens:
        """Second login step: redeem an mfa_pending token with a TOTP/recovery code."""
        # Non-enumerating: an expired/invalid challenge, an unusable account, and a wrong
        # code all fail with the SAME 401 message so an attacker cannot tell them apart.
        try:
            claims = self._codec.verify_special(mfa_token, expected_typ=MFA_PENDING)
        except TokenError:
            raise _unauthorized(_MFA_LOGIN_FAIL) from None
        user = await self.users.by_id(claims.sub)
        name = user.username if user else claims.sub[:64]
        # The same three limits as the password step: the second factor is throttled too.
        pair_key = await self._enforce_login_limits(name, ip)
        if user is None or user.disabled or not user.mfa_enabled:
            raise _unauthorized(_MFA_LOGIN_FAIL)
        if not await self._check_second_factor(user, code):
            await self._audit.record("mfa_login_failed", ip=ip, user=user.username)
            raise _unauthorized(_MFA_LOGIN_FAIL)
        await self._cache.delete("rl:" + pair_key)
        await self.users.touch_login(user.id)
        issued = await self._open_session(user.id, user.username, user.role, None,
                                          timedelta(days=self._s.refresh_token_days), ip, user_agent)
        await self._audit.record("mfa_login_succeeded", ip=ip, user=user.username, sid=issued[1])
        return issued[0]

    async def verify_step_up(self, principal: Principal, code: str) -> None:
        """Prove a fresh second factor and stamp the session (fail closed)."""
        user = await self.users.by_id(principal.subject)
        if user is None or not user.mfa_enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "MFA is not enabled for this account.")
        if not await self._check_second_factor(user, code):
            await self._audit.record("step_up_failed", user=user.username)
            raise _unauthorized("That code is not valid.")
        await self.sessions.mark_step_up(principal.sid)
        await self._audit.record("step_up_verified", user=user.username, sid=principal.sid)

    async def mfa_enabled_for(self, user_id: str) -> bool:
        user = await self.users.by_id(user_id)
        return bool(user and user.mfa_enabled)

    async def ensure_step_up(self, principal: Principal, inline_code: Optional[str]) -> None:
        """Gate a high-risk grant: accept an inline code now OR a recent step-up on the
        session. Raises 401 otherwise. Caller only invokes this once MFA is confirmed on."""
        if inline_code:
            await self.verify_step_up(principal, inline_code)
            return
        row = await self.sessions.get(principal.sid)
        # Age must be within [0, window]: a future-dated step_up_at (clock rollback or
        # malformed persistence) has a NEGATIVE age and must NOT pass -- fail closed on it
        # rather than treating "not yet expired" as "fresh".
        fresh = False
        if row is not None and row.step_up_at is not None:
            age = utcnow() - row.step_up_at
            fresh = timedelta(0) <= age <= timedelta(seconds=self._s.step_up_window_seconds)
        if not fresh:
            raise _unauthorized("Re-authentication required: confirm your second factor to "
                                "approve a high-risk action.")

    async def _open_session(self, user_id: str, username: str, role: str, family_id: Optional[str],
                            lifetime: timedelta, ip: str, user_agent: str) -> tuple[IssuedTokens, str]:
        refresh_secret = secrets.token_urlsafe(48)
        session = await self.sessions.create(user_id=user_id, family_id=family_id,
                                             refresh_secret=refresh_secret, lifetime=lifetime,
                                             ip=ip, user_agent=user_agent)
        ttl = self._s.access_token_minutes * 60
        access = self._codec.issue(sub=user_id, role=role, sid=session.id, ttl_seconds=ttl)
        await self._cache.set(f"sess:{session.id}", "1", _ACTIVE_TTL)
        return IssuedTokens(access, ttl, refresh_secret, username, role), session.id

    # ---------------------------------------------------------------------- refresh
    async def refresh(self, refresh_secret: Optional[str], ip: str, user_agent: str) -> IssuedTokens:
        if not refresh_secret:
            raise _unauthorized("No session.")
        await self._limiter.enforce(f"refresh:ip:{ip}", limit=30, window=300)

        row = await self.sessions.by_refresh_secret(refresh_secret)
        if row is None:
            raise _unauthorized("Session expired.")

        if row.revoked_at is not None:
            replayed_late = (row.revoked_reason == "rotated"
                             and utcnow() - row.revoked_at > REFRESH_REUSE_GRACE)
            if replayed_late:
                await self._revoke_family(row.family_id, "reuse_detected")
                await self._audit.record("refresh_reuse_detected", ip=ip, user_id=row.user_id,
                                         family=row.family_id[:8])
                log.warning("refresh token reuse detected; token family revoked",
                            extra={"user_id": row.user_id})
            raise _unauthorized("Session expired.")

        now = utcnow()
        user = await self.users.by_id(row.user_id)
        if row.expires_at <= now or user is None or user.disabled:
            await self._revoke_family(row.family_id, "invalid")
            raise _unauthorized("Session expired.")

        if not await self.sessions.rotate(row.id):  # lost a race with another refresh
            raise _unauthorized("Session expired.")
        await self._cache.set(f"sess:{row.id}", "0", _REVOKED_TTL)
        issued, new_sid = await self._open_session(user.id, user.username, user.role, row.family_id,
                                                   row.expires_at - now, ip, user_agent)
        return issued

    # ----------------------------------------------------------------------- logout
    async def logout(self, principal: Principal) -> None:
        row = await self.sessions.get(principal.sid)
        if row is not None:
            await self._revoke_family(row.family_id, "logout")
        await self._mark_revoked(principal.sid)
        await self._audit.record("logout", user_id=principal.subject)

    async def logout_everywhere(self, user_id: str, reason: str = "logout_all") -> int:
        ids = await self.sessions.revoke_all_for_user(user_id, reason)
        for sid in ids:
            await self._mark_revoked(sid)
        await self._audit.record("logout_all", user_id=user_id, sessions=len(ids))
        return len(ids)

    async def change_password(self, principal: Principal, current: str, new: str) -> None:
        user = await self.users.by_id(principal.subject)
        if user is None or not await verify_password(user.password_hash, current):
            await self._audit.record("password_change_failed", user_id=principal.subject)
            raise _unauthorized("Current password is incorrect.")
        try:
            check_policy(new, user.username)
        except WeakPassword as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
        await self.users.set_password(user.id, await hash_password(new))
        await self.logout_everywhere(user.id, reason="password_changed")
        await self._audit.record("password_changed", user_id=user.id)

    # ----------------------------------------------------------------------- devices
    async def exchange_device(self, device_id: str, secret: str, ip: str) -> IssuedTokens:
        await self._limiter.enforce(f"device:ip:{ip}", limit=60, window=300)
        await self._limiter.enforce(f"device:id:{device_id[:36]}", limit=30, window=300)
        device = await self.devices.authenticate(device_id, secret)
        if device is None:
            await self._audit.record("device_auth_failed", ip=ip, device_id=device_id[:36])
            raise _unauthorized("Invalid device credentials.")
        await self.devices.touch(device.id)
        ttl = self._s.device_token_minutes * 60
        sid = f"dev:{device.id}"
        access = self._codec.issue(sub=f"device:{device.id}", role=AGENT, sid=sid, ttl_seconds=ttl)
        await self._cache.set(f"sess:{sid}", "1", _ACTIVE_TTL)
        return IssuedTokens(access, ttl, None, device.name, AGENT)

    async def revoke_device(self, device_id: str, by: str) -> bool:
        revoked = await self.devices.revoke(device_id)
        await self._mark_revoked(f"dev:{device_id}")
        if revoked:
            await self._audit.record("device_revoked", device_id=device_id, by=by)
        return revoked

    # --------------------------------------------------------------- request checking
    async def authenticate(self, token: str) -> Principal:
        try:
            claims: Claims = self._codec.verify(token)
        except TokenError as exc:
            log.debug("token rejected", extra={"reason": str(exc)})
            raise _unauthorized("Invalid or expired token.") from None
        if claims.kind == "device" and claims.sid != "dev:" + claims.sub.split(":", 1)[1]:
            raise _unauthorized("Invalid or expired token.")
        if not await self._session_active(claims.sid):
            raise _unauthorized("Session ended.")
        return Principal(claims.sub, claims.role, claims.sid, claims.kind)

    async def _session_active(self, sid: str) -> bool:
        cached = await self._cache.get(f"sess:{sid}")
        if cached is not None:
            return cached == "1"
        if sid.startswith("dev:"):
            active = await self.devices.is_active(sid[4:])
        else:
            active = await self.sessions.is_active(sid)
        await self._cache.set(f"sess:{sid}", "1" if active else "0",
                              _ACTIVE_TTL if active else _REVOKED_TTL)
        return active

    async def _mark_revoked(self, sid: str) -> None:
        await self._cache.set(f"sess:{sid}", "0", _REVOKED_TTL)

    async def _revoke_family(self, family_id: str, reason: str) -> None:
        for sid in await self.sessions.revoke_family(family_id, reason):
            await self._mark_revoked(sid)
