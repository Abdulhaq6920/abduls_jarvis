"""Logging setup shared by the backend, worker, and local agent.

One place defines how JARVIS logs so every process produces the same shape:

* ``text`` format for humans (dev), ``json`` format for log pipelines (prod/Docker).
* Every record carries a ``request_id`` (set per HTTP request, or per voice turn) so a
  single user action can be followed across the local agent and the backend.
* Optional size-rotated log file under the data directory.
* Secrets are scrubbed: any ``extra`` field whose name looks sensitive is redacted, and
  call sites must never log utterances above DEBUG.

Usage::

    from jarvis_shared.log import get_logger, setup_logging
    setup_logging("api", level="INFO", fmt="json")
    log = get_logger(__name__)
    log.info("assist handled", extra={"agent": "SHURI", "latency_ms": 41})
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Optional

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
principal_var: ContextVar[str] = ContextVar("principal", default="-")

_SENSITIVE = re.compile(r"(token|secret|password|authorization|api[_-]?key|cookie)", re.I)
# Defence in depth: even if a secret slips into a message or exception text, scrub it.
_SCRUB = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[JWT]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)(password|secret|token|api[_-]?key)(\s*[=:]\s*)[^\s,;&\"']{3,}"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\b(postgres(?:ql)?(?:\+\w+)?|redis|rediss)://([^:/@\s]+):[^@\s]+@"), r"\1://\2:[REDACTED]@"),
]


def scrub(text: str) -> str:
    for pattern, replacement in _SCRUB:
        text = pattern.sub(replacement, text)
    return text
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {
    "message", "asctime", "request_id", "service", "principal",
}


class _ContextFilter(logging.Filter):
    """Stamp each record with the current request id and the service name."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.principal = principal_var.get()
        record.service = self._service
        return True


def _extras(record: logging.LogRecord) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_ATTRS or key.startswith("_"):
            continue
        out[key] = "[REDACTED]" if _SENSITIVE.search(key) else (scrub(value) if isinstance(value, str) else value)
    return out


class JsonFormatter(logging.Formatter):
    """One JSON object per line; stable keys so log tooling can index them."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "-"),
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "principal": getattr(record, "principal", "-"),
            "msg": scrub(record.getMessage()),
        }
        payload.update(_extras(record))
        if record.exc_info:
            payload["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Readable single-line format with ``key=value`` extras appended."""

    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s %(levelname)-7s %(service)s %(name)s [%(request_id)s] %(message)s",
            datefmt="%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        line = scrub(super().format(record))
        extras = _extras(record)
        if extras:
            line += "  " + " ".join(f"{k}={v}" for k, v in extras.items())
        return line


def setup_logging(
    service: str,
    *,
    level: str = "INFO",
    fmt: str = "text",
    log_dir: Optional[Path] = None,
) -> None:
    """Configure the root logger. Idempotent: calling twice replaces the handlers."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level.upper())

    formatter: logging.Formatter = JsonFormatter() if fmt == "json" else TextFormatter()
    context = _ContextFilter(service)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(context)
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        # Files are always JSON: they are for machines and post-mortems.
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / f"{service}.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(context)
        root.addHandler(file_handler)

    # We emit our own per-request line; uvicorn's access log would duplicate it.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpcore", "apscheduler.scheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
