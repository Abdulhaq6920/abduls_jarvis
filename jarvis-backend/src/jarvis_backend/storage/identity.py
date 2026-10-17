"""Stores for users, refresh-token sessions, and device credentials."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import select, update

from jarvis_backend.storage.db import Database, utcnow
from jarvis_backend.storage.models import DeviceRow, SessionRow, UserRow


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------- users
class UserStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def count(self) -> int:
        async with self._db.session() as s:
            return len((await s.execute(select(UserRow.id))).all())

    async def any_admin_has_mfa(self) -> bool:
        """True if at least one enabled admin has MFA on (used for a prod posture warning)."""
        async with self._db.session() as s:
            rows = (await s.execute(select(UserRow.mfa_enabled).where(
                UserRow.role == "admin", UserRow.disabled.is_(False),
                UserRow.mfa_enabled.is_(True)))).all()
        return len(rows) > 0

    async def create(self, username: str, password_hash: str, role: str = "admin") -> UserRow:
        row = UserRow(id=str(uuid.uuid4()), username=username.strip().lower(),
                      password_hash=password_hash, role=role)
        async with self._db.session() as s:
            s.add(row)
        return row

    async def by_username(self, username: str) -> Optional[UserRow]:
        async with self._db.session() as s:
            return (await s.execute(
                select(UserRow).where(UserRow.username == username.strip().lower())
            )).scalar_one_or_none()

    async def by_id(self, user_id: str) -> Optional[UserRow]:
        async with self._db.session() as s:
            return await s.get(UserRow, user_id)

    async def set_password(self, user_id: str, password_hash: str) -> None:
        async with self._db.session() as s:
            await s.execute(update(UserRow).where(UserRow.id == user_id)
                            .values(password_hash=password_hash, password_changed_at=utcnow()))

    async def touch_login(self, user_id: str, new_hash: Optional[str] = None) -> None:
        values: dict = {"last_login_at": utcnow()}
        if new_hash:  # transparent upgrade when argon2 parameters were strengthened
            values["password_hash"] = new_hash
        async with self._db.session() as s:
            await s.execute(update(UserRow).where(UserRow.id == user_id).values(**values))

    # ---------------------------------------------------------------------------- mfa
    #
    # The MFA factor is one state machine guarded by ``mfa_state_version``. Every
    # transition reads the current version, then issues a conditional UPDATE whose WHERE
    # clause pins that version and bumps it. The checked ``rowcount == 1`` is the
    # single-winner guarantee that works IDENTICALLY on SQLite and PostgreSQL -- it does
    # NOT rely on ``SELECT ... FOR UPDATE`` (which SQLite silently ignores). A caller that
    # read a stale version loses the UPDATE (0 rows) and fails closed.
    async def set_mfa_pending(self, user_id: str, secret: str) -> bool:
        """Store a freshly generated (unconfirmed) TOTP secret; MFA is NOT yet enabled.

        Refused (returns False) once the account already has MFA enabled, so a stale
        enrollment that began while the account was disabled cannot commit after a
        concurrent confirmation and clear the just-activated factor. Fail closed."""
        async with self._db.session() as s:
            res = await s.execute(update(UserRow).where(
                UserRow.id == user_id, UserRow.mfa_enabled.is_(False)).values(
                mfa_secret=secret, mfa_enabled=False, mfa_last_counter=None,
                mfa_recovery_hashes=None,
                mfa_state_version=UserRow.mfa_state_version + 1))
            return res.rowcount == 1

    async def enable_mfa(self, user_id: str, recovery_hashes: List[str], *,
                         expected_secret: str, last_counter: Optional[int] = None) -> bool:
        """Flip MFA on after a correct code confirmed the pending secret.

        Bound to the EXACT secret verified AND to the version read at the start: the row is
        read, checked in Python (still pending, same secret), then a conditional UPDATE
        pins ``mfa_state_version`` and ``mfa_enabled=False`` and bumps the version. Two
        concurrent confirmations read the same version but only one UPDATE affects a row
        (``rowcount == 1``); the loser returns False and reveals no recovery set. A
        concurrent ``set_mfa_pending`` that swapped the secret bumped the version too, so
        this enable sees either a different version or a different secret and fails closed."""
        async with self._db.session() as s:
            row = (await s.execute(
                select(UserRow).where(UserRow.id == user_id))).scalar_one_or_none()
            if row is None or row.mfa_enabled or row.mfa_secret != expected_secret:
                return False
            version = row.mfa_state_version
            res = await s.execute(update(UserRow).where(
                UserRow.id == user_id, UserRow.mfa_enabled.is_(False),
                UserRow.mfa_state_version == version).values(
                mfa_enabled=True, mfa_recovery_hashes=list(recovery_hashes),
                mfa_last_counter=last_counter,
                mfa_state_version=UserRow.mfa_state_version + 1))
            return res.rowcount == 1

    async def disable_mfa(self, user_id: str) -> None:
        async with self._db.session() as s:
            await s.execute(update(UserRow).where(UserRow.id == user_id).values(
                mfa_secret=None, mfa_enabled=False, mfa_last_counter=None,
                mfa_recovery_hashes=None,
                mfa_state_version=UserRow.mfa_state_version + 1))

    async def advance_mfa_counter(self, user_id: str, counter: int,
                                  previous: Optional[int]) -> bool:
        """Compare-and-set the last-used TOTP counter: succeed only if the stored counter
        is still ``previous`` AND the new counter is strictly greater.

        This closes the replay race: two requests reading the same stored counter and
        matching the same TOTP cannot both advance -- exactly one UPDATE affects a row.
        The ``counter > mfa_last_counter`` guard also rejects an already-passed step even
        if ``previous`` was read as stale-null by a racing caller."""
        async with self._db.session() as s:
            # Strictly-increasing guard: the stored counter must still be ``previous`` AND
            # the new counter must be greater than whatever is stored. A duplicate that read
            # the same ``previous`` after the counter already moved to ``counter`` loses,
            # because ``counter > counter`` is false -- so a step cannot be re-advanced.
            prev_clause = (UserRow.mfa_last_counter.is_(None) if previous is None
                           else UserRow.mfa_last_counter == previous)
            res = await s.execute(update(UserRow).where(
                UserRow.id == user_id, UserRow.mfa_enabled.is_(True), prev_clause,
                (UserRow.mfa_last_counter.is_(None) | (UserRow.mfa_last_counter < counter))).values(
                mfa_last_counter=counter))
            return res.rowcount == 1

    async def consume_recovery_hash(self, user_id: str, expected_hash: str) -> bool:
        """Remove a single recovery-code hash (single-use), keyed by the matched hash.

        The recovery list is a field-encrypted opaque blob, so the removal cannot be
        pushed into SQL. Instead it is a version compare-and-set: read the row and its
        ``mfa_state_version``, remove the matched hash by VALUE in Python (never by index,
        which would shift), then UPDATE only if the version is unchanged, bumping it. Two
        parallel logins presenting the same code read the same version and the same list,
        but exactly one UPDATE affects a row (``rowcount == 1``) -- the first spends the
        code, the loser's stale-version UPDATE touches nothing and returns False. Portable:
        no ``FOR UPDATE`` (which SQLite ignores), so single-use holds on SQLite too."""
        async with self._db.session() as s:
            row = (await s.execute(
                select(UserRow).where(UserRow.id == user_id))).scalar_one_or_none()
            hashes = list(row.mfa_recovery_hashes or []) if row else []
            if row is None or expected_hash not in hashes:
                return False
            version = row.mfa_state_version
            hashes.remove(expected_hash)
            res = await s.execute(update(UserRow).where(
                UserRow.id == user_id,
                UserRow.mfa_state_version == version).values(
                mfa_recovery_hashes=hashes,
                mfa_state_version=UserRow.mfa_state_version + 1))
            return res.rowcount == 1


# ------------------------------------------------------------------------- sessions
class SessionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, *, user_id: str, family_id: Optional[str], refresh_secret: str,
                     lifetime: timedelta, ip: str, user_agent: str) -> SessionRow:
        row = SessionRow(
            id=str(uuid.uuid4()), user_id=user_id, family_id=family_id or str(uuid.uuid4()),
            refresh_hash=sha256_hex(refresh_secret), expires_at=utcnow() + lifetime,
            ip=ip[:64], user_agent=user_agent[:200],
        )
        async with self._db.session() as s:
            s.add(row)
        return row

    async def by_refresh_secret(self, refresh_secret: str) -> Optional[SessionRow]:
        async with self._db.session() as s:
            return (await s.execute(
                select(SessionRow).where(SessionRow.refresh_hash == sha256_hex(refresh_secret))
            )).scalar_one_or_none()

    async def get(self, session_id: str) -> Optional[SessionRow]:
        async with self._db.session() as s:
            return await s.get(SessionRow, session_id)

    async def is_active(self, session_id: str) -> bool:
        row = await self.get(session_id)
        return bool(row and row.revoked_at is None and row.expires_at > utcnow())

    async def mark_step_up(self, session_id: str, at: Optional[datetime] = None) -> None:
        """Record that this session just proved a fresh second factor (step-up re-auth)."""
        async with self._db.session() as s:
            await s.execute(update(SessionRow).where(SessionRow.id == session_id)
                            .values(step_up_at=at or utcnow()))

    async def rotate(self, old_id: str) -> bool:
        """Atomically retire a refresh token. False if someone else already did."""
        async with self._db.session() as s:
            res = await s.execute(
                update(SessionRow).where(SessionRow.id == old_id, SessionRow.revoked_at.is_(None))
                .values(revoked_at=utcnow(), revoked_reason="rotated"))
            return res.rowcount == 1

    async def revoke(self, session_id: str, reason: str = "logout") -> bool:
        async with self._db.session() as s:
            res = await s.execute(
                update(SessionRow).where(SessionRow.id == session_id, SessionRow.revoked_at.is_(None))
                .values(revoked_at=utcnow(), revoked_reason=reason))
            return res.rowcount == 1

    async def revoke_family(self, family_id: str, reason: str) -> List[str]:
        """Revoke every live session in a chain; returns their ids (for cache invalidation)."""
        async with self._db.session() as s:
            ids = [r for (r,) in (await s.execute(
                select(SessionRow.id).where(SessionRow.family_id == family_id,
                                            SessionRow.revoked_at.is_(None)))).all()]
            if ids:
                await s.execute(update(SessionRow).where(SessionRow.id.in_(ids))
                                .values(revoked_at=utcnow(), revoked_reason=reason))
            return ids

    async def revoke_all_for_user(self, user_id: str, reason: str) -> List[str]:
        async with self._db.session() as s:
            ids = [r for (r,) in (await s.execute(
                select(SessionRow.id).where(SessionRow.user_id == user_id,
                                            SessionRow.revoked_at.is_(None)))).all()]
            if ids:
                await s.execute(update(SessionRow).where(SessionRow.id.in_(ids))
                                .values(revoked_at=utcnow(), revoked_reason=reason))
            return ids

    async def list_for_user(self, user_id: str, limit: int = 20) -> List[SessionRow]:
        async with self._db.session() as s:
            return list((await s.execute(
                select(SessionRow).where(SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None),
                                         SessionRow.expires_at > utcnow())
                .order_by(SessionRow.created_at.desc()).limit(limit))).scalars().all())

    async def prune(self, older_than: timedelta = timedelta(days=30)) -> int:
        """Delete long-dead sessions so the table (and its audit surface) stays small."""
        from sqlalchemy import delete
        async with self._db.session() as s:
            res = await s.execute(delete(SessionRow).where(SessionRow.expires_at < utcnow() - older_than))
            return res.rowcount or 0


# -------------------------------------------------------------------------- devices
class DeviceStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, name: str, created_by: str) -> Tuple[DeviceRow, str]:
        """Returns (row, secret). The secret is shown ONCE and never stored in clear."""
        secret = secrets.token_urlsafe(32)
        row = DeviceRow(id=str(uuid.uuid4()), name=name[:64], secret_hash=sha256_hex(secret),
                        created_by=created_by[:64])
        async with self._db.session() as s:
            s.add(row)
        return row, secret

    async def authenticate(self, device_id: str, secret: str) -> Optional[DeviceRow]:
        async with self._db.session() as s:
            row = await s.get(DeviceRow, device_id)
        expected = row.secret_hash if row else sha256_hex("x")  # constant work either way
        ok = hmac.compare_digest(sha256_hex(secret), expected)
        return row if (row and ok and row.revoked_at is None) else None

    async def is_active(self, device_id: str) -> bool:
        async with self._db.session() as s:
            row = await s.get(DeviceRow, device_id)
        return bool(row and row.revoked_at is None)

    async def revoke(self, device_id: str) -> bool:
        async with self._db.session() as s:
            res = await s.execute(update(DeviceRow)
                                  .where(DeviceRow.id == device_id, DeviceRow.revoked_at.is_(None))
                                  .values(revoked_at=utcnow()))
            return res.rowcount == 1

    async def touch(self, device_id: str, at: Optional[datetime] = None) -> None:
        async with self._db.session() as s:
            await s.execute(update(DeviceRow).where(DeviceRow.id == device_id)
                            .values(last_seen_at=at or utcnow()))

    async def list(self) -> List[DeviceRow]:
        async with self._db.session() as s:
            return list((await s.execute(
                select(DeviceRow).order_by(DeviceRow.created_at.desc()))).scalars().all())
