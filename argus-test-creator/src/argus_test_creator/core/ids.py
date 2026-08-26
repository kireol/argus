"""Identifier generation (short, sortable, collision-safe within a document)."""

from __future__ import annotations

import secrets
import time


def new_id(prefix: str = "") -> str:
    """Return a time-ordered unique id such as ``step_1f3a9c2b7e``.

    The millisecond timestamp keeps ids sortable in journals; the random
    suffix keeps ids unique across processes.
    """
    stamp = format(int(time.time() * 1000), "x")
    rand = secrets.token_hex(3)
    return f"{prefix}{'_' if prefix else ''}{stamp}{rand}"
