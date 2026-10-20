"""Structured logging with a per-update correlation ID and secret redaction."""

import logging
import re
import sys
import uuid
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Bot tokens look like "123456:ABC..." and appear in API URLs; never let them reach logs.
_TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")
_SECRET_KEYS = {"token", "bot_token", "password", "secret", "authorization", "database_url", "shaba_number"}


def new_correlation_id() -> str:
    cid = uuid.uuid4().hex[:12]
    correlation_id_var.set(cid)
    return cid


def _add_correlation_id(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    cid = correlation_id_var.get()
    if cid:
        event.setdefault("correlation_id", cid)
    return event


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _TOKEN_RE.sub("<redacted-token>", value)
    return value


def _redact_secrets(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event.keys()):
        if key.lower() in _SECRET_KEYS:
            event[key] = "<redacted>"
        else:
            event[key] = redact(event[key])
    return event


class _RedactingFilter(logging.Filter):
    """Redacts tokens in stdlib log records (aiogram and httpx log full URLs)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(a) for a in record.args) if isinstance(record.args, tuple) else record.args
        return True


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _add_correlation_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_secrets,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    handler.addFilter(_RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("aiogram.event", "httpx", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
