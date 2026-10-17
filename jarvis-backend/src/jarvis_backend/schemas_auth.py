"""Request/response models for authentication and device management."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# A six-digit TOTP code or an alphanumeric recovery code (dashes allowed).
_CODE = Field(min_length=6, max_length=14, pattern=r"^[0-9A-Za-z\-]{6,14}$")


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


# ------------------------------------------------------------------------------ MFA
class MfaEnrollOut(BaseModel):
    """Returned when enrollment starts: the secret and URI for manual entry / QR import.

    The secret is NOT returned again once MFA is confirmed."""

    secret: str
    otpauth_uri: str


class MfaVerifyIn(BaseModel):
    code: str = _CODE


class MfaEnableOut(BaseModel):
    """Recovery codes are shown exactly once here; only their hashes are stored."""

    recovery_codes: List[str]


class MfaDisableIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    # Optional at the schema layer so a password-only attempt is DENIED by the service
    # (fail closed, 401) rather than bounced as a 422 validation error -- disable must
    # never succeed on the password alone, and the matrix asserts a 401 for that case.
    code: Optional[str] = Field(default=None, max_length=14, pattern=r"^[0-9A-Za-z\-]{6,14}$")


class MfaLoginIn(BaseModel):
    mfa_token: str = Field(min_length=1, max_length=4096)
    code: str = _CODE


class MfaStatusOut(BaseModel):
    enabled: bool


class MfaRequiredOut(BaseModel):
    """Login's answer when MFA is enabled: no access/refresh yet, a second call is needed."""

    mfa_required: bool = True
    mfa_token: str
    expires_in: int


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
