"""Target catalog: which platforms exist, what they can do, how Argus addresses them."""

from argus_test_creator.targets.catalog import (
    PLATFORM_CAPABILITIES,
    TargetCatalog,
    builtin_targets,
)

__all__ = ["PLATFORM_CAPABILITIES", "TargetCatalog", "builtin_targets"]
