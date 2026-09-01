"""In-run performance metrics collected from the app under test."""

from __future__ import annotations

import math
import statistics
from typing import Any

from pydantic import BaseModel, Field

# Preferred display order; unknown names follow alphabetically.
METRIC_ORDER = (
    "fps",
    "app_fps",
    "jank_percent",
    "missed_vsync",
    "slow_ui_frames",
    "deadline_missed",
    "frame_p50_ms",
    "frame_p90_ms",
    "frame_p95_ms",
    "frame_p99_ms",
    "app_rss_mb",
    "app_vsize_mb",
    "app_cpu_percent",
    "app_threads",
    "app_uptime_s",
    "system_uptime_s",
    "system_load_1m",
    "system_load_5m",
    "system_load_15m",
    "system_mem_available_mb",
    "system_mem_used_percent",
)

METRIC_UNITS: dict[str, str] = {
    "fps": "fps",
    "app_fps": "fps",
    "jank_percent": "%",
    "missed_vsync": "count",
    "slow_ui_frames": "count",
    "deadline_missed": "count",
    "frame_p50_ms": "ms",
    "frame_p90_ms": "ms",
    "frame_p95_ms": "ms",
    "frame_p99_ms": "ms",
    "app_rss_mb": "MiB",
    "app_vsize_mb": "MiB",
    "app_cpu_percent": "%",
    "app_threads": "count",
    "app_uptime_s": "s",
    "system_uptime_s": "s",
    "system_load_1m": "",
    "system_load_5m": "",
    "system_load_15m": "",
    "system_mem_available_mb": "MiB",
    "system_mem_used_percent": "%",
}

METRIC_LABELS: dict[str, str] = {
    "fps": "FPS",
    "app_fps": "App FPS",
    "jank_percent": "Jank",
    "missed_vsync": "Missed vsync",
    "slow_ui_frames": "Slow UI frames",
    "deadline_missed": "Deadline missed",
    "frame_p50_ms": "Frame p50",
    "frame_p90_ms": "Frame p90",
    "frame_p95_ms": "Frame p95",
    "frame_p99_ms": "Frame p99",
    "app_rss_mb": "App RSS",
    "app_vsize_mb": "App virtual size",
    "app_cpu_percent": "App CPU",
    "app_threads": "App threads",
    "app_uptime_s": "App uptime",
    "system_uptime_s": "System uptime",
    "system_load_1m": "Load 1m",
    "system_load_5m": "Load 5m",
    "system_load_15m": "Load 15m",
    "system_mem_available_mb": "Mem available",
    "system_mem_used_percent": "Mem used",
}

