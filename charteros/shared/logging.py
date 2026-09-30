from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from charteros.shared.config import Settings
from charteros.shared.context import correlation_id_context

_RESERVED_LOG_RECORD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)
_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "bearer",
    "credential",
    "database_url",
    "dsn",
    "password",
    "secret",
    "token",
)
_REDACTED = "[REDACTED]"


def _redact(key: str, value: Any) -> Any:
    normalized = key.casefold().replace("-", "_")
    if any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS):
        return _REDACTED
    if isinstance(value, Mapping):
        return {\n            str(child_key): _redact(str(child_key), child)\n            for child_key, child in value.items()\n        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact(key, item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        correlation_id = correlation_id_context.get()
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": self._service_name,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": correlation_id,
            "correlation_id": correlation_id,
            "organization_id": getattr(record, "organization_id", None),
            "aggregate_id": getattr(record, "aggregate_id", None),
            "event": getattr(record, "event", record.getMessage()),
        }

        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_FIELDS and key not in payload:
                payload[key] = _redact(key, value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service_name=settings.service_name))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    logging.captureWarnings(True)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
