"""Opaque, deterministic cursors for paginated tool results."""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass

from argus.mcp.errors import InvalidArgumentError


@dataclass
class Page[T]:
    items: list[T]
    total: int
    truncated: bool
    next_cursor: str | None


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        offset = int(data["o"])
    except Exception as exc:  # noqa: BLE001 - any malformed cursor is the caller's error
        raise InvalidArgumentError(
            "Invalid pagination cursor.",
            remediation="Pass the next_cursor value from the previous response unchanged.",
        ) from exc
    if offset < 0:
        raise InvalidArgumentError("Invalid pagination cursor.")
    return offset


def paginate[T](items: Sequence[T], *, cursor: str | None, limit: int) -> Page[T]:
    """Slice a fully ordered sequence; ordering is the caller's responsibility."""
    offset = decode_cursor(cursor)
    window = list(items[offset : offset + limit])
    end = offset + len(window)
    truncated = end < len(items)
    return Page(
        items=window,
        total=len(items),
        truncated=truncated,
        next_cursor=encode_cursor(end) if truncated else None,
    )
