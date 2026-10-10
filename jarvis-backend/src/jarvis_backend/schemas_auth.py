"""Request/response models for authentication and device management."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    # Capped: argon2 cost grows with input, so an unbounded password is a DoS lever.
    password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth scheme name, not a credential
    expires_in: int
    username: str
    role: str


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class MeOut(BaseModel):
    subject: str
    role: str
    kind: str
    username: Optional[str] = None


class DeviceTokenIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=36)
    secret: str = Field(min_length=1, max_length=128)


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[\w .\-]+$")


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime
    created_by: str
    last_seen_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class DeviceCreated(DeviceOut):
    # Shown exactly once; only a hash is stored, so it cannot be recovered later.
    secret: str


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    expires_at: datetime
    ip: str
    user_agent: str
