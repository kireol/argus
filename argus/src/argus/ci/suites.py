"""Named CI suites -> the engine's own selection filters.

Precedence (deterministic, documented in docs/ci-cd.md):

1. explicit CLI selectors (``--test/--feature/--tag/--platform``) *narrow*
2. the ``--suite`` selectors, which *narrow*
3. the default: every test.

Selectors combine with AND — a CLI selector never silently replaces a suite
selector. Lists of the same kind intersect (features, platforms, ids); tags
accumulate (every tag must be present); tag expressions are conjoined.
"""

from __future__ import annotations

from argus.config.models import CIConfig, CISuiteConfig
from argus.engine.filters import TestFilter, build_filter
from argus.exceptions import ConfigurationError


def resolve_suite(ci: CIConfig, name: str) -> CISuiteConfig:
    """The fully merged suite (``extends`` chains resolved, cycles rejected)."""
    if name not in ci.suites:
        defined = ", ".join(sorted(ci.suites)) or "<none>"
        raise ConfigurationError(
            f"Invalid configuration: ci.suites\n\nUnknown suite {name!r}.\n"
            f"Defined suites: {defined}",
            remediation="Define the suite under ci.suites in configuration or pick one of "
            "the defined suites.",
        )
    chain: list[str] = []
    current: str | None = name
    while current is not None:
        if current in chain:
            raise ConfigurationError(
                f"Invalid configuration: ci.suites.{name}.extends\n\n"
                f"Suite inheritance cycle: {' -> '.join([*chain, current])}",
                remediation="Remove the circular extends: reference.",
            )
        chain.append(current)
        current = ci.suites[current].extends
    merged = CISuiteConfig()
    for suite_name in reversed(chain):  # base first, the requested suite last
        suite = ci.suites[suite_name]
        merged = CISuiteConfig(
            description=suite.description or merged.description,
            tags=_union(merged.tags, suite.tags),
            features=_union(merged.features, suite.features),
            platforms=_union(merged.platforms, suite.platforms),
            tests=_union(merged.tests, suite.tests),
        )
    return merged


def suite_filter(suite: CISuiteConfig) -> TestFilter:
    return build_filter(
        test_ids=suite.tests, features=suite.features, tags=suite.tags, platforms=suite.platforms
    )


def combine_filters(base: TestFilter, narrow: TestFilter) -> TestFilter:
    """AND two filters: ``narrow`` (CLI) restricts ``base`` (suite)."""
    expression = _conjoin(base.tag_expression, narrow.tag_expression)
    return TestFilter(
        test_ids=_intersect(base.test_ids, narrow.test_ids),
        features=_intersect_ci(base.features, narrow.features),
        tags=_union(base.tags, narrow.tags),
        platforms=_intersect(base.platforms, narrow.platforms),
        tag_expression=expression,
    )


def _union(left: list[str], right: list[str]) -> list[str]:
    merged = list(left)
    merged.extend(v for v in right if v not in merged)
    return merged


def _intersect(left: list[str], right: list[str]) -> list[str]:
    if not left:
        return list(right)
    if not right:
        return list(left)
    common = [v for v in left if v in right]
    # An empty intersection must still *filter* (select nothing), never fall
    # back to "everything": use an impossible sentinel list.
    return common or ["__no_match__"]


def _intersect_ci(left: list[str], right: list[str]) -> list[str]:
    if not left:
        return list(right)
    if not right:
        return list(left)
    lowered = {v.lower() for v in right}
    common = [v for v in left if v.lower() in lowered]
    return common or ["__no_match__"]


def _conjoin(left: str | None, right: str | None) -> str | None:
    if left and right:
        return f"({left}) and ({right})"
    return left or right
