"""ORM tables. One table per concern; JSON columns hold flexible payloads.

Columns holding personal or sensitive content use ``EncryptedText`` / ``EncryptedJSON``
(see ``crypto.py``): conversation text, memories, approval payloads, job output.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from jarvis_backend.crypto import EncryptedJSON, EncryptedText
from jarvis_backend.storage.db import UTCDateTime, utcnow


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- identity
class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # lower-cased
    password_hash: Mapped[str] = mapped_column(String(255))  # argon2id
    role: Mapped[str] = mapped_column(String(16), default="admin")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # TOTP second factor. The base32 secret is field-ENCRYPTED at rest (never plaintext);
    # it is set at enrollment and only `mfa_enabled` once a correct code is confirmed.
    mfa_secret: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_last_counter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # replay guard
    # argon2 hashes of one-time recovery codes (never plaintext); each used once.
    mfa_recovery_hashes: Mapped[Optional[list]] = mapped_column(EncryptedJSON, nullable=True)


class SessionRow(Base):
    """One refresh token. Rotated on every use; ``family_id`` ties a chain together so a
    replayed (stolen) token can revoke the whole chain."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # also the JWT `sid`
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(200), default="")
    # When the holder last proved a fresh second factor (step-up re-auth for high-risk grants).
    step_up_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class DeviceRow(Base):
    """A machine identity for a local agent: its own secret, individually revocable."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    secret_hash: Mapped[str] = mapped_column(String(64))  # sha256 of a 256-bit random secret
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


# ------------------------------------------------------------------------ audit trail
class AuditEntry(Base):
    """Append-only, hash-chained. ``hash = sha256(prev_hash + canonical(entry))``."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


# ---------------------------------------------------------------------------- workflow
class ApprovalRow(Base):
    """PENDING -> GRANTED -> EXECUTING -> EXECUTED | FAILED, or DENIED / EXPIRED."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(EncryptedText)
    risk: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    request: Mapped[Optional[dict[str, Any]]] = mapped_column(EncryptedJSON, nullable=True)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(EncryptedJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class TurnRow(Base):
    """One user utterance and the agent's answer: conversation history + agent stats."""

    __tablename__ = "turns"
    __table_args__ = (Index("ix_turns_session_created", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128))
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    utterance: Mapped[str] = mapped_column(EncryptedText)
    speech: Mapped[str] = mapped_column(EncryptedText)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    pending_approval_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    trace: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class MemoryRow(Base):
    """Long-term memory the user (or WONG) asked JARVIS to keep."""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True, default="fact")
    text: Mapped[str] = mapped_column(EncryptedText)
    tags: Mapped[list[str]] = mapped_column(EncryptedJSON, default=list)
    agent_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="voice")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class AgentSettingRow(Base):
    """Per-agent overrides on top of the built-in defaults."""

    __tablename__ = "agent_settings"

    agent_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    voice: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class JobRunRow(Base):
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="cron")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    output: Mapped[str] = mapped_column(EncryptedText, default="")
    error: Mapped[str] = mapped_column(Text, default="")


class KeyValueRow(Base):
    """Small shared state that must be visible to every replica (e.g. runtime heartbeat)."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