# Plain-language meaning of each metric, shown as a hover tooltip in report.html.
# Every metric is a snapshot per sample interval (metrics.interval, default 1s);
# min / max / average / median summarize those samples over one test.
METRIC_DESCRIPTIONS: dict[str, str] = {
    "fps": (
        "Display refresh rate the app is rendering against, from the compositor's vsync "
        "period (Android: dumpsys SurfaceFlinger --latency). Usually a constant such as 60 "
        "or 120; a drop means the display itself changed mode. It is not how often the app "
        "drew — see App FPS for that."
    ),
    "app_fps": (
        "Frames the app actually produced per second: unique frames in the last second of "
        "the renderer's profile data (Android: dumpsys gfxinfo). An idle app that only "
        "redraws when something changes legitimately reads low (1–5); watch it during "
        "animations, scrolling and video, where it should approach FPS."
    ),
    "jank_percent": (
        "Share of frames drawn during this sample interval that the renderer flagged as "
        "janky, i.e. took longer than one vsync period and so were displayed late or "
        "dropped. 0% is smooth; sustained values above ~10% are visible stutter."
    ),
    "missed_vsync": (
        "Number of frames in this sample interval that missed their vsync deadline "
        "because the app started drawing too late (the UI thread was busy). A cause of "
        "jank; each one is a frame the user waited an extra refresh for."
    ),
    "slow_ui_frames": (
        "Frames in this sample interval whose UI-thread work (measure/layout/draw, input "
        "handling) exceeded its budget. Points at expensive layouts, main-thread I/O or "
        "heavy view hierarchies rather than GPU cost."
    ),
    "deadline_missed": (
        "Frames in this sample interval that were not ready when the display needed "
        "them, counted over the whole pipeline (UI thread plus render thread/GPU). Any "
        "value above 0 means at least one visibly dropped or repeated frame."
    ),
    "frame_p50_ms": (
        "Median time to produce one frame (50th percentile) across the renderer's recent "
        "frame history. At 60 Hz the budget is 16.7 ms, at 120 Hz 8.3 ms; a p50 near or "
        "above the budget means most frames are late."
    ),
    "frame_p90_ms": (
        "Frame time that 90% of recent frames stay under; the remaining 10% were slower. "
        "Shows the typical worst case a user sees every second or two."
    ),
    "frame_p95_ms": (
        "Frame time that 95% of recent frames stay under. A p95 well above the vsync "
        "budget (16.7 ms at 60 Hz) means regular, noticeable hitches."
    ),
    "frame_p99_ms": (
        "Frame time that 99% of recent frames stay under — the slowest 1%, usually the "
        "long stalls (GC pauses, big layouts, image decodes) that users notice most."
    ),
    "app_rss_mb": (
        "Resident set size: physical RAM the app process is currently using, in MiB "
        "(shared libraries included). A steady climb across samples suggests a leak; the "
        "max shows the test's peak footprint."
    ),
    "app_vsize_mb": (
        "Virtual address space the app process has mapped, in MiB. Much larger than RSS "
        "and mostly not backed by RAM; only its growth over time is meaningful."
    ),
    "app_cpu_percent": (
        "CPU time the app process consumed during this sample interval, as a percentage "
        "of one core (user + kernel time). Can exceed 100% on multi-core devices when "
        "several threads are busy at once."
    ),
    "app_threads": (
        "Number of threads in the app process. Thread pools grow under load; a count "
        "that keeps rising without falling back suggests threads are leaked."
    ),
    "app_uptime_s": (
        "Seconds since the app process started. Drops back toward 0 when the app "
        "restarted, crashed and relaunched, or was killed and recreated during the test."
    ),
    "system_uptime_s": (
        "Seconds since the device booted. A reset to near 0 mid-run means the device "
        "rebooted."
    ),
    "system_load_1m": (
        "System load average over the last minute: runnable plus (on Linux) "
        "uninterruptible-I/O tasks. Compare with the number of CPU cores — equal to the "
        "core count is fully busy, above it is oversubscribed."
    ),
    "system_load_5m": (
        "System load average over the last 5 minutes; smoother than the 1-minute value "
        "and useful for spotting a device that was already busy before the test started."
    ),
    "system_load_15m": (
        "System load average over the last 15 minutes — the long-term baseline. Rising "
        "1m over a low 15m means load started during this run."
    ),
    "system_mem_available_mb": (
        "RAM the system estimates can be given to new work without swapping, in MiB "
        "(free memory plus reclaimable caches). Low values put the app at risk of being "
        "killed by the low-memory killer."
    ),
    "system_mem_used_percent": (
        "Percentage of total RAM in use across the device (100% minus available), i.e. "
        "memory pressure rather than the app's own footprint."
    ),
}


class MetricSummary(BaseModel):
    """Aggregates for one named series: min, max, average, and median."""

    unit: str = ""
    count: int
    min: float
    max: float
    average: float
    median: float


class MetricSample(BaseModel):
    """One snapshot taken while the test was running (``t`` is seconds from start)."""

    t: float
    values: dict[str, float] = Field(default_factory=dict)


class MetricsReport(BaseModel):
    """All series collected during one test (or rolled up for a run)."""

    sample_count: int = 0
    interval_seconds: float = 1.0
    metrics: dict[str, MetricSummary] = Field(default_factory=dict)
    series: dict[str, list[float]] = Field(default_factory=dict)
    #: Chronological snapshots so reports can show values as they occurred.
    samples: list[MetricSample] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.metrics


def unit_for(name: str) -> str:
    return METRIC_UNITS.get(name, "")


def label_for(name: str) -> str:
    return METRIC_LABELS.get(name, name.replace("_", " "))


def description_for(name: str) -> str:
    """Hover/help text for a metric; empty for unknown (custom) metric names."""
    return METRIC_DESCRIPTIONS.get(name, "")


