"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import re
from typing import Any

_SECRET_RE = re.compile(
    r"(?i)(token|password|passwd|secret|api[_-]?key|authorization|cookie)"
    r"(\s*[=:]\s*)(\S+)"
)


def redact(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str = "INFO", *, diagnostic: bool = False) -> None:
    root = logging.getLogger("argus_test_creator")
    root.setLevel("DEBUG" if diagnostic else level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            _RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)


def get_logger(name: str, **context: Any) -> logging.LoggerAdapter[logging.Logger]:
    return logging.LoggerAdapter(logging.getLogger(f"argus_test_creator.{name}"), context)
