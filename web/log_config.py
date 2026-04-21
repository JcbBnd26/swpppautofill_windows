"""Shared logging configuration for the Tools platform."""

from __future__ import annotations

import json
import logging
import os
import traceback


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON object on one line.

    Fields: timestamp, level, logger, message, (exc_info if present).
    Extra keyword arguments passed to log calls are included as top-level keys.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Include any extra fields attached to the record
        skip = logging.LogRecord.__dict__.keys() | {
            "message",
            "asctime",
            "exc_text",
            "stack_info",
        }
        for k, v in record.__dict__.items():
            if k not in skip and not k.startswith("_"):
                try:
                    json.dumps(v)  # only include JSON-serializable extras
                    payload[k] = v
                except (TypeError, ValueError):
                    payload[k] = str(v)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger with JSON output.

    Call once at service startup, before any log calls.
    Safe to call multiple times — subsequent calls are no-ops if handlers exist.
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g., by test framework)
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
