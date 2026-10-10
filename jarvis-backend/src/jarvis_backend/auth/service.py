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

from jarvis_backend.auth.passwords import (
    WeakPassword,
    check_policy,
    hash_password,
    needs_rehash,
    verify_password,
)
from jarvis_backend.auth.tokens import Claims, TokenCodec, TokenError
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


class AuthService:
    def __init__(self, *, settings: Settings, users: UserStore, sessions: SessionStore,
                 devices: DeviceStore, codec: TokenCodec, cache: Cache, limiter: RateLimiter,
                 audit: AuditLog) -> None:
        self._s, self.users, self.sessions, self.devices = settings, users, sessions, devices
        self._codec, self._cache, self._limiter, self._audit = codec, cache, limiter, audit

    # ----------------------------------------------------------------------- login
    async def login(self, username: str, password: str, ip: str, user_agent: str) -> IssuedTokens:
        name = username.strip().lower()[:64]
        limit, window = self._s.login_attempts_per_window, self._s.login_window_seconds
        pair_key = f"login:pair:{ip}:{name}"
        await self._limiter.enforce(pair_key, limit=limit, window=window)
        await self._limiter.enforce(f"login:user:{name}", limit=limit * 4, window=window)
        await self._limiter.enforce(f"login:ip:{ip}", limit=limit * 6, window=window)

        user = await self.users.by_username(name)
        valid = await verify_password(user.password_hash if user else None, password)
        if not (valid and user and not user.disabled):
            await self._audit.record("login_failed", ip=ip, username_hash=_short_hash(name),
                                     reason="unknown_user" if user is None else "bad_credentials")
            raise _unauthorized("Invalid username or password.")

        await self._cache.delete("rl:" + pair_key)
        await self.users.touch_login(user.id, await hash_password(password)
                                     if needs_rehash(user.password_hash) else None)
        issued = await self._open_session(user.id, user.username, user.role, None,
                                          timedelta(days=self._s.refresh_token_days), ip, user_agent)
        await self._audit.record("login_succeeded", ip=ip, user=user.username, sid=issued[1])
        return issued[0]

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
            raise HTTPException(422, str(exc)) from None
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
