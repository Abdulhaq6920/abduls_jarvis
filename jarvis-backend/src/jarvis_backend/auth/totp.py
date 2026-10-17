"""RFC 6238 time-based one-time passwords, stdlib only.

This is a deliberate, self-contained implementation using ``hmac`` + ``hashlib`` +
``base64`` + ``secrets`` rather than ``pyotp``. ``pyotp`` is the documented dependency
(see ``pyproject.toml`` / ``requirements.txt``) but the offline runtime venv does not and
cannot install it, so verification -- which must fail closed -- is never allowed to depend
on an un-importable package. We therefore implement the ~40 lines of RFC 6238 directly and
never ``import pyotp`` anywhere in the runtime.

Design choices match what every standard authenticator app (Google Authenticator, Authy,
Microsoft Authenticator) expects by default: SHA1, 6 digits, a 30-second period.

Security notes:

* ``generate_secret`` draws 20 bytes from ``secrets`` (never ``random``) -- the usual TOTP
  key length; the base32 encoding is what the ``otpauth://`` URI carries.
* ``verify`` compares with :func:`hmac.compare_digest` (constant time) and returns the
  matched counter so the caller can store it and reject a replay within the same step.
* A small ``window`` tolerates one step of clock drift either side; anything outside the
  window is rejected (fail closed).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote, urlencode

_DIGITS = 6
_PERIOD = 30
_SECRET_BYTES = 20


def generate_secret() -> str:
    """A fresh base32 TOTP secret (no padding), suitable for an ``otpauth://`` URI."""
    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def current_counter(ts: Optional[float] = None) -> int:
    return int((ts if ts is not None else time.time()) // _PERIOD)


def _normalise(secret: str) -> bytes:
    padded = secret.strip().replace(" ", "").upper()
    padded += "=" * (-len(padded) % 8)
    return base64.b32decode(padded, casefold=True)


def _code_for_counter(key: bytes, counter: int) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFF_FFFF
    return str(truncated % (10 ** _DIGITS)).zfill(_DIGITS)


def totp_now(secret: str, counter: Optional[int] = None) -> str:
    """The current (or a specific counter's) 6-digit code for ``secret``."""
    if counter is None:
        counter = current_counter()
    return _code_for_counter(_normalise(secret), counter)


def verify(secret: str, code: str, *, window: int = 1,
           last_counter: Optional[int] = None, ts: Optional[float] = None) -> Optional[int]:
    """Return the matched counter if ``code`` is valid, else ``None`` (fail closed).

    ``window`` steps of drift are tolerated either side of now. A match whose counter is
    ``<= last_counter`` is rejected as a replay within the same (or an earlier) time step.
    """
    candidate = (code or "").strip()
    if len(candidate) != _DIGITS or not candidate.isdigit():
        return None
    try:
        key = _normalise(secret)
    except (ValueError, TypeError):
        return None
    now = current_counter(ts)
    for counter in range(now - window, now + window + 1):
        if counter < 0:
            continue
        if last_counter is not None and counter <= last_counter:
            continue
        if hmac.compare_digest(_code_for_counter(key, counter), candidate):
            return counter
    return None


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    """An ``otpauth://totp/...`` URI any standard authenticator app can import."""
    label = quote(f"{issuer}:{account}", safe=":")
    params = urlencode({
        "secret": secret, "issuer": issuer,
        "algorithm": "SHA1", "digits": _DIGITS, "period": _PERIOD,
    })
    return f"otpauth://totp/{label}?{params}"
