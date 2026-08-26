"""Evidence collection — reuses Argus's artifact layout.

    results/stress/<run_id>/
      run.json                 ← StressRunRecord (seed, scenario, summary, failures)
      trace.jsonl              ← every event
      failures/<failure_id>/   ← before.png, after.png, history.json, logs.txt, failure.json
      samples/step_000123.png  ← optional periodic screenshots

Screenshots are taken lazily from the bounded observation ring — never one
full-resolution image per raw step.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from argus.artifacts.manager import TestArtifacts, safe_path_component
from argus.exceptions import UTFError
from argus.stress.config import EvidenceConfig
from argus.stress.models import Failure

if TYPE_CHECKING:
    from argus.stress.context import StressContext


class EvidenceCollector:
    def __init__(self, config: EvidenceConfig, run_dir: Path | None) -> None:
        self._config = config
        self._run_dir = run_dir
        self.collected = 0

    @property
    def enabled(self) -> bool:
        return self._run_dir is not None

    def collect(self, context: StressContext, failure: Failure) -> Failure:
        """Save evidence for ``failure``; returns the failure with evidence paths filled in."""
        if self._run_dir is None or self.collected >= self._config.max_failures_with_evidence:
            return failure
        self.collected += 1
        directory = self._run_dir / "failures" / safe_path_component(failure.failure_id)
        artifacts = TestArtifacts(directory)
        evidence: dict[str, Any] = {"dir": str(directory)}
        try:
            if self._config.save_screenshots:
                records = list(context.observations)
                if len(records) >= 2:
                    path = artifacts.save_image("before.png", records[-2].image)
                    evidence["before"] = str(path)
                if records:
                    path = artifacts.save_image("after.png", records[-1].image)
                    evidence["after"] = str(path)
                    ocr = records[-1].ocr
                    if ocr is not None:
                        artifacts.save_text("ocr.txt", ocr.text)
                        evidence["ocr"] = str(directory / "ocr.txt")
            history = {
                "run_id": context.run_id,
                "seed": context.seed,
                "step": failure.step,
                "scenario": context.config.name,
                "device": context.device_name,
                "recent_events": [e.model_dump(mode="json", exclude_none=True)
                                  for e in context.trace.recent(self._config.history)],
                "recent_actions": [
                    {"action": a.model_dump(mode="json"), "outcome": o.model_dump(mode="json")}
                    for a, o in list(context.action_history)[-self._config.history:]
                ],
                "recent_mutations": [
                    {"mutation": m.model_dump(mode="json"), "outcome": o.model_dump(mode="json")}
                    for m, o in list(context.mutation_history)[-self._config.history:]
                ],
                "entity_context": [r.model_dump(mode="json") for r in context.entity_context],
                "active_faults": [f.model_dump(mode="json") for f in context.active_faults],
            }
            artifacts.save_json("history.json", history)
            evidence["history"] = str(directory / "history.json")
            if self._config.save_logs and context.device is not None and (
                context.device.capabilities.supports_logs
            ):
                try:
                    artifacts.save_text("logs.txt", context.device.get_logs())
                    evidence["logs"] = str(directory / "logs.txt")
                except UTFError:
                    pass
            if context.backend is not None:
                try:
                    artifacts.save_json("backend_state.json", context.backend.get_state())
                    evidence["backend_state"] = str(directory / "backend_state.json")
                except Exception:  # noqa: BLE001 - optional evidence
                    pass
            updated = failure.model_copy(update={"evidence": evidence})
            artifacts.save_json("failure.json", updated.model_dump(mode="json"))
            return updated
        except Exception as exc:  # noqa: BLE001 - evidence must never mask the failure
            context.infrastructure_error(f"evidence collection failed: {exc}")
            return failure.model_copy(update={"evidence": evidence})

    def sample(self, context: StressContext) -> None:
        """Periodic screenshot sampling (``evidence.sample_every``)."""
        every = self._config.sample_every
        if self._run_dir is None or every <= 0 or context.step % every != 0:
            return
        record = context.last_observation
        if record is None:
            return
        artifacts = TestArtifacts(self._run_dir / "samples")
        artifacts.save_image(f"step_{context.step:06d}.png", record.image)


__all__ = ["EvidenceCollector"]
