"""Configuration loading.

Precedence (highest wins):

1. Explicit ``--config`` file
2. User configuration (``argus init`` creates it)
3. Repository ``config/default.yaml``
4. Built-in defaults

Environment variables are substituted with ``${NAME}`` syntax. Unresolved
references are kept literal so that optional, unconfigured components can be
reported as "not configured" rather than crashing the whole framework.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_path
from pydantic import ValidationError

from argus.config.models import AppConfig
from argus.exceptions import ConfigurationError
from argus.utilities.variables import expand_variables


def default_user_config_path() -> Path:
    """Platform-appropriate user configuration file location."""
    return user_config_path("argus", appauthor=False) / "config.yaml"


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"Invalid YAML in configuration file {path}: {exc}",
            remediation="Fix the YAML syntax error shown above.",
        ) from exc
    except OSError as exc:
        raise ConfigurationError(f"Cannot read configuration file {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Configuration file {path} must contain a YAML mapping at the top level."
        )
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_with_extends(path: Path, *, seen: frozenset[Path] | None = None) -> dict[str, Any]:
    """Load a YAML config, resolving an optional ``extends:`` chain.

    ``extends`` is a path relative to the file that declares it (or absolute).
    Bases are merged first; the declaring file wins on conflicts. The key is
    stripped before validation so AppConfig never sees it.
    """
    resolved = path.resolve()
    chain = seen or frozenset()
    if resolved in chain:
        raise ConfigurationError(
            f"Configuration extends cycle involving {resolved}",
            remediation="Remove the circular extends: reference.",
        )
    data = _read_yaml(path)
    extends = data.pop("extends", None)
    if extends is None:
        return data
    if not isinstance(extends, str) or not extends.strip():
        raise ConfigurationError(
            f"'extends' in {path} must be a non-empty string path.",
            remediation="Example: extends: base.yaml",
        )
    base_path = Path(extends)
    if not base_path.is_absolute():
        base_path = path.parent / base_path
    if not base_path.is_file():
        raise ConfigurationError(
            f"Extended configuration not found: {base_path} (from {path})",
            remediation="Check the extends: path relative to the config file.",
        )
    base = _load_with_extends(base_path, seen=chain | {resolved})
    return _deep_merge(base, data)


def load_config(
    config_file: str | Path | None = None,
    *,
    root_dir: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> AppConfig:
    """Load, merge, env-expand, and validate configuration."""
    root = Path(root_dir) if root_dir else Path.cwd()
    environment = dict(os.environ if env is None else env)

    layers: list[tuple[Path, dict[str, Any]]] = []

    repo_default = root / "config" / "default.yaml"
    if repo_default.is_file():
        layers.append((repo_default, _load_with_extends(repo_default)))

    user_cfg = default_user_config_path()
    if user_cfg.is_file():
        layers.append((user_cfg, _load_with_extends(user_cfg)))

    explicit: Path | None = None
    if config_file is not None:
        explicit = Path(config_file)
        if not explicit.is_file():
            raise ConfigurationError(
                f"Configuration file not found: {explicit}",
                remediation="Check the --config path, or run 'argus init' to create one.",
            )
        layers.append((explicit, _load_with_extends(explicit)))

    merged: dict[str, Any] = {}
    for _, layer in layers:
        merged = _deep_merge(merged, layer)

    # Substitute environment variables; unresolved refs stay literal so that
    # optional components degrade to "not configured" instead of erroring.
    expanded = expand_variables(merged, environment, strict=False, source="config")

    try:
        config = AppConfig.model_validate(expanded)
    except ValidationError as exc:
        source = explicit or (layers[-1][0] if layers else "built-in defaults")
        raise ConfigurationError(
            f"Invalid configuration ({source}):\n{exc}",
            remediation="Fix the fields listed above; see docs/configuration.md.",
        ) from exc

    config.config_file = str(explicit or (layers[-1][0] if layers else "")) or None
    config.root_dir = str(root)
    return config
