"""Field-level encryption for data at rest.

Sensitive columns (conversation text, memories, approval payloads) are encrypted before
they reach the database and decrypted when read, transparently, through the SQLAlchemy
column types at the bottom of this file. A stolen database dump or backup therefore
contains ciphertext only.

* Algorithm: Fernet (AES-128-CBC + HMAC-SHA256, authenticated, with a timestamp).
* Key rotation: ``JARVIS_DATA_KEY`` accepts several comma-separated keys, newest first.
  New data is written with the first key; every listed key can still decrypt.
* Values are stored as ``enc:v1:<token>``. A value without the prefix is treated as
  legacy plaintext and read as-is, so enabling encryption on an existing database works.

Honest limits: this protects data at rest, not a compromised running process (the key
lives in process memory), and a database administrator who can also write rows could swap
ciphertexts between rows. Pair it with disk/volume encryption and least-privilege DB roles.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

PREFIX = "enc:v1:"


class DecryptionError(Exception):
    """Ciphertext could not be authenticated: wrong key, or the data was tampered with."""


class FieldCipher:
    def __init__(self, keys: List[str]) -> None:
        self._fernet: Optional[MultiFernet] = (
            MultiFernet([Fernet(k.encode()) for k in keys]) if keys else None
        )

    @classmethod
    def from_setting(cls, raw: str) -> FieldCipher:
        return cls([k.strip() for k in raw.split(",") if k.strip()])

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        if self._fernet is None:
            return value
        return PREFIX + self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value.startswith(PREFIX):
            return value  # legacy plaintext
        if self._fernet is None:
            raise DecryptionError("Encrypted data found but JARVIS_DATA_KEY is not configured.")
        try:
            return self._fernet.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise DecryptionError("Could not decrypt stored data (wrong key or tampered).") from exc


_cipher = FieldCipher([])


def set_cipher(cipher: FieldCipher) -> None:
    """Install the process-wide cipher. Called once at start-up by the container."""
    global _cipher
    _cipher = cipher


def get_cipher() -> FieldCipher:
    return _cipher


# ------------------------------------------------------------------- column types
class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Optional[str], dialect: Any) -> Optional[str]:
        return None if value is None else _cipher.encrypt(value)

    def process_result_value(self, value: Optional[str], dialect: Any) -> Optional[str]:
        return None if value is None else _cipher.decrypt(value)


class EncryptedJSON(TypeDecorator):
    """Any JSON-serialisable value, stored as encrypted text."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Optional[str]:
        return None if value is None else _cipher.encrypt(json.dumps(value, default=str))

    def process_result_value(self, value: Optional[str], dialect: Any) -> Any:
        return None if value is None else json.loads(_cipher.decrypt(value))
