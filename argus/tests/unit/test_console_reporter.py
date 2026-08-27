"""Console reporter progress counters."""

import logging
from io import StringIO

from rich.console import Console

from argus.events.bus import EventBus
from argus.events.events import (
    TestFailed,
    TestPassed,
    TestRunStarted,
    TestSkipped,
    TestStarted,
)
from argus.models.results import TestResult, TestStatus
from argus.reporting.console import ConsoleReporter


def _result(
    test_id: str,
    name: str,
    *,
    feature: str = "Prndl",
    status: TestStatus = TestStatus.PASSED,
) -> TestResult:
    return TestResult(test_id=test_id, name=name, feature=feature, status=status)


def _reporter(*, log_level: int = logging.WARNING) -> tuple[ConsoleReporter, StringIO]:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, width=120, highlight=False)
    logging.getLogger("argus").setLevel(log_level)
    return ConsoleReporter(console), buffer


class TestConsoleProgressCounters:
    def test_passed_failed_skipped_include_index_and_total(self):
        reporter, buffer = _reporter()
        bus = EventBus()
        reporter.attach(bus)

        bus.publish(TestRunStarted(total_tests=3))
        bus.publish(TestStarted(test_id="T-1", name="first", feature="Prndl"))
        bus.publish(TestPassed(result=_result("T-1", "first")))
        bus.publish(TestStarted(test_id="T-2", name="second", feature="Prndl"))
        bus.publish(
            TestFailed(result=_result("T-2", "second", status=TestStatus.FAILED))
        )
        bus.publish(
            TestSkipped(result=_result("T-3", "third", status=TestStatus.SKIPPED))
        )

        text = buffer.getvalue()
        assert "✓ 1/3 - T-1" in text
        assert "✗ 2/3 - T-2" in text
        assert "3/3 - T-3" in text
        assert "Prndl" in text
        # Running line is overwritten in-place (ANSI cursor-up / clear).
        assert "\x1b[1A" in text

    def test_skip_to_preserves_progress_numbering(self):
        reporter, buffer = _reporter()
        bus = EventBus()
        reporter.attach(bus)

        bus.publish(TestRunStarted(total_tests=5, start_index=4, filters={"skip_to": 4}))
        bus.publish(TestStarted(test_id="T-4", name="fourth", feature="Prndl"))
        bus.publish(TestPassed(result=_result("T-4", "fourth")))
        bus.publish(TestStarted(test_id="T-5", name="fifth", feature="Prndl"))
        bus.publish(TestPassed(result=_result("T-5", "fifth")))

        text = buffer.getvalue()
        assert "Starting at test 4/5" in text
        assert "✓ 4/5 - T-4" in text
        assert "✓ 5/5 - T-5" in text
        assert "1/5" not in text
        assert "2/5" not in text
        assert "3/5" not in text

    def test_start_line_shows_progress_before_completion(self):
        reporter, buffer = _reporter()
        bus = EventBus()
        reporter.attach(bus)

        bus.publish(TestRunStarted(total_tests=2))
        bus.publish(
            TestStarted(
                test_id="TS-005",
                name="Hazard lights show both turn signals on",
                feature="TurnSignal",
                platform="android",
            )
        )

        text = buffer.getvalue()
        assert "TurnSignal" in text
        assert "→ 1/2 - TS-005" in text
        assert "(android)" in text
        assert "✓" not in text
        assert "✗" not in text

    def test_completion_overwrites_start_line_when_logs_quiet(self):
        reporter, buffer = _reporter(log_level=logging.WARNING)
        bus = EventBus()
        reporter.attach(bus)

        bus.publish(TestRunStarted(total_tests=1))
        bus.publish(TestStarted(test_id="T-1", name="only", feature="Prndl"))
        bus.publish(TestPassed(result=_result("T-1", "only")))

        text = buffer.getvalue()
        assert "✓ 1/1 - T-1" in text
        assert "\x1b[1A" in text  # cursor up to replace → line
        assert "2/1" not in text

    def test_completion_prints_second_line_when_info_logs_enabled(self):
        reporter, buffer = _reporter(log_level=logging.INFO)
        bus = EventBus()
        reporter.attach(bus)

        bus.publish(TestRunStarted(total_tests=1))
        bus.publish(TestStarted(test_id="T-1", name="only", feature="Prndl"))
        bus.publish(TestPassed(result=_result("T-1", "only")))

        text = buffer.getvalue()
        assert "→ 1/1 - T-1" in text
        assert "✓ 1/1 - T-1" in text
        assert "\x1b[1A" not in text  # no overwrite while INFO logs may interleave

    def test_retry_reuses_progress_without_double_counting(self):
        reporter, buffer = _reporter()
        bus = EventBus()
        reporter.attach(bus)

        bus.publish(TestRunStarted(total_tests=1))
        bus.publish(TestStarted(test_id="T-1", name="flaky", feature="Prndl"))
        bus.publish(TestStarted(test_id="T-1", name="flaky", feature="Prndl"))
        bus.publish(TestPassed(result=_result("T-1", "flaky")))

        text = buffer.getvalue()
        assert "✓ 1/1 - T-1" in text
        assert text.count("\x1b[1A") >= 2  # refresh →, then overwrite with ✓
        assert "2/1" not in text

    def test_quiet_still_advances_index_for_printed_failures(self):
        buffer = StringIO()
        console = Console(file=buffer, force_terminal=True, width=120, highlight=False)
        logging.getLogger("argus").setLevel(logging.ERROR)
        reporter = ConsoleReporter(console, quiet=True)
        bus = EventBus()
        reporter.attach(bus)

        bus.publish(TestRunStarted(total_tests=3))
        bus.publish(TestStarted(test_id="T-1", name="first", feature="Prndl"))
        bus.publish(TestPassed(result=_result("T-1", "first")))
        bus.publish(TestStarted(test_id="T-2", name="second", feature="Prndl"))
        bus.publish(
            TestFailed(result=_result("T-2", "second", status=TestStatus.FAILED))
        )

        text = buffer.getvalue()
        assert "→" not in text  # start lines suppressed in quiet mode
        assert "1/3" not in text  # passed line suppressed in quiet mode
        assert "2/3 - T-2" in text


