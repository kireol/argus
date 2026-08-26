"""Append-only trace: the source of truth for reproduction.

Events stream to ``trace.jsonl`` as they happen (one JSON object per line,
flushed every few events) while a bounded tail stays in memory for evidence
snapshots. A torn last line after a crash is ignored on read.
"""

from __future__ import annotations

import collections
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from argus.stress.models import TraceEvent, TraceEventType

FLUSH_EVERY = 10


class Trace:
    def __init__(self, path: Path | None, *, tail: int = 200) -> None:
        self.path = path
        self._tail: collections.deque[TraceEvent] = collections.deque(maxlen=tail)
        self._fh: Any = None
        self._lock = threading.Lock()
        self._sequence = 0
        self._since_flush = 0
        self.count = 0
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    @property
    def next_sequence(self) -> int:
        return self._sequence + 1

    def append(self, event_type: TraceEventType, *, elapsed: float, timestamp: str,
               **fields: Any) -> TraceEvent:
        with self._lock:
            self._sequence += 1
            event = TraceEvent(sequence=self._sequence, elapsed=elapsed, timestamp=timestamp,
                               event_type=event_type, **fields)
            self._tail.append(event)
            self.count += 1
            if self._fh is not None:
                self._fh.write(json.dumps(event.model_dump(mode="json", exclude_none=True),
                                          separators=(",", ":")) + "\n")
                self._since_flush += 1
                if self._since_flush >= FLUSH_EVERY:
                    self._fh.flush()
                    self._since_flush = 0
            return event

    def recent(self, n: int) -> list[TraceEvent]:
        with self._lock:
            items = list(self._tail)
        return items[-n:] if n < len(items) else items

    def flush(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.flush()
                self._fh.close()
                self._fh = None


def read_trace(path: Path) -> Iterator[TraceEvent]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue  # torn final line after a crash
            yield TraceEvent.model_validate(data)


def load_trace(path: Path) -> list[TraceEvent]:
    return list(read_trace(path))


__all__ = ["Trace", "load_trace", "read_trace"]
