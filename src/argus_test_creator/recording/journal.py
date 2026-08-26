"""SessionJournal — crash-safe recording storage.

::

    sessions/<id>/
    ├── session.json      # snapshot (atomic write, periodically refreshed)
    ├── events.jsonl      # append-only raw events (one JSON per line, fsync'd)
    ├── actions.jsonl     # normalized actions as they are produced
    └── screenshots/      # PNG captures + sidecar metadata + thumbnails

Recovery replays ``events.jsonl`` line by line and ignores a torn final line.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from argus_test_creator.core.paths import atomic_write_json
from argus_test_creator.models.recording import NormalizedAction, RecordingEvent

CHECKPOINT_EVERY = 25


class SessionJournal:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.screenshots_dir = directory / "screenshots"
        self._events_path = directory / "events.jsonl"
        self._actions_path = directory / "actions.jsonl"
        self._snapshot_path = directory / "session.json"
        self._lock = threading.Lock()
        self._events_fh: Any = None
        self._actions_fh: Any = None
        self._since_checkpoint = 0

    def open(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._events_fh = self._events_path.open("a", encoding="utf-8")
        self._actions_fh = self._actions_path.open("a", encoding="utf-8")

    def close(self) -> None:
        with self._lock:
            for fh in (self._events_fh, self._actions_fh):
                if fh is not None:
                    fh.flush()
                    os.fsync(fh.fileno())
                    fh.close()
            self._events_fh = self._actions_fh = None

    @property
    def is_open(self) -> bool:
        return self._events_fh is not None

    def append_event(self, event: RecordingEvent) -> None:
        self._append(self._events_fh, event.model_dump(mode="json"))

    def append_action(self, action: NormalizedAction) -> None:
        self._append(self._actions_fh, action.model_dump(mode="json"))

    def _append(self, fh: Any, payload: dict[str, Any]) -> None:
        if fh is None:
            return
        line = json.dumps(payload, separators=(",", ":"))
        with self._lock:
            fh.write(line + "\n")
            self._since_checkpoint += 1
            if self._since_checkpoint >= CHECKPOINT_EVERY:
                fh.flush()
                os.fsync(fh.fileno())
                self._since_checkpoint = 0

    def write_snapshot(self, snapshot: dict[str, Any]) -> None:
        snapshot = {**snapshot, "checkpoint_at": datetime.now(UTC).isoformat()}
        atomic_write_json(self._snapshot_path, snapshot)

    def read_snapshot(self) -> dict[str, Any] | None:
        if not self._snapshot_path.is_file():
            return None
        try:
            return json.loads(self._snapshot_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def read_events(self) -> list[RecordingEvent]:
        return [RecordingEvent.model_validate(d) for d in _read_jsonl(self._events_path)]

    def read_actions(self) -> list[NormalizedAction]:
        return [NormalizedAction.model_validate(d) for d in _read_jsonl(self._actions_path)]

    @staticmethod
    def list_sessions(sessions_dir: Path) -> list[Path]:
        if not sessions_dir.is_dir():
            return []
        return sorted(p for p in sessions_dir.iterdir() if (p / "session.json").is_file())

    @staticmethod
    def recoverable(sessions_dir: Path) -> list[Path]:
        """Sessions whose snapshot says they never finished cleanly."""
        out: list[Path] = []
        for path in SessionJournal.list_sessions(sessions_dir):
            snapshot = SessionJournal(path).read_snapshot()
            if snapshot and snapshot.get("state") not in ("stopped", "discarded"):
                out.append(path)
        return out


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except ValueError:
                # A torn final line after a crash — ignore it.
                continue
    return items
