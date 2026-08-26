"""ArgusIntegration — discover, validate, run, and read results from the Argus CLI."""

from argus_test_creator.integrations.argus.integration import (
    INSTALL_HINT,
    ArgusInfo,
    ArgusIntegration,
    ArgusRunResult,
    ArgusValidationResult,
    discover_argus,
)

__all__ = [
    "INSTALL_HINT",
    "ArgusInfo",
    "ArgusIntegration",
    "ArgusRunResult",
    "ArgusValidationResult",
    "discover_argus",
]
