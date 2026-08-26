"""Structured logging on top of the standard library.

Every log record can carry structured context (test id, feature, platform,
device, action). Output is either human-readable text or JSON lines.
Secrets are redacted before they reach any handler.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CONTEXT_FIELDS = (
    "test_id",
    "test_name",
    "feature",
    "platform",
    "device",
    "action",
    "run_id",
    "operation",
    "tool",
)

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\n]+)"),
    re.compile(r"(?i)(bearer\s+)(\S+)"),
    re.compile(r"(?i)((?:token|password|secret|api[_-]?key|private[_-]?key)\s*[:=]\s*)(\S+)"),
]


def redact(text: str) -> str:
    """Redact obvious secrets from a string before logging or persisting."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


class _TextFormatter(logging.Formatter):
    # Indent per-test log lines so Rich ``→ N/M - …`` start markers stand out.
    _TEST_INDENT = "    "

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        context = _record_context(record)
        if context:
            pairs = " ".join(f"{k}={v}" for k, v in context.items())
            base = f"{base} [{pairs}]"
        # Lines tied to a running test (shell.run, etc.) sit under the start marker.
        if getattr(record, "test_id", None) is not None:
            base = self._TEST_INDENT + base
        return base


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_record_context(record))
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _record_context(record: logging.LogRecord) -> dict[str, Any]:
    return {
        field: getattr(record, field)
        for field in _CONTEXT_FIELDS
        if getattr(record, field, None) is not None
    }


class ContextLogger(logging.LoggerAdapter):
    """Logger adapter that attaches structured context to every record."""

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        extra = kwargs.setdefault("extra", {})
        for key, value in (self.extra or {}).items():
            extra.setdefault(key, value)
        return msg, kwargs

    def bind(self, **context: Any) -> ContextLogger:
        merged = {**(self.extra or {}), **context}
        return ContextLogger(self.logger, merged)


def get_logger(name: str, **context: Any) -> ContextLogger:
    return ContextLogger(logging.getLogger(name), context)


def configure_logging(
    level: str = "INFO",
    fmt: str = "text",
    log_file: str | Path | None = None,
) -> None:
    """Configure the ``argus`` logger hierarchy. Safe to call repeatedly."""
    root = logging.getLogger("argus")
    root.setLevel(level.upper())
    root.handlers.clear()
    root.propagate = False

    formatter: logging.Formatter
    if fmt == "json":
        formatter = _JsonFormatter()
    else:
        formatter = _TextFormatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    handler: logging.Handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    handler.addFilter(_RedactingFilter())
    root.addHandler(handler)

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(_JsonFormatter())
        file_handler.addFilter(_RedactingFilter())
        root.addHandler(file_handler)
