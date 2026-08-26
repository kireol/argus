"""Deterministic randomness for stress runs.

Every random decision in a stress run derives from one :class:`DeterministicRNG`
owned by the :class:`~argus.stress.context.StressContext`. Nothing in the
package touches the global ``random`` module. Sub-generators (``fork``) let
independent components (action choice, data mutation, timing) draw from
isolated streams so adding a decision in one component does not shift the
sequence of another — the property that keeps replays stable across versions.
"""

from __future__ import annotations

import hashlib
import random
import secrets
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")

#: Seeds are 32-bit so they fit comfortably on a command line and in reports.
SEED_MAX = 2**31 - 1


def new_seed() -> int:
    """A fresh random seed (the only place a stress run may use OS entropy)."""
    return secrets.randbelow(SEED_MAX) + 1


class DeterministicRNG:
    """A seeded, forkable random source with weighted selection."""

    def __init__(self, seed: int, *, stream: str = "root") -> None:
        self.seed = int(seed)
        self.stream = stream
        self._random = random.Random(_derive(seed, stream))
        self._draws = 0

    @property
    def draws(self) -> int:
        """How many values were drawn (diagnostics / determinism tests)."""
        return self._draws

    def fork(self, label: str) -> DeterministicRNG:
        """An independent stream derived from this seed and ``label``."""
        return DeterministicRNG(self.seed, stream=f"{self.stream}/{label}")

    # -- primitives ------------------------------------------------------------------

    def random(self) -> float:
        self._draws += 1
        return self._random.random()

    def uniform(self, low: float, high: float) -> float:
        self._draws += 1
        return self._random.uniform(low, high)

    def randint(self, low: int, high: int) -> int:
        self._draws += 1
        return self._random.randint(low, high)

    def chance(self, probability: float) -> bool:
        """True with the given probability (0 → never, 1 → always)."""
        if probability <= 0:
            return False
        if probability >= 1:
            return True
        return self.random() < probability

    def choice(self, items: Sequence[T]) -> T:
        if not items:
            raise ValueError("choice() from an empty sequence")
        self._draws += 1
        return self._random.choice(items)

    def weighted_choice(self, items: Sequence[T], weights: Sequence[float]) -> T:
        if not items:
            raise ValueError("weighted_choice() from an empty sequence")
        if len(items) != len(weights):
            raise ValueError("items and weights must have the same length")
        if any(w < 0 for w in weights):
            raise ValueError("weights must be non-negative")
        total = float(sum(weights))
        if total <= 0:
            return self.choice(items)
        self._draws += 1
        point = self._random.random() * total
        cumulative = 0.0
        for item, weight in zip(items, weights, strict=True):
            cumulative += weight
            if point < cumulative:
                return item
        return items[-1]

    def shuffle(self, items: list[T]) -> list[T]:
        self._draws += 1
        copy = list(items)
        self._random.shuffle(copy)
        return copy

    def sample(self, items: Sequence[T], k: int) -> list[T]:
        self._draws += 1
        return self._random.sample(list(items), min(k, len(items)))

    def token(self, length: int = 8, alphabet: str = "abcdefghijklmnopqrstuvwxyz0123456789") -> str:
        return "".join(self.choice(alphabet) for _ in range(length))

    def getstate(self) -> tuple[object, ...]:
        return self._random.getstate()

    def setstate(self, state: tuple[object, ...]) -> None:
        self._random.setstate(state)


def _derive(seed: int, stream: str) -> int:
    digest = hashlib.sha256(f"{seed}:{stream}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


__all__ = ["SEED_MAX", "DeterministicRNG", "new_seed"]
