"""Failure-category vocabulary shared by configuration, retry, and reporting.

The engine records a *structured* ``failure_category`` per step/test
(``assertion``, ``timeout``, ``device_connection``, ``backend``,
``screenshot``, ``feature_setup``, ``error``). The CI layer exposes a
provider-neutral vocabulary on top of it; the mapping lives here so no
other module has to match error strings.
"""

from __future__ import annotations

from enum import StrEnum


class FailureCategory(StrEnum):
    """CI-level classification of a failed test or run."""

    TEST_FAILURE = "test_failure"
    ASSERTION_FAILURE = "assertion_failure"
    VISUAL_REGRESSION = "visual_regression"
    DEVICE_ERROR = "device_error"
    CONNECTION_ERROR = "connection_error"
    TIMEOUT = "timeout"
    SCREENSHOT_CAPTURE_ERROR = "screenshot_capture_error"
    CONFIGURATION_ERROR = "configuration_error"
    TEST_DEFINITION_ERROR = "test_definition_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    POLICY_FAILURE = "policy_failure"
    INTERNAL_ERROR = "internal_error"


#: Engine ``failure_category`` -> CI category. ``assertion`` is refined to
#: ``visual_regression`` by :func:`argus.ci.classify.classify_test` when the
#: failing verification compared images.
ENGINE_TO_CI: dict[str, FailureCategory] = {
    "assertion": FailureCategory.ASSERTION_FAILURE,
    "timeout": FailureCategory.TIMEOUT,
    "device_connection": FailureCategory.DEVICE_ERROR,
    "backend": FailureCategory.CONNECTION_ERROR,
    "screenshot": FailureCategory.SCREENSHOT_CAPTURE_ERROR,
    "feature_setup": FailureCategory.TEST_FAILURE,
    "error": FailureCategory.INTERNAL_ERROR,
}

#: CI retry category -> engine category. Only transient categories are
#: retryable; assertion failures and visual regressions never are.
RETRYABLE: dict[str, str] = {
    FailureCategory.TIMEOUT.value: "timeout",
    FailureCategory.DEVICE_ERROR.value: "device_connection",
    FailureCategory.CONNECTION_ERROR.value: "backend",
    FailureCategory.SCREENSHOT_CAPTURE_ERROR.value: "screenshot",
}

#: Accepted synonyms for ``ci.retry.on`` (spec vocabulary) -> canonical CI name.
RETRY_ALIASES: dict[str, str] = {
    "device_timeout": FailureCategory.TIMEOUT.value,
    "device_disconnected": FailureCategory.DEVICE_ERROR.value,
    "transient_transport_error": FailureCategory.CONNECTION_ERROR.value,
}

DEFAULT_RETRY_ON: tuple[str, ...] = (
    FailureCategory.TIMEOUT.value,
    FailureCategory.DEVICE_ERROR.value,
    FailureCategory.CONNECTION_ERROR.value,
    FailureCategory.SCREENSHOT_CAPTURE_ERROR.value,
)


def canonical_retry_category(name: str) -> str | None:
    """Canonical CI retry category for ``name`` (alias-aware); ``None`` if unknown."""
    canonical = RETRY_ALIASES.get(name, name)
    return canonical if canonical in RETRYABLE else None


def retry_categories_for_engine(names: list[str]) -> frozenset[str]:
    """Translate CI retry categories into engine failure categories."""
    engine: set[str] = set()
    for name in names:
        canonical = canonical_retry_category(name)
        if canonical is not None:
            engine.add(RETRYABLE[canonical])
    return frozenset(engine)
