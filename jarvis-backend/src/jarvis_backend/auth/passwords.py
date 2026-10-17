"""Password hashing and policy.

* **argon2id** (memory-hard; the current OWASP first choice) via ``argon2-cffi``.
* Hashing is CPU/memory heavy on purpose, so it runs in a worker thread and never blocks
  the event loop.
* Unknown usernames are verified against a dummy hash so the response time does not
  reveal which usernames exist.
* Policy follows NIST 800-63B: length matters, composition rules do not. We enforce a
  minimum length and reject trivially guessable choices.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH, MAX_LENGTH = 12, 128
_COMMON = frozenset({
    "password", "password1", "password123", "123456789012", "qwertyuiopas", "iloveyou1234",
    "administrator", "letmein12345", "welcome12345", "jarvis123456", "jarvisjarvis",
    "starkindustries", "changemechangeme", "passwordpassword", "111111111111", "000000000000",
})

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
_DUMMY_HASH = _hasher.hash("jarvis-dummy-password-for-timing-equalisation")


class WeakPassword(ValueError):
    pass


def check_policy(password: str, username: str = "") -> None:
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"Password must be at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise WeakPassword(f"Password must be at most {MAX_LENGTH} characters.")
    lowered = password.lower()
    if lowered in _COMMON or (username and username.lower() in lowered):
        raise WeakPassword("That password is too easy to guess.")
    if len(set(password)) < 5:
        raise WeakPassword("Password has too little variety.")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(stored_hash: Optional[str], password: str) -> bool:
    """Constant-effort verification: a missing user costs the same as a wrong password."""
    target = stored_hash or _DUMMY_HASH
    try:
        ok = await asyncio.to_thread(_hasher.verify, target, password)
    except (VerificationError, InvalidHashError):
        return False
    return ok and stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


async def verify_hash(stored_hash: str, value: str) -> bool:
    """Verify a value against a specific argon2 hash (e.g. one recovery-code hash).

    Unlike :func:`verify_password` this takes a concrete hash and is used to walk a small
    list of recovery-code hashes; a mismatch simply returns False."""
    try:
        return await asyncio.to_thread(_hasher.verify, stored_hash, value)
    except (VerificationError, InvalidHashError):
        return False
