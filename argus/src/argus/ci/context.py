"""Provider-neutral CI context.

Every provider adapter normalizes its environment variables into this one
shape. Missing values stay ``None`` — they are never fabricated. The context
is immutable and safe to serialize into ``metadata/ci.json``: it only ever
holds whitelisted, non-secret values.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from argus.logging import redact


class CIContext(BaseModel):
    """Normalized description of where a CI run executes."""

    model_config = ConfigDict(frozen=True)

    provider: str
    display_name: str
    detected: bool = False
    repository: str | None = None
    repository_url: str | None = None
    branch: str | None = None
    commit: str | None = None
    commit_sha: str | None = None
    pull_request: str | None = None
    workflow: str | None = None
    job: str | None = None
    run_id: str | None = None
    run_number: str | None = None
    run_url: str | None = None
    actor: str | None = None
    event: str | None = None
    workspace: str | None = None
    base_branch: str | None = None
    head_branch: str | None = None

    @property
    def short_commit(self) -> str | None:
        sha = self.commit_sha or self.commit
        return sha[:7] if sha else None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready view with obvious secrets redacted (defensive)."""
        data = self.model_dump(mode="json")
        return {k: redact(v) if isinstance(v, str) else v for k, v in data.items()}


Environment = Mapping[str, str]


def clean(environment: Environment, name: str) -> str | None:
    """Return ``environment[name]`` stripped, or ``None`` when unset/blank."""
    value = environment.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}
