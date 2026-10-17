"""Backend configuration and secret handling.

Where values come from (highest priority first):

1. real environment variables (``JARVIS_*``) -- what Docker / your host sets
2. files in a secrets directory (``/run/secrets`` or ``JARVIS_SECRETS_DIR``) -- the
   Docker/Kubernetes secrets mechanism: one file per setting, file name = variable name
3. a local ``.env`` file (development only; it is git-ignored and excluded from images)

Secret handling rules enforced here:

* every secret is a ``SecretStr`` -- it prints as ``**********`` in logs, reprs and
  tracebacks and must be unwrapped deliberately with ``get_secret_value()``;
* in ``JARVIS_ENV=prod`` the process REFUSES TO START on missing, short, placeholder or
  structurally unsafe secrets/settings (it lists the problems, never the values);
* in dev, missing secrets are generated once into the data directory (mode 0600) so the
  first run works without ceremony, and a posture report flags that they are dev-only.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_LENGTH = 32
_PLACEHOLDERS = ("change", "example", "secret", "password", "default", "your-", "xxxx", "todo")
_PRIVATE_DB_HOSTS = {"localhost", "127.0.0.1", "::1", "db", "postgres"}
# Hosts a plaintext http:// LLM base URL may use (a local Ollama server). A Bearer key must
# never be sent over http:// to any other host.
_LLM_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _secrets_dir() -> Optional[str]:
    explicit = os.environ.get("JARVIS_SECRETS_DIR")
    if explicit:
        return explicit
    return "/run/secrets" if Path("/run/secrets").is_dir() else None


@dataclass(frozen=True)
class Finding:
    level: Literal["error", "warning"]
    code: str
    message: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JARVIS_", env_file=".env", env_file_encoding="utf-8",
        extra="ignore", secrets_dir=_secrets_dir(),
    )

    # --- runtime -------------------------------------------------------------------
    env: Literal["dev", "prod"] = "dev"
    host: str = "127.0.0.1"
    port: int = 8077
    data_dir: Path = Path.home() / ".jarvis"
    version: str = "0.2.0"

    # --- network exposure ----------------------------------------------------------
    # Comma separated Host headers we answer to (blocks DNS-rebinding attacks).
    allowed_hosts: str = "localhost,127.0.0.1"
    # The browser origin of the dashboard, e.g. https://jarvis.example.com. Used to
    # verify the Origin header on cookie-authenticated requests (CSRF defence).
    public_origin: str = ""
    # Number of reverse proxies in front of the API whose X-Forwarded-For we trust (nginx: 1).
    # 0 means "ignore the header entirely" -- a client can forge it.
    trusted_proxy_hops: int = 0
    max_body_bytes: int = 65_536
    max_ws_connections: int = 200
    max_ws_per_principal: int = 5

    # --- authentication ------------------------------------------------------------
    jwt_secret: Optional[SecretStr] = None
    # Old signing secrets still accepted for VERIFICATION during a rotation.
    jwt_previous_secrets: Optional[SecretStr] = None
    jwt_issuer: str = "jarvis"
    jwt_audience: str = "jarvis-api"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    device_token_minutes: int = 10
    cookie_name: str = "jarvis_refresh"
    cookie_secure: Optional[bool] = None  # default: True in prod, False in dev
    login_attempts_per_window: int = 5
    login_window_seconds: int = 900
    assist_per_minute: int = 60

    # --- multi-factor auth (TOTP) + step-up re-auth --------------------------------
    # Issuer shown in the authenticator app and the otpauth:// URI.
    mfa_issuer: str = "JARVIS"
    # One-time recovery codes generated when MFA is enabled (shown once, stored hashed).
    mfa_recovery_codes: int = 10
    # How long a step-up proof stays fresh for high-risk approvals.
    step_up_window_seconds: int = 300
    # Harden approvals: when True, a high-risk grant requires the admin to have MFA.
    approval_require_mfa: bool = False
    # Lowest risk level that forces a step-up on a GRANT: "DANGEROUS" (default) or
    # "RESTRICTED" (also gates RESTRICTED grants). SAFE never requires step-up.
    approval_step_up_risk: Literal["DANGEROUS", "RESTRICTED"] = "DANGEROUS"

    # --- data protection -----------------------------------------------------------
    # Fernet key(s), comma separated, newest first. Encrypts conversations, memories and
    # approval payloads at rest. Generate with `python -m jarvis_backend init-secrets`.
    data_key: Optional[SecretStr] = None
    turn_retention_days: int = 30
    history_turns: int = 6
    approval_ttl_seconds: int = 900

    # --- storage -------------------------------------------------------------------
    # Empty => SQLite file in data_dir. Production: postgresql+asyncpg://user:pw@host/db
    database_url: Optional[SecretStr] = None
    database_ssl: Literal["disable", "prefer", "require", "verify-ca", "verify-full"] = "prefer"
    database_ca_file: str = ""
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- redis ---------------------------------------------------------------------
    # Empty => in-process cache/limiter/bus (single replica only). Use rediss:// for TLS.
    redis_url: Optional[SecretStr] = None
    cache_default_ttl: int = 60
    cache_max_local_items: int = 5000

    # --- scheduler -----------------------------------------------------------------
    # Run the cron scheduler inside the API process. Disable on API replicas and run the
    # dedicated `worker`, otherwise every replica fires every job.
    run_scheduler: bool = True
    timezone: str = ""

    # --- llm -----------------------------------------------------------------------
    # Which language-model provider the reasoning agents (VISION / DR_STRANGE /
    # QUICKSILVER / NICK_FURY) talk to. "echo" is the deterministic offline default used
    # in dev and tests; "openai"/"ollama" speak the OpenAI-compatible /v1 chat API.
    llm_provider: Literal["echo", "openai", "ollama"] = "echo"
    llm_model: str = "gpt-4o-mini"
    # OpenAI-compatible base URL, e.g. https://api.openai.com/v1 or http://localhost:11434/v1.
    # Empty with a non-echo provider falls back to EchoLLM (see build_llm).
    llm_base_url: str = ""
    # Optional: required for OpenAI, not for a local Ollama server.
    llm_api_key: Optional[SecretStr] = None
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 512

    # --- search --------------------------------------------------------------------
    # Which web-search engine SHURI's research uses. "none" is the disabled default used in
    # dev and tests (no network, no key). "duckduckgo"/"generic" talk to a DuckDuckGo-shaped
    # (or SearxNG) JSON endpoint via GenericJsonSearch; disabled is a valid state.
    search_provider: Literal["none", "duckduckgo", "generic"] = "none"
    # Search endpoint base URL, e.g. https://api.duckduckgo.com/ or a SearxNG /search URL.
    # Empty with a non-none provider falls back to DisabledSearch (see build_search).
    search_base_url: str = ""
    # Optional: most engines (DuckDuckGo) need none; a keyed engine sends it as a Bearer header.
    search_api_key: Optional[SecretStr] = None
    search_timeout_seconds: float = 10.0
    search_max_results: int = 5

    # --- observability -------------------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    log_to_file: bool = True
    metrics_enabled: bool = True
    # Prometheus scrapes /metrics with `Authorization: Bearer <this>`.
    metrics_token: Optional[SecretStr] = None

    # ------------------------------------------------------------------ derived values
    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def host_list(self) -> List[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def secure_cookies(self) -> bool:
        return self.cookie_secure if self.cookie_secure is not None else self.is_prod

    @property
    def effective_database_url(self) -> str:
        raw = self.database_url.get_secret_value().strip() if self.database_url else ""
        if not raw:
            return f"sqlite+aiosqlite:///{(self.data_dir / 'jarvis.db').as_posix()}"
        # Be forgiving about what people paste from a provider dashboard.
        for prefix in ("postgres://", "postgresql://"):
            if raw.startswith(prefix):
                return "postgresql+asyncpg://" + raw[len(prefix):]
        return raw

    @property
    def is_sqlite(self) -> bool:
        return self.effective_database_url.startswith("sqlite")

    @property
    def redis_url_value(self) -> str:
        return self.redis_url.get_secret_value().strip() if self.redis_url else ""

    @property
    def llm_base_url_value(self) -> str:
        return self.llm_base_url.strip().rstrip("/")

    @property
    def search_base_url_value(self) -> str:
        return self.search_base_url.strip().rstrip("/")

    def secret(self, name: str) -> str:
        value = getattr(self, name)
        return value.get_secret_value() if value else ""

    # ------------------------------------------------------------------ validation
    def findings(self) -> List[Finding]:
        """Every security-relevant problem with the current configuration."""
        out: List[Finding] = []

        def err(code: str, msg: str) -> None:
            out.append(Finding("error" if self.is_prod else "warning", code, msg))

        def warn(code: str, msg: str) -> None:
            out.append(Finding("warning", code, msg))

        jwt = self.secret("jwt_secret")
        if len(jwt) < MIN_SECRET_LENGTH:
            err("jwt_secret_weak", f"JARVIS_JWT_SECRET must be at least {MIN_SECRET_LENGTH} characters.")
        elif _looks_like_placeholder(jwt):
            err("jwt_secret_placeholder", "JARVIS_JWT_SECRET looks like a placeholder value.")
        if not self.secret("data_key"):
            err("data_key_missing", "JARVIS_DATA_KEY is not set: data at rest is NOT encrypted.")
        if self.is_prod:
            if self.is_sqlite:
                err("sqlite_in_prod", "Production should use PostgreSQL (JARVIS_DATABASE_URL).")
            if not self.redis_url_value:
                err("redis_missing", "Production needs Redis (JARVIS_REDIS_URL) for revocation, "
                                     "rate limiting and cross-replica events.")
            if "*" in self.host_list or not self.host_list:
                err("hosts_open", "JARVIS_ALLOWED_HOSTS must list your real host names, not '*'.")
            if not self.public_origin.startswith("https://"):
                err("origin_not_https", "JARVIS_PUBLIC_ORIGIN must be an https:// origin.")
            if not self.secure_cookies:
                err("cookie_insecure", "Cookies must be Secure in production.")
            if self.metrics_enabled and len(self.secret("metrics_token")) < 24:
                err("metrics_token_weak", "Set JARVIS_METRICS_TOKEN (>= 24 chars) or disable metrics.")
        if not self.is_sqlite and self.database_ssl in ("disable", "prefer"):
            host = urlparse(self.effective_database_url.replace("+asyncpg", "")).hostname or ""
            if self.database_ssl == "disable" and host not in _PRIVATE_DB_HOSTS:
                warn("db_tls_off", "Database traffic to a remote host is not encrypted (database_ssl=disable).")
            elif self.database_ssl == "prefer" and host not in _PRIVATE_DB_HOSTS:
                warn("db_tls_optional", "database_ssl=prefer can silently fall back to plaintext; use "
                                        "require or verify-full.")
        if self.llm_provider != "echo" and self.llm_base_url_value:
            # A remote plaintext http:// endpoint leaks the prompt (user task + bounded local
            # context) and any Bearer key on the wire -- confidentiality must not depend on
            # whether a key is set. Loopback http:// (local Ollama) stays allowed. A malformed
            # URL (e.g. an unterminated IPv6 literal) makes urlparse raise -- treat it as unsafe
            # (fail closed) rather than letting the error abort config validation.
            try:
                parsed = urlparse(self.llm_base_url_value)
                _ = parsed.port  # non-numeric port raises ValueError lazily; force it here
                plaintext_remote = parsed.scheme == "http" and (parsed.hostname or "") not in _LLM_LOOPBACK_HOSTS
            except ValueError:
                plaintext_remote = True
            if plaintext_remote:
                err("llm_plaintext_remote", "JARVIS_LLM_BASE_URL is a plaintext HTTP endpoint on a "
                                            "remote host; the prompt (and any JARVIS_LLM_API_KEY) would "
                                            "travel in cleartext. Use an https:// base URL for hosted "
                                            "providers (loopback http:// is allowed for local Ollama).")
        if self.search_provider != "none" and self.search_base_url_value:
            # A remote plaintext http:// search endpoint leaks the query text (and any Bearer
            # key) on the wire -- confidentiality must not depend on whether a key is set.
            # Loopback http:// (local SearxNG) stays allowed. A malformed URL makes urlparse
            # raise -- treat it as unsafe (fail closed) rather than aborting config validation.
            # NB: "none" (disabled) is a valid state and produces NO finding.
            try:
                parsed = urlparse(self.search_base_url_value)
                _ = parsed.port  # non-numeric port raises ValueError lazily; force it here
                search_plaintext_remote = parsed.scheme == "http" and (parsed.hostname or "") not in _LLM_LOOPBACK_HOSTS
            except ValueError:
                search_plaintext_remote = True
            if search_plaintext_remote:
                err("search_plaintext_remote", "JARVIS_SEARCH_BASE_URL is a plaintext HTTP endpoint on "
                                               "a remote host; the query (and any JARVIS_SEARCH_API_KEY) "
                                               "would travel in cleartext. Use an https:// base URL for "
                                               "hosted engines (loopback http:// is allowed for local "
                                               "SearxNG).")
        if self.redis_url_value.startswith("redis://") and self.is_prod:
            host = urlparse(self.redis_url_value).hostname or ""
            if host not in _PRIVATE_DB_HOSTS:
                warn("redis_tls_off", "Redis connection is not TLS (use rediss://).")
        if self.access_token_minutes > 60:
            warn("access_token_long", "Access tokens longer than 60 minutes weaken revocation.")
        return out

    @model_validator(mode="after")
    def _enforce_production_rules(self) -> Settings:
        if self.is_prod:
            problems = [f.message for f in self.findings() if f.level == "error"]
            if problems:
                raise ValueError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))
        return self

    def describe(self) -> dict:
        """Safe-to-log summary: secrets appear only as set/unset + length."""
        def shown(name: str) -> str:
            value = self.secret(name)
            return f"set(len={len(value)})" if value else "unset"

        return {
            "env": self.env, "version": self.version, "host": self.host, "port": self.port,
            "database": "sqlite" if self.is_sqlite else "postgresql",
            "database_ssl": None if self.is_sqlite else self.database_ssl,
            "redis": "configured" if self.redis_url_value else "none (in-process)",
            "jwt_secret": shown("jwt_secret"), "data_key": shown("data_key"),
            "metrics_token": shown("metrics_token"), "cookie_secure": self.secure_cookies,
            "allowed_hosts": self.host_list,
        }


def _looks_like_placeholder(value: str) -> bool:
    low = value.lower()
    return any(p in low for p in _PLACEHOLDERS) or len(set(value)) < 10


# ------------------------------------------------------------------ dev secret bootstrap
def _write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # Windows: access is governed by the per-user profile ACL
        pass


def with_dev_secrets(settings: Settings) -> Settings:
    """Fill missing secrets for a DEV install from a private file in the data dir.

    Never used in prod (there, missing secrets are a startup error). The generated file
    is stable across restarts, so sessions and encrypted data survive a restart.
    """
    if settings.is_prod:
        return settings
    from cryptography.fernet import Fernet

    path = settings.data_dir / "dev_secrets.json"
    stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    changed = False
    if not settings.secret("jwt_secret") and "jwt_secret" not in stored:
        stored["jwt_secret"], changed = secrets.token_urlsafe(48), True
    if not settings.secret("data_key") and "data_key" not in stored:
        stored["data_key"], changed = Fernet.generate_key().decode(), True
    if changed:
        _write_private(path, stored)
    updates = {}
    if not settings.secret("jwt_secret"):
        updates["jwt_secret"] = SecretStr(stored["jwt_secret"])
    if not settings.secret("data_key"):
        updates["data_key"] = SecretStr(stored["data_key"])
    return settings.model_copy(update=updates) if updates else settings


def warn_if_env_file_is_world_readable(path: Path = Path(".env")) -> Optional[str]:
    """POSIX only: a group/world-readable .env leaks secrets to other local users."""
    if os.name == "nt" or not path.exists():
        return None
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return f"{path} is readable by other users; run: chmod 600 {path}"
    return None


@lru_cache
def get_settings() -> Settings:
    return with_dev_secrets(Settings())
