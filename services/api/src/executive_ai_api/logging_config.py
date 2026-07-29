from __future__ import annotations

import contextvars
import json
import logging
from datetime import UTC, datetime

request_id_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class JsonFormatter(logging.Formatter):
    REDACTED_KEYS = {"password", "token", "authorization", "cookie", "secret", "csrf"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
        }
        fields = getattr(record, "structured", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                payload[key] = "[REDACTED]" if key.lower() in self.REDACTED_KEYS else value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
