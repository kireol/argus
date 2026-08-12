"""Action system: pluggable, declarative test steps."""

from utf.actions.base import Action, ActionRegistry, ActionResult
from utf.actions.builtin import register

__all__ = ["Action", "ActionRegistry", "ActionResult", "register"]
