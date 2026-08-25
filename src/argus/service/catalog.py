"""Cached test discovery.

Loading a suite means scanning the filesystem and validating every YAML file.
Interfaces that serve many small requests (MCP, a GUI) would otherwise repeat
that work on every call. The catalog caches the loaded definitions and
invalidates the cache whenever the set of files, or any file's size/mtime,
changes — so edits made while the server runs are picked up on the next call
without a restart.
"""

from __future__ import annotations

import threading
from pathlib import Path

from argus.config.models import AppConfig
from argus.engine.filters import TestFilter
from argus.engine.loader import discover_test_files, load_tests
from argus.models.test_definition import TestDefinition

_Signature = tuple[tuple[str, int, int], ...]


class TestCatalog:
    """Thread-safe, self-invalidating view of the configured test suites."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._tests: list[TestDefinition] | None = None
        self._signature: _Signature | None = None

    @property
    def paths(self) -> list[Path]:
        return [self._config.resolve_path(p) for p in self._config.test_paths]

    def _current_signature(self) -> _Signature:
        entries: list[tuple[str, int, int]] = []
        for file in discover_test_files(self.paths):
            try:
                stat = file.stat()
            except OSError:
                entries.append((str(file), -1, -1))
                continue
            entries.append((str(file), stat.st_mtime_ns, stat.st_size))
        return tuple(entries)

    def load(self) -> list[TestDefinition]:
        """All test definitions (cached until a suite file changes).

        Raises :class:`argus.exceptions.TestDefinitionError` exactly as the
        loader does; a broken suite is never served partially.
        """
        signature = self._current_signature()
        with self._lock:
            if self._tests is not None and signature == self._signature:
                return list(self._tests)
            tests = load_tests(self.paths)
            self._tests = tests
            self._signature = signature
            return list(tests)

    def select(self, filters: TestFilter | None = None) -> list[TestDefinition]:
        tests = self.load()
        return (filters or TestFilter()).apply(tests)

    def get(self, test_id: str) -> TestDefinition | None:
        return next((t for t in self.load() if t.id == test_id), None)

    def invalidate(self) -> None:
        with self._lock:
            self._tests = None
            self._signature = None
