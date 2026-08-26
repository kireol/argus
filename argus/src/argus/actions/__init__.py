"""Action system: pluggable, declarative test steps."""

from argus.actions.base import Action, ActionRegistry, ActionResult
from argus.actions.builtin import register

__all__ = ["Action", "ActionRegistry", "ActionResult", "register"]
