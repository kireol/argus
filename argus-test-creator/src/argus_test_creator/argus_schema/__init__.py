"""The Argus test schema as data.

This is the ONE place the Creator encodes knowledge about Argus actions and
conditions (names, parameters, capability requirements). It is kept in sync
with the installed Argus package by ``tests/integration/test_argus_schema_sync.py``
and can be refreshed at runtime via ``ArgusIntegration.inspect_schema``.
"""

from argus_test_creator.argus_schema.actions import ACTIONS, ActionSpec, ParamSpec, action_spec
from argus_test_creator.argus_schema.conditions import CONDITIONS, ConditionSpec, condition_spec

__all__ = [
    "ACTIONS",
    "CONDITIONS",
    "ActionSpec",
    "ConditionSpec",
    "ParamSpec",
    "action_spec",
    "condition_spec",
]
