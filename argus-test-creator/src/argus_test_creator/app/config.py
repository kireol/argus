"""Configuration hierarchy: defaults → project → user → environment → explicit overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from argus_test_creator.core.errors import ProjectError
from argus_test_creator.core.paths import user_config_path

ENV_PREFIX = "ARGUS_CREATOR_"


class RecordingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "smart"  # smart | exact
    settle_ms: int = Field(default=150, ge=0, le=5000)
    capture_after_actions: bool = True
    live_preview_fps: float = Field(default=4.0, gt=0, le=30)
    suggest_assertions: bool = True


class OCRConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "tesseract"  # tesseract | fake
    language: str = "eng"


class ArgusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable: str | None = None
    run_timeout: float = Field(default=600.0, gt=0)


class CreatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    argus: ArgusConfig = Field(default_factory=ArgusConfig)
    diagnostic: bool = False
    workers: int = Field(default=4, ge=1, le=32)
    #: Extra target profiles (mapping id → TargetProfile fields).
    targets: dict[str, dict[str, Any]] = Field(default_factory=dict)

    sources: list[str] = Field(default_factory=list)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectError(f"Cannot read configuration {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _from_env(env: dict[str, str]) -> dict[str, Any]:
    """``ARGUS_CREATOR_ARGUS__EXECUTABLE=/x`` → ``{"argus": {"executable": "/x"}}``."""
    out: dict[str, Any] = {}
    for key, value in env.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX):].lower().split("__")
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    if "ARGUS_EXECUTABLE" in env and "executable" not in out.get("argus", {}):
        out.setdefault("argus", {})["executable"] = env["ARGUS_EXECUTABLE"]
    return out


def _coerce(value: str) -> Any:
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def load_config(
    *,
    project_root: Path | None = None,
    user_path: Path | None = None,
    env: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> CreatorConfig:
    env = dict(os.environ if env is None else env)
    merged: dict[str, Any] = {}
    sources: list[str] = ["defaults"]
    user_file = user_path if user_path is not None else user_config_path()
    project_file = project_root / ".argus-creator" / "config.yaml" if project_root else None
    for label, path in (("user", user_file), ("project", project_file)):
        if path is not None and path.is_file():
            merged = _deep_merge(merged, _read_yaml(path))
            sources.append(f"{label}:{path}")
    env_values = _from_env(env)
    if env_values:
        merged = _deep_merge(merged, env_values)
        sources.append("environment")
    if overrides:
        merged = _deep_merge(merged, overrides)
        sources.append("overrides")
    merged.pop("sources", None)
    config = CreatorConfig.model_validate(merged)
    config.sources = sources
    return config
