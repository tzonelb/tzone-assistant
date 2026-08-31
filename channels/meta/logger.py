"""Channel event logging.

Two rules this module exists to enforce:

* Logging never breaks message delivery. A failure to write a log line used to
  raise out of the webhook and return 500, which makes Meta retry and then
  disable the subscription — an unwritable log file could take the whole
  integration offline.
* Logs do not store customer message bodies or access tokens. Log files are
  rarely encrypted, get copied into backups and pasted into tickets, so they
  hold identifiers and outcomes only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

from config.settings import config


MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

# Values under these keys are replaced before anything is written.
_SENSITIVE_KEYS = frozenset(
    {
        "text",
        "body",
        "message",
        "reply",
        "access_token",
        "app_secret",
        "verify_token",
        "password",
        "authorization",
        "messages",
        "note",
    }
)

_MAX_DEPTH = 6

_logger: logging.Logger | None = None


def _build_logger() -> logging.Logger:
    global _logger

    if _logger is not None:
        return _logger

    logger = logging.getLogger("tzone.channels")
    logger.setLevel(config.LOG_LEVEL)
    logger.propagate = False

    if not logger.handlers:
        try:
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = RotatingFileHandler(
                config.LOG_DIR / "channels.log",
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
        except OSError:
            # No writable log directory is a reason to log to stderr, never a
            # reason to stop accepting customer messages.
            handler = logging.StreamHandler()

        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)

    _logger = logger
    return logger


def redact(value: Any, depth: int = 0) -> Any:
    """Strip message content and secrets out of a structure before logging."""
    if depth > _MAX_DEPTH:
        return "<max-depth>"

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}

        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                if isinstance(item, str):
                    cleaned[key] = f"<redacted {len(item)} chars>"
                elif isinstance(item, (list, tuple)):
                    cleaned[key] = f"<redacted {len(item)} items>"
                else:
                    cleaned[key] = "<redacted>"
            else:
                cleaned[key] = redact(item, depth + 1)

        return cleaned

    if isinstance(value, (list, tuple)):
        return [redact(item, depth + 1) for item in value[:20]]

    return value


def log_meta_event(event_type: str, data: dict[str, Any] | None = None) -> None:
    """Record one channel event. Never raises."""
    try:
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "data": redact(data or {}),
        }
        _build_logger().info(json.dumps(record, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 - logging must not break delivery
        pass
