"""Structured JSON logging to stdout.

Use :func:`log_event` so every line has an ``event`` name plus flat key/value fields. Never pass
passwords or the API key value as a field.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import UTC, datetime
from typing import Any

_FIELDS = "fields"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, _FIELDS, None)
        if isinstance(fields, dict):
            entry.update(fields)
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_type"] = record.exc_info[0].__name__
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Send every log record to stdout as one JSON object per line (flushed per record)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper() if isinstance(level, str) else level)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        log_event(
            logging.getLogger("db_app"),
            logging.ERROR,
            "thread_crashed",
            thread=args.thread.name if args.thread else None,
            exc_type=args.exc_type.__name__,
        )

    # Uncaught thread exceptions would otherwise print a traceback to stderr.
    threading.excepthook = thread_hook


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, event, extra={_FIELDS: fields})
