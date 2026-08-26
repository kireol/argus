"""TestQualityAnalyzer — guards against common test-authoring mistakes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from argus_test_creator.models.authoring import AuthoringDocument, StepDraft
from argus_test_creator.models.common import Rect, parse_duration


@dataclass(frozen=True)
class QualityFinding:
    status: str  # ok | warn
    code: str
    message: str
    step_id: str | None = None

    @property
    def symbol(self) -> str:
        return "✓" if self.status == "ok" else "⚠"


@dataclass
class QualityReport:
    findings: list[QualityFinding] = field(default_factory=list)

    @property
    def warnings(self) -> list[QualityFinding]:
        return [f for f in self.findings if f.status == "warn"]

    @property
    def score(self) -> int:
        """0–100; deterministic and only meant for at-a-glance feedback."""
        if not self.findings:
            return 100
        return round(100 * (1 - len(self.warnings) / len(self.findings)))

    def render(self) -> str:
        return "Test Quality\n\n" + "\n".join(f"{f.symbol} {f.message}" for f in self.findings)


class QualityAnalyzer(Protocol):
    def analyze(self, document: AuthoringDocument) -> QualityReport: ...


class TestQualityAnalyzer:
    """Rule-based analyzer. Each rule is a method returning findings."""

    def __init__(self, *, screen_size: tuple[int, int] | None = None) -> None:
        self._screen_size = screen_size

    def analyze(self, document: AuthoringDocument) -> QualityReport:
        report = QualityReport()
        steps = [s for s in document.steps if s.enabled]
        report.findings.extend(self._name(document))
        report.findings.extend(self._synchronization(steps))
        report.findings.extend(self._verification(steps))
        report.findings.extend(self._fixed_waits(steps))
        report.findings.extend(self._redundant_taps(steps))
        report.findings.extend(self._broad_screenshots(steps))
        report.findings.extend(self._low_thresholds(steps))
        report.findings.extend(self._unbounded_ocr(steps))
        report.findings.extend(self._assets(document))
        report.findings.extend(self._variables(document))
        report.findings.extend(self._platforms(document))
        return report

    def _name(self, document: AuthoringDocument) -> list[QualityFinding]:
        name = document.metadata.name.strip()
        if len(name) >= 8 and len(name.split()) >= 2:
            return [QualityFinding("ok", "name", "Has a meaningful name")]
        return [QualityFinding("warn", "name", "Name is short or missing — describe the outcome "
                               "(e.g. 'Search shows Batman Begins')")]

    def _synchronization(self, steps: list[StepDraft]) -> list[QualityFinding]:
        if any(s.action == "wait_until" for s in steps):
            return [QualityFinding("ok", "sync", "Uses synchronization (wait_until)")]
        if any(s.action.startswith("device.") or s.action.startswith("backend.") for s in steps):
            return [QualityFinding("warn", "sync", "No wait_until step — after an action, wait "
                                   "for what should appear before verifying")]
        return []

    def _verification(self, steps: list[StepDraft]) -> list[QualityFinding]:
        if any(s.is_assertion for s in steps):
            return [QualityFinding("ok", "verify", "Contains verification")]
        return [QualityFinding("warn", "verify", "No verification — the test cannot fail "
                               "meaningfully")]

    def _fixed_waits(self, steps: list[StepDraft]) -> list[QualityFinding]:
        waits = [s for s in steps if s.action == "wait"]
        if not waits:
            return []
        total = 0.0
        for s in waits:
            try:
                total += parse_duration(s.params.get("duration", 0))
            except ValueError:
                pass
        return [QualityFinding("warn", "fixed_waits",
                               f"Contains {len(waits)} fixed wait(s) totalling {total:g}s")]

    def _redundant_taps(self, steps: list[StepDraft]) -> list[QualityFinding]:
        findings: list[QualityFinding] = []
        for index in range(1, len(steps)):
            a, b = steps[index - 1], steps[index]
            if a.action == b.action == "device.tap" and a.params == b.params:
                findings.append(QualityFinding(
                    "warn", "redundant_tap",
                    f"Step {index + 1} repeats the previous tap at the same position",
                    step_id=b.id,
                ))
        return findings

    def _broad_screenshots(self, steps: list[StepDraft]) -> list[QualityFinding]:
        findings: list[QualityFinding] = []
        for index, s in enumerate(steps, start=1):
            if s.condition is None:
                continue
            for leaf in s.condition.leaves():
                if leaf.type == "screenshot_matches" and "region" not in leaf.params:
                    size = f"{self._screen_size[0]}x{self._screen_size[1]} " if self._screen_size else ""  # noqa: E501
                    findings.append(QualityFinding(
                        "warn", "broad_screenshot",
                        f"Step {index}: screenshot assertion covers the entire {size}screen — "
                        "any pixel change (clock, cursor) fails it",
                        step_id=s.id,
                    ))
        return findings

    def _low_thresholds(self, steps: list[StepDraft]) -> list[QualityFinding]:
        findings: list[QualityFinding] = []
        for index, s in enumerate(steps, start=1):
            if s.condition is None:
                continue
            for leaf in s.condition.leaves():
                threshold = leaf.params.get("threshold")
                try:
                    if threshold is not None and float(threshold) < 0.7:
                        findings.append(QualityFinding(
                            "warn", "low_threshold",
                            f"Step {index}: image threshold {threshold} is very low",
                            step_id=s.id,
                        ))
                except (TypeError, ValueError):
                    pass
        return findings

    def _unbounded_ocr(self, steps: list[StepDraft]) -> list[QualityFinding]:
        ocr = [s for s in steps if s.condition and any(
            leaf.type in ("text_present", "text_not_present") for leaf in s.condition.leaves()
        )]
        if not ocr:
            return []
        unbounded = [s for s in ocr if not all(
            "region" in leaf.params for leaf in s.condition.leaves()  # type: ignore[union-attr]
            if leaf.type in ("text_present", "text_not_present")
        )]
        if len(unbounded) >= 3:
            return [QualityFinding("warn", "unbounded_ocr",
                                   f"{len(unbounded)} OCR assertions scan the whole screen — "
                                   "a region makes them faster and more reliable")]
        return [QualityFinding("ok", "ocr", "OCR assertions are bounded or few")]

    def _assets(self, document: AuthoringDocument) -> list[QualityFinding]:
        images = document.referenced_images()
        if not images:
            return []
        missing = [i for i in images if "${" not in i and document.asset_by_path(i) is None]
        if missing:
            return [QualityFinding("warn", "assets", f"Missing image assets: {', '.join(sorted(missing))}")]  # noqa: E501
        return [QualityFinding("ok", "assets", "Assets are referenced")]

    def _variables(self, document: AuthoringDocument) -> list[QualityFinding]:
        import re

        refs: set[str] = set()
        pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")

        def walk(value: object) -> None:
            if isinstance(value, str):
                refs.update(pattern.findall(value))
            elif isinstance(value, dict):
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)

        for s in document.steps + document.setup + document.teardown:
            walk(s.params)
            if s.condition:
                walk(s.condition.to_argus())
        unresolved = sorted(r for r in refs if r not in document.metadata.parameters)
        if unresolved:
            return [QualityFinding("warn", "variables",
                                   f"Variables not defined in parameters (may come from config): "
                                   f"{', '.join(unresolved)}")]
        return []

    def _platforms(self, document: AuthoringDocument) -> list[QualityFinding]:
        target = document.target
        platforms = document.metadata.platforms
        if target and platforms and target.platform not in platforms:
            return [QualityFinding("warn", "platform",
                                   f"Test platforms {platforms} do not include the selected "
                                   f"target platform {target.platform!r}")]
        return []


def region_fraction(region: Rect | None, screen: tuple[int, int]) -> float:
    if region is None:
        return 1.0
    return region.area / float(screen[0] * screen[1])
