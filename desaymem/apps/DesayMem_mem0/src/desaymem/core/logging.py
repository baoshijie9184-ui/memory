"""Logging helpers that redact secrets and conversation payloads."""

from __future__ import annotations

import logging
import re
from typing import Any

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "llm_api_key",
    "embedding_api_key",
    "password",
    "postgres_dsn",
    "dsn",
    "token",
    "secret",
    "authorization",
    "client_secret",
}

_SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key\s*[=:]\s*)([^\s,;]+)", re.IGNORECASE),
    re.compile(r"(password\s*[=:]\s*)([^\s,;]+)", re.IGNORECASE),
    re.compile(r"(bearer\s+)([A-Za-z0-9._\-]+)", re.IGNORECASE),
    re.compile(r"(postgresql://[^:]+:)([^@]+)(@)", re.IGNORECASE),
]


def redact_value(key: str, value: Any) -> Any:
    if str(key).lower() in _SECRET_KEYS:
        return "***"
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 3:
            redacted = pattern.sub(r"\1***\3", redacted)
        else:
            redacted = pattern.sub(r"\1***", redacted)
    return redacted


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    return {key: redact_value(key, value) for key, value in data.items()}


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_mapping(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_text(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        root.addHandler(handler)
    for handler in root.handlers:
        handler.addFilter(RedactingFilter())
    logging.getLogger("desaymem").addFilter(RedactingFilter())


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.addFilter(RedactingFilter())
    return logger
