"""Variable expansion for test definitions and configuration.

Supports ``${name}`` references resolved against a mapping. Used for both
environment-variable substitution in configuration files and test-parameter
substitution in step arguments.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from argus.exceptions import ConfigurationError

_VAR_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_.]*)(?::-(?P<default>[^}]*))?\}")


def expand_string(
    text: str,
    variables: Mapping[str, Any],
    *,
    strict: bool = True,
    source: str = "value",
) -> Any:
    """Expand ``${name}`` references in a single string.

    If the entire string is a single reference (``"${movie_id}"``), the
    referenced value is returned with its original type (int stays int).
    Otherwise references are interpolated into the string.

    Supports ``${name:-default}`` fallback syntax.
    """
    full_match = _VAR_RE.fullmatch(text)
    if full_match:
        return _resolve(full_match, variables, strict=strict, source=source)

    def _sub(match: re.Match[str]) -> str:
        return str(_resolve(match, variables, strict=strict, source=source))

    return _VAR_RE.sub(_sub, text)


def _resolve(
    match: re.Match[str],
    variables: Mapping[str, Any],
    *,
    strict: bool,
    source: str,
) -> Any:
    name = match.group("name")
    default = match.group("default")
    if name in variables and variables[name] is not None:
        return variables[name]
    if default is not None:
        return default
    if strict:
        raise ConfigurationError(
            f"Unresolved variable ${{{name}}} in {source}",
            remediation=(
                f"Define {name!r} (as a test parameter, config value, or environment "
                f'variable) or provide a default: ${{{name}:-default}}.'
            ),
        )
    return match.group(0)


def expand_variables(
    value: Any,
    variables: Mapping[str, Any],
    *,
    strict: bool = True,
    source: str = "value",
) -> Any:
    """Recursively expand ``${name}`` references in strings, lists, and dicts."""
    if isinstance(value, str):
        return expand_string(value, variables, strict=strict, source=source)
    if isinstance(value, Mapping):
        return {
            key: expand_variables(item, variables, strict=strict, source=f"{source}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            expand_variables(item, variables, strict=strict, source=f"{source}[{i}]")
            for i, item in enumerate(value)
        ]
    return value
