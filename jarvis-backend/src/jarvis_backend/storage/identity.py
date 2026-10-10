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