class TestFeatureLifecycleLines:
    def test_feature_setup_and_teardown_are_reported(self):
        from argus.events.events import (
            FeatureSetupCompleted,
            FeatureSetupStarted,
            FeatureTeardownCompleted,
            FeatureTeardownStarted,
        )

        reporter, buffer = _reporter()
        bus = EventBus()
        reporter.attach(bus)
        bus.publish(TestRunStarted(total_tests=1))
        bus.publish(FeatureSetupStarted(feature="Movies", platform="android"))
        bus.publish(FeatureSetupCompleted(feature="Movies", platform="android", passed=True))
        bus.publish(FeatureTeardownStarted(feature="Movies", platform="android"))
        bus.publish(
            FeatureTeardownCompleted(
                feature="Movies", platform="android", passed=False, error="backend.set — boom"
            )
        )
        import re

        text = re.sub(r"\x1b\[[0-9;]*m", "", buffer.getvalue())
        assert "Movies" in text
        assert "✓ setup (android)" in text
        assert "✗ teardown (android)" in text
        assert "backend.set — boom" in text


def _metrics_result() -> TestResult:
    from argus.models.metrics import build_report

    report = build_report({"fps": [30.0, 60.0, 60.0]}, interval_seconds=1.0)
    return TestResult(
        test_id="T-1",
        name="only",
        feature="Prndl",
        status=TestStatus.PASSED,
        metrics=report,
    )


class TestMetricsConsoleOutput:
    def test_metrics_print_under_each_test_by_default(self):
        reporter, buffer = _reporter()
        bus = EventBus()
        reporter.attach(bus)
        bus.publish(TestRunStarted(total_tests=1))
        bus.publish(TestStarted(test_id="T-1", name="only", feature="Prndl"))
        bus.publish(TestPassed(result=_metrics_result()))
        text = buffer.getvalue()
        assert "FPS" in text
        assert "average=" in text
        assert "median=" in text

    def test_no_logs_hides_metrics_keeps_progress(self):
        from argus.events.events import TestRunCompleted
        from argus.models.results import RunResult, RunStatus

        reporter, buffer = _reporter()
        reporter.no_logs = True
        bus = EventBus()
        reporter.attach(bus)
        result = _metrics_result()
        bus.publish(TestRunStarted(total_tests=1))
        bus.publish(TestStarted(test_id="T-1", name="only", feature="Prndl"))
        bus.publish(TestPassed(result=result))
        bus.publish(
            TestRunCompleted(
                result=RunResult(
                    status=RunStatus.PASSED,
                    tests=[result],
                    metrics=result.metrics,
                )
            )
        )
        text = buffer.getvalue()
        assert "✓ 1/1 - T-1" in text
        assert "TEST RUN PASSED" in text
        assert "FPS" not in text
        assert "average=" not in text
        assert "median=" not in text

