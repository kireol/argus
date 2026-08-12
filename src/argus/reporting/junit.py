"""JUnit XML report for CI systems."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from argus.models.results import RunResult, TestStatus


def write_junit_report(result: RunResult, path: Path) -> Path:
    suites = ET.Element("testsuites")
    features: dict[str, list] = {}
    for test in result.tests:
        features.setdefault(test.feature, []).append(test)

    for feature, tests in features.items():
        suite = ET.SubElement(suites, "testsuite")
        suite.set("name", feature)
        suite.set("tests", str(len(tests)))
        suite.set(
            "failures",
            str(len([t for t in tests if t.status == TestStatus.FAILED])),
        )
        suite.set("errors", str(len([t for t in tests if t.status == TestStatus.ERROR])))
        suite.set(
            "skipped", str(len([t for t in tests if t.status == TestStatus.SKIPPED]))
        )
        suite.set("time", f"{sum(t.duration for t in tests):.3f}")

        for test in tests:
            case = ET.SubElement(suite, "testcase")
            name = test.name if test.platform is None else f"{test.name} [{test.platform}]"
            case.set("name", f"{test.test_id}: {name}")
            case.set("classname", f"{feature}.{test.test_id}")
            case.set("time", f"{test.duration:.3f}")
            if test.status == TestStatus.FAILED:
                failure = ET.SubElement(case, "failure")
                failure.set("message", (test.error or "test failed")[:500])
                failure.text = _failure_detail(test)
            elif test.status == TestStatus.ERROR:
                error = ET.SubElement(case, "error")
                error.set("message", (test.error or "test error")[:500])
                error.text = _failure_detail(test)
            elif test.status == TestStatus.SKIPPED:
                skipped = ET.SubElement(case, "skipped")
                skipped.set("message", test.error or "skipped")

    tree = ET.ElementTree(suites)
    ET.indent(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="unicode", xml_declaration=True)
    return path


def _failure_detail(test) -> str:
    lines = [test.error or ""]
    for step in test.steps:
        if not step.passed:
            lines.append(f"step: {step.action}")
            lines.append(f"message: {step.message}")
            if step.error:
                lines.append(step.error)
    if test.artifact_dir:
        lines.append(f"artifacts: {test.artifact_dir}")
    return "\n".join(lines)
