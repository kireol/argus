"""Background sampler that records device metrics while a test runs."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from argus.logging import get_logger
from argus.models.metrics import MetricSample, MetricsReport, build_report, unit_for

if TYPE_CHECKING:
    from argus.adapters.base import Device


class MetricsSampler:
    """Daemon thread that calls ``device.sample_metrics()`` on an interval."""

    def __init__(self, device: Device, *, interval_seconds: float = 1.0) -> None:
        self._device = device
        self._interval = max(0.2, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._series: dict[str, list[float]] = {}
        self._samples: list[MetricSample] = []
        self._started = 0.0
        self._log = get_logger("argus.metrics")

    def start(self) -> None:
        try:
            self._device.begin_metrics_session()
        except Exception:  # noqa: BLE001 - sampling must never fail the test
            self._log.debug("begin_metrics_session failed", exc_info=True)
        self._started = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="argus-metrics", daemon=True
        )
        self._thread.start()

    def stop(self) -> MetricsReport | None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(2.0, self._interval + 1.0))
        self._thread = None
        with self._lock:
            series = {name: list(values) for name, values in self._series.items()}
            samples = list(self._samples)
        units = {name: unit_for(name) for name in series}
        return build_report(
            series, interval_seconds=self._interval, units=units, samples=samples
        )

    def _run(self) -> None:
        self._sample()
        while not self._stop.wait(self._interval):
            self._sample()

    def _sample(self) -> None:
        try:
            snapshot = self._device.sample_metrics() or {}
        except Exception:  # noqa: BLE001 - one bad sample must not stop collection
            self._log.debug("sample_metrics failed", exc_info=True)
            return
        parsed: dict[str, float] = {}
        for name, raw in snapshot.items():
            try:
                parsed[str(name)] = float(raw)
            except (TypeError, ValueError):
                continue
        if not parsed:
            return
        elapsed = round(max(0.0, time.monotonic() - self._started), 3)
        with self._lock:
            self._samples.append(MetricSample(t=elapsed, values=parsed))
            for name, value in parsed.items():
                self._series.setdefault(name, []).append(value)
