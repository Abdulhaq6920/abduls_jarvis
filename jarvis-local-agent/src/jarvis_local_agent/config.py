"""Local runtime configuration (separate ``.env`` from the backend's).

Loaded from ``JARVIS_*`` environment variables and this directory's ``.env``. The device
secret is a ``SecretStr`` (never printed) and is preferably kept in the Windows Credential
Manager instead of any file: see ``credentials.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, List, Optional

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _split(value: object) -> object:
    if isinstance(value, str):
        return [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]
    return value


class RuntimeConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", env_file=".env", env_file_encoding="utf-8",
                                      extra="ignore")

    # --- backend + identity ----------------------------------------------------------
    backend_url: str = "http://127.0.0.1:8077"
    device_id: str = ""
    device_secret: Optional[SecretStr] = None
    # Credentials are only ever sent over TLS, except to this machine. Set to true ONLY for
    # a trusted private network you control, and understand the secret is then sniffable.
    allow_insecure_backend: bool = False

    # --- voice -----------------------------------------------------------------------
    wake_phrase: str = "hey jarvis"
    wake_sensitivity: float = 0.5
    conversation_timeout_s: float = 12.0
    wake_engine: str = "dev"
    stt_engine: str = "dev"
    tts_engine: str = "dev"
    # Offline Vosk model selection: an empty path means "use the bundled language code"; a
    # non-empty path points at a downloaded model directory (e.g. a non-English model).
    vosk_model_lang: str = "en-us"
    vosk_model_path: str = ""
    # Optional preference for the SAPI voice (substring of a voice name/id/lang); empty keeps
    # the default english david/zira selection.
    tts_voice_hint: str = ""

    # --- local security allow-lists (the real boundary; the backend cannot widen them) ---
    allowed_paths: Annotated[List[str], NoDecode] = [str(Path.cwd())]
    allowed_apps: Annotated[List[str], NoDecode] = ["notepad", "code", "explorer", "chrome", "msedge"]

    # --- housekeeping ----------------------------------------------------------------
    data_dir: Path = Path.home() / ".jarvis"
    log_level: str = "INFO"
    log_format: str = "text"
    heartbeat_seconds: float = 15.0
    approval_poll_seconds: float = 3.0

    @field_validator("allowed_paths", "allowed_apps", mode="before")
    @classmethod
    def _parse_lists(cls, value: object) -> object:
        return _split(value)

    def model_post_init(self, _ctx: object) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def secret(self) -> str:
        return self.device_secret.get_secret_value() if self.device_secret else ""

    @classmethod
    def load(cls) -> RuntimeConfig:
        """Settings from env/.env, with device credentials from the OS credential store when
        they are not set explicitly (Windows Credential Manager beats a plain-text file)."""
        config = cls()
        if not config.device_id or not config.secret():
            from jarvis_local_agent.credentials import load_from_keyring

            stored = load_from_keyring()
            if stored:
                config.device_id, config.device_secret = stored[0], SecretStr(stored[1])
        return config

    def backend_is_safe_transport(self) -> bool:
        from urllib.parse import urlparse
        parsed = urlparse(self.backend_url)
        return parsed.scheme == "https" or (parsed.hostname or "") in _LOOPBACK or self.allow_insecure_backend
