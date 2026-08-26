"""Command-based undo/redo.

Every mutation of an authoring document goes through a :class:`Command` with
explicit ``apply``/``revert``; the :class:`CommandStack` records history.
Commands are small and store only what they need to revert (not whole
document snapshots), so a 10,000-step undo history stays cheap.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class Command(ABC, Generic[T]):
    """A reversible mutation of a target of type ``T``."""

    #: Human-readable label for menus ("Undo Add step").
    label: str = "command"

    @abstractmethod
    def apply(self, target: T) -> None: ...

    @abstractmethod
    def revert(self, target: T) -> None: ...

    def merge_with(self, previous: Command[T]) -> bool:
        """Fold this command into ``previous`` (e.g. consecutive text edits).

        Return True when merged; the stack then drops this command.
        """
        return False


class CommandStack(Generic[T]):
    def __init__(self, target: T, *, limit: int = 10_000) -> None:
        self._target = target
        self._limit = limit
        self._undo: list[Command[T]] = []
        self._redo: list[Command[T]] = []
        self.revision = 0
        self._clean_revision = 0

    @property
    def target(self) -> T:
        return self._target

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    @property
    def dirty(self) -> bool:
        return self.revision != self._clean_revision

    def mark_clean(self) -> None:
        self._clean_revision = self.revision

    def execute(self, command: Command[T]) -> None:
        command.apply(self._target)
        self._redo.clear()
        if self._undo and command.merge_with(self._undo[-1]):
            pass
        else:
            self._undo.append(command)
            if len(self._undo) > self._limit:
                del self._undo[0]
        self.revision += 1

    def undo(self) -> Command[T] | None:
        if not self._undo:
            return None
        command = self._undo.pop()
        command.revert(self._target)
        self._redo.append(command)
        self.revision += 1
        return command

    def redo(self) -> Command[T] | None:
        if not self._redo:
            return None
        command = self._redo.pop()
        command.apply(self._target)
        self._undo.append(command)
        self.revision += 1
        return command

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
