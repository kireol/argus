"""Generic reporter: local reports only, no provider APIs."""

from __future__ import annotations

from collections.abc import Mapping

from argus.ci.artifacts import CIArtifactLayout
from argus.ci.reporters.base import CIReporter
from argus.ci.result import CIRunResult


class GenericReporter(CIReporter):
    name = "generic"

    def publish(
        self,
        result: CIRunResult,
        layout: CIArtifactLayout | None,
        environment: Mapping[str, str],
        *,
        summary: bool = True,
        annotations: bool = True,
        max_annotations: int = 20,
    ) -> list[str]:
        if layout is None:
            return []
        return [f"Reports: {layout.report_json.parent}"]
