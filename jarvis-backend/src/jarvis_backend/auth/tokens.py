"""JWT access tokens.

Security properties, each enforced explicitly rather than left to library defaults:

* **Algorithm pinned** to HS256 on both sign and verify -- the ``alg`` header in a
  presented token is never trusted (blocks ``alg=none`` and RS/HS confusion).
* **Required claims**: ``exp iat nbf iss aud sub jti sid typ role``. Missing => rejected.
* **Short lifetime** (default 15 min). Longevity comes from rotating refresh tokens.
* **Revocable**: the ``sid`` claim is checked against live session state on every request
  (see ``service.SessionGuard``), so logout takes effect immediately, not at expiry.
* **Key rotation**: tokens carry a ``kid``; previous secrets still verify while you roll
  to a new signing secret, then drop them.
* **typ** distinguishes access from any future token kinds so one cannot be replayed as
  another.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

import jwt

ALGORITHM = "HS256"
ACCESS = "access"
# Single-purpose short-lived kinds. They carry the SAME required claims and go through the
# SAME decode rigor as an access token, but a distinct `typ` means `verify()` rejects them
# as access tokens -- so an mfa_pending token can never authenticate a protected request.
MFA_PENDING = "mfa_pending"  # noqa: S105 - a token-type label, not a credential
_REQUIRED = ["exp", "iat", "nbf", "iss", "aud", "sub", "jti", "sid", "typ", "role"]
_LEEWAY_SECONDS = 5  # tolerate small clock drift between replicas


class TokenError(Exception):
    """The presented token is not acceptable. The message is safe to log, not to show."""


@dataclass(frozen=True)
class Claims:
    sub: str
    role: str
    sid: str
    jti: str
    exp: int
    kind: str  # "user" | "device"


def _kid(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()[:8]


class TokenCodec:
    def __init__(self, secret: str, previous: List[str], issuer: str, audience: str) -> None:
        if len(secret) < 32:
            raise ValueError("JWT signing secret must be at least 32 characters.")
        self._secret, self._issuer, self._audience = secret, issuer, audience
        self._keys: Dict[str, str] = {_kid(s): s for s in [secret, *previous] if s}

    def issue(self, *, sub: str, role: str, sid: str, ttl_seconds: int) -> str:
        now = int(time.time())
        payload = {
            "iss": self._issuer, "aud": self._audience, "sub": sub, "role": role,
            "sid": sid, "jti": str(uuid.uuid4()), "typ": ACCESS,
            "iat": now, "nbf": now, "exp": now + ttl_seconds,
        }
        return jwt.encode(payload, self._secret, algorithm=ALGORITHM,
                          headers={"kid": _kid(self._secret)})

    def issue_special(self, *, sub: str, sid: str, typ: str, ttl_seconds: int,
                      role: str = "admin") -> str:
        """Mint a single-purpose token (e.g. ``mfa_pending``) with a non-access ``typ``.

        It carries the full required-claim set so it is validated with the same rigor as
        an access token, but ``verify()`` will reject it for normal authentication."""
        now = int(time.time())
        payload = {
            "iss": self._issuer, "aud": self._audience, "sub": sub, "role": role,
            "sid": sid, "jti": str(uuid.uuid4()), "typ": typ,
            "iat": now, "nbf": now, "exp": now + ttl_seconds,
        }
        return jwt.encode(payload, self._secret, algorithm=ALGORITHM,
                          headers={"kid": _kid(self._secret)})

    def _decode(self, token: str) -> dict:
        header = jwt.get_unverified_header(token)
        secret = self._keys.get(header.get("kid", ""))
        if secret is None:
            raise TokenError("unknown signing key")
        try:
            return jwt.decode(
                token, secret, algorithms=[ALGORITHM], issuer=self._issuer,
                audience=self._audience, leeway=_LEEWAY_SECONDS,
                options={"require": _REQUIRED},
            )
        except jwt.ExpiredSignatureError:
            raise TokenError("expired") from None
        except jwt.PyJWTError as exc:
            raise TokenError(type(exc).__name__) from None

    def verify(self, token: str) -> Claims:
        try:
            data = self._decode(token)
        except TokenError:
            raise
        if data.get("typ") != ACCESS:
            raise TokenError("wrong token type")
        sub = str(data["sub"])
        return Claims(sub=sub, role=str(data["role"]), sid=str(data["sid"]), jti=str(data["jti"]),
                      exp=int(data["exp"]), kind="device" if sub.startswith("device:") else "user")

    def verify_special(self, token: str, *, expected_typ: str) -> Claims:
        """Validate a single-purpose token and require its exact ``typ`` (fail closed)."""
        if expected_typ == ACCESS:
            raise TokenError("wrong token type")
        data = self._decode(token)
        if data.get("typ") != expected_typ:
            raise TokenError("wrong token type")
        sub = str(data["sub"])
        return Claims(sub=sub, role=str(data["role"]), sid=str(data["sid"]), jti=str(data["jti"]),
                      exp=int(data["exp"]), kind="device" if sub.startswith("device:") else "user")


def build_codec(settings) -> Optional[TokenCodec]:
    previous = [s.strip() for s in settings.secret("jwt_previous_secrets").split(",") if s.strip()]
    return TokenCodec(settings.secret("jwt_secret"), previous, settings.jwt_issuer, settings.jwt_audience)
