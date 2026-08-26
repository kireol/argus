"""Centralized background execution.

All expensive work (OCR, image analysis, screenshots, device I/O, persistence)
runs through :class:`WorkerPool`. Each submission returns a :class:`Job` with
cancellation, timeout, and exception propagation. Nothing else in the code
base creates threads ad hoc.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from argus_test_creator.core.errors import WorkerError

T = TypeVar("T")


class CancellationToken:
    """Cooperative cancellation shared between a job and the code it runs."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise WorkerError("Operation cancelled.")


@dataclass
class Job(Generic[T]):
    name: str
    future: Future[T]
    token: CancellationToken

    def cancel(self) -> None:
        self.token.cancel()
        self.future.cancel()

    @property
    def done(self) -> bool:
        return self.future.done()

    @property
    def cancelled(self) -> bool:
        return self.token.cancelled or self.future.cancelled()

    def result(self, timeout: float | None = None) -> T:
        try:
            return self.future.result(timeout=timeout)
        except FutureTimeout as exc:
            raise WorkerError(
                f"{self.name} timed out after {timeout}s.",
                remediation="Retry, or increase the timeout in settings.",
            ) from exc

    def on_done(self, callback: Callable[[Job[T]], None]) -> None:
        self.future.add_done_callback(lambda _f: callback(self))


def _accepts_token(fn: Callable[..., Any]) -> bool:
    try:
        return "token" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


class WorkerPool:
    """A named thread pool with lifecycle management and cooperative cancellation."""

    def __init__(self, name: str = "creator", max_workers: int = 4) -> None:
        self._name = name
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=name)
        self._jobs: list[Job[Any]] = []
        self._lock = threading.Lock()
        self._closed = False

    def submit(
        self,
        name: str,
        fn: Callable[..., T],
        *args: Any,
        token: CancellationToken | None = None,
        **kwargs: Any,
    ) -> Job[T]:
        """Run ``fn(*args, **kwargs)`` in the background.

        ``fn`` may accept a ``token`` keyword to poll for cancellation.
        """
        if self._closed:
            raise WorkerError(f"Worker pool {self._name!r} is shut down.")
        token = token or CancellationToken()
        if _accepts_token(fn) and "token" not in kwargs:
            kwargs["token"] = token
        future: Future[T] = self._executor.submit(fn, *args, **kwargs)
        job = Job(name=name, future=future, token=token)
        with self._lock:
            self._jobs = [j for j in self._jobs if not j.done]
            self._jobs.append(job)
        return job

    def active_jobs(self) -> list[Job[Any]]:
        with self._lock:
            return [j for j in self._jobs if not j.done]

    def cancel_all(self) -> None:
        for job in self.active_jobs():
            job.cancel()

    def shutdown(self, *, wait: bool = True, timeout: float | None = 10.0) -> None:
        """Cancel outstanding work and stop the pool. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        self.cancel_all()
        self._executor.shutdown(wait=False, cancel_futures=True)
        if wait:
            deadline_jobs = self.active_jobs()
            for job in deadline_jobs:
                try:
                    job.future.result(timeout=timeout)
                except Exception:  # noqa: BLE001 — shutting down; failures are irrelevant
                    pass

    def __enter__(self) -> WorkerPool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