def ordered_metric_names(names: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    known = [name for name in METRIC_ORDER if name in names]
    extra = sorted(name for name in names if name not in METRIC_ORDER)
    return known + extra


def summarize_series(values: list[float], *, unit: str = "") -> MetricSummary | None:
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return None
    ordered = sorted(finite)
    return MetricSummary(
        unit=unit,
        count=len(finite),
        min=ordered[0],
        max=ordered[-1],
        average=statistics.fmean(finite),
        median=statistics.median(finite),
    )


def build_report(
    series: dict[str, list[float]],
    *,
    interval_seconds: float,
    units: dict[str, str] | None = None,
    samples: list[MetricSample] | None = None,
) -> MetricsReport | None:
    summaries: dict[str, MetricSummary] = {}
    kept: dict[str, list[float]] = {}
    for name, values in series.items():
        unit = (units or {}).get(name) or unit_for(name)
        summary = summarize_series(values, unit=unit)
        if summary is None:
            continue
        summaries[name] = summary
        kept[name] = list(values)
    if not summaries:
        return None
    sample_count = max(len(samples or []), max((len(v) for v in kept.values()), default=0))
    return MetricsReport(
        sample_count=sample_count,
        interval_seconds=interval_seconds,
        metrics={name: summaries[name] for name in ordered_metric_names(summaries)},
        series={name: kept[name] for name in ordered_metric_names(kept)},
        samples=list(samples or []),
    )


def merge_metrics_reports(reports: list[MetricsReport]) -> MetricsReport | None:
    """Concatenate per-test series into one run-level report."""
    if not reports:
        return None
    combined: dict[str, list[float]] = {}
    units: dict[str, str] = {}
    for report in reports:
        for name, values in report.series.items():
            combined.setdefault(name, []).extend(values)
        for name, summary in report.metrics.items():
            units.setdefault(name, summary.unit)
    interval = reports[0].interval_seconds if reports else 1.0
    return build_report(combined, interval_seconds=interval, units=units)


def format_metric_value(value: float, unit: str = "") -> str:
    if unit == "count":
        return str(int(round(value)))
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text else "0"


_COMPACT_NAMES = (
    "fps",
    "jank_percent",
    "app_rss_mb",
    "app_cpu_percent",
    "system_load_1m",
)


def compact_metrics_summary(report: MetricsReport) -> str:
    """One-line highlight for a test row: ``FPS 54 (28–60) · RSS 128 MiB``."""
    bits: list[str] = []
    for name in _COMPACT_NAMES:
        summary = report.metrics.get(name)
        if summary is None:
            continue
        unit = summary.unit
        average = format_metric_value(summary.average, unit)
        span = (
            f"{format_metric_value(summary.min, unit)}"
            f"–{format_metric_value(summary.max, unit)}"
        )
        suffix = f" {unit}" if unit and unit not in {"count", "%", ""} else ""
        if unit == "%":
            average = f"{average}%"
            span = (
                f"{format_metric_value(summary.min, '')}"
                f"–{format_metric_value(summary.max, '')}%"
            )
        bits.append(f"{label_for(name)} {average} ({span}){suffix}")
    return " · ".join(bits)


def junit_metric_properties(report: MetricsReport) -> dict[str, str]:
    """Per-testcase JUnit properties, including the series as sampled."""
    props: dict[str, str] = {"metrics.samples": str(report.sample_count)}
    for name, summary in report.metrics.items():
        prefix = f"metric.{name}"
        props[f"{prefix}.min"] = format_metric_value(summary.min, summary.unit)
        props[f"{prefix}.max"] = format_metric_value(summary.max, summary.unit)
        props[f"{prefix}.average"] = format_metric_value(summary.average, summary.unit)
        props[f"{prefix}.median"] = format_metric_value(summary.median, summary.unit)
        series = report.series.get(name) or []
        if series:
            props[f"{prefix}.series"] = ",".join(
                format_metric_value(v, summary.unit) for v in series
            )
    return props


def format_metrics_lines(report: MetricsReport) -> list[str]:
    """Human-readable rows: ``FPS  min=.. max=.. average=.. median=..``."""
    lines: list[str] = []
    for name, summary in report.metrics.items():
        unit = summary.unit
        parts = [
            f"min={format_metric_value(summary.min, unit)}",
            f"max={format_metric_value(summary.max, unit)}",
            f"average={format_metric_value(summary.average, unit)}",
            f"median={format_metric_value(summary.median, unit)}",
        ]
        suffix = f" {unit}" if unit and unit not in {"count", ""} else ""
        label = f"{label_for(name):<18}"
        lines.append(f"{label} {'  '.join(parts)}{suffix}")
    return lines


def metrics_as_json(report: MetricsReport) -> dict[str, Any]:
    return report.model_dump(mode="json")
