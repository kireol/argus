"""HTML report generation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image

from argus.models.results import (
    RunResult,
    RunStatus,
    StepResult,
    TestResult,
    TestStatus,
)
from argus.reporting.html import write_html_report


def _png(path: Path, color: tuple[int, int, int] = (200, 40, 40)) -> None:
    arr = np.zeros((24, 32, 3), dtype=np.uint8)
    arr[:, :] = color
    Image.fromarray(arr).save(path)


def test_html_report_lists_tests_and_embeds_images(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    fail_dir = run_dir / "TT-FAIL"
    fail_dir.mkdir(parents=True)
    _png(fail_dir / "actual.png", (200, 40, 40))
    _png(fail_dir / "expected.png", (40, 200, 40))
    _png(fail_dir / "diff.png", (40, 40, 200))
    _png(fail_dir / "screenshot.png", (180, 180, 40))

    result = RunResult(
        status=RunStatus.FAILED,
        started_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
        duration=12.5,
        results_dir=str(run_dir),
        tests=[
            TestResult(
                test_id="TT-PASS",
                name="ok",
                feature="EvRange",
                status=TestStatus.PASSED,
                duration=1.2,
                platform="android",
            ),
            TestResult(
                test_id="TT-FAIL",
                name="broken image match",
                feature="DoorAjar",
                status=TestStatus.FAILED,
                duration=3.4,
                platform="android",
                error="Image 'icn.png' not found (confidence 0.4)",
                failure_category="assertion",
                artifact_dir=str(fail_dir),
                steps=[
                    StepResult(
                        action="verify",
                        name="check icon",
                        passed=False,
                        error="Image not found",
                    )
                ],
            ),
            TestResult(
                test_id="TT-SKIP",
                name="skipped",
                feature="EvRange",
                status=TestStatus.SKIPPED,
            ),
        ],
    )

    path = write_html_report(result, run_dir / "report.html")
    html = path.read_text(encoding="utf-8")

    assert "TT-PASS" in html
    assert "TT-FAIL" in html
    assert "TT-SKIP" in html
    assert "EvRange" in html
    assert "DoorAjar" in html
    assert "Image &#x27;icn.png&#x27; not found" in html or "Image 'icn.png' not found" in html
    assert "TT-FAIL/actual.png" in html
    assert "TT-FAIL/expected.png" in html
    assert "TT-FAIL/diff.png" in html
    assert "TT-FAIL/screenshot.png" in html
    assert "broken image match" in html
    assert 'data-filter="failed"' in html


def test_html_report_omits_gallery_when_no_images(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    art = run_dir / "TT-EMPTY"
    art.mkdir(parents=True)
    (art / "logs.txt").write_text("log", encoding="utf-8")

    result = RunResult(
        status=RunStatus.FAILED,
        results_dir=str(run_dir),
        tests=[
            TestResult(
                test_id="TT-EMPTY",
                name="no images",
                feature="Alerts",
                status=TestStatus.FAILED,
                error="timeout",
                artifact_dir=str(art),
            )
        ],
    )
    html = write_html_report(result, run_dir / "report.html").read_text(encoding="utf-8")
    assert "timeout" in html
    assert "artifacts:" in html
    assert "<img " not in html


def test_html_report_shows_metrics_next_to_test(tmp_path: Path) -> None:
    from argus.models.metrics import MetricSample, build_report

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    report = build_report(
        {"fps": [30.0, 60.0, 60.0], "app_rss_mb": [100.0, 120.0, 110.0]},
        interval_seconds=1.0,
        samples=[
            MetricSample(t=0.0, values={"fps": 30.0, "app_rss_mb": 100.0}),
            MetricSample(t=1.0, values={"fps": 60.0, "app_rss_mb": 120.0}),
            MetricSample(t=2.0, values={"fps": 60.0, "app_rss_mb": 110.0}),
        ],
    )
    result = RunResult(
        status=RunStatus.PASSED,
        results_dir=str(run_dir),
        tests=[
            TestResult(
                test_id="VM-TT-001",
                name="battery telltale",
                feature="Telltales",
                status=TestStatus.PASSED,
                duration=2.1,
                platform="android",
                metrics=report,
            )
        ],
        metrics=report,
    )
    html = write_html_report(result, run_dir / "report.html").read_text(encoding="utf-8")
    assert "Metrics" in html
    assert "Average" in html
    assert "Median" in html
    assert "FPS" in html
    assert "30" in html and "60" in html
    assert "Samples during this test" in html
    assert ">Average</th>" in html
    assert ">Median</th>" in html

