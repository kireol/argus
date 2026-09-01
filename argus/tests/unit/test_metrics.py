"""In-run metrics aggregation and reporting helpers."""

from __future__ import annotations

from argus.engine.metrics import MetricsSampler
from argus.models.metrics import (
    MetricSample,
    MetricSummary,
    build_report,
    compact_metrics_summary,
    format_metrics_lines,
    junit_metric_properties,
    summarize_series,
)


class _StubDevice:
    def __init__(self, values: dict[str, float]) -> None:
        self.values = values
        self.began = 0

    def begin_metrics_session(self) -> None:
        self.began += 1

    def sample_metrics(self) -> dict[str, float]:
        return dict(self.values)


def test_summarize_uses_average_and_median_not_a_duplicate_mean() -> None:
    # Outlier pulls the average up; median stays on the middle sample.
    summary = summarize_series([1.0, 2.0, 100.0], unit="fps")
    assert summary is not None
    assert summary.min == 1.0
    assert summary.max == 100.0
    assert abs(summary.average - (1.0 + 2.0 + 100.0) / 3.0) < 1e-9
    assert summary.median == 2.0
    assert "mean" not in MetricSummary.model_fields


def test_build_report_keeps_samples_in_order() -> None:
    series = {"fps": [60.0, 30.0, 60.0], "app_rss_mb": [100.0, 110.0, 105.0]}
    samples = [
        MetricSample(t=0.0, values={"fps": 60.0, "app_rss_mb": 100.0}),
        MetricSample(t=1.0, values={"fps": 30.0, "app_rss_mb": 110.0}),
        MetricSample(t=2.0, values={"fps": 60.0, "app_rss_mb": 105.0}),
    ]
    report = build_report(series, interval_seconds=1.0, samples=samples)
    assert report is not None
    assert report.metrics["fps"].median == 60.0
    assert report.metrics["fps"].average == 50.0
    assert [tick.t for tick in report.samples] == [0.0, 1.0, 2.0]
    lines = format_metrics_lines(report)
    assert any("average=" in line and "median=" in line for line in lines)
    assert not any("mean=" in line for line in lines)
    compact = compact_metrics_summary(report)
    assert "FPS" in compact
    props = junit_metric_properties(report)
    assert props["metric.fps.average"] == "50"
    assert props["metric.fps.median"] == "60"
    assert "metric.fps.mean" not in props
    assert props["metric.fps.series"] == "60,30,60"


def test_sampler_records_samples() -> None:
    import time

    device = _StubDevice({"fps": 55.0, "app_rss_mb": 128.0})
    sampler = MetricsSampler(device, interval_seconds=0.2)
    sampler.start()
    time.sleep(0.35)
    report = sampler.stop()
    assert device.began == 1
    assert report is not None
    assert report.metrics["fps"].average == 55.0
    assert report.metrics["fps"].median == 55.0
    assert report.samples
    assert report.samples[0].values["fps"] == 55.0


def test_metric_summary_json_fields() -> None:
    dumped = MetricSummary(
        unit="fps", count=3, min=1, max=3, average=2, median=2
    ).model_dump()
    assert set(dumped) == {"unit", "count", "min", "max", "average", "median"}


def test_every_known_metric_has_a_description():
    from argus.models.metrics import METRIC_ORDER, description_for

    for name in METRIC_ORDER:
        text = description_for(name)
        assert text and text != name, name
    assert description_for("custom_thing") == ""
