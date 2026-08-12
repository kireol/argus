"""Condition system: declarative predicates evaluated against the system under test."""

from utf.conditions.base import Condition, ConditionFactory
from utf.conditions.builtin import register

__all__ = ["Condition", "ConditionFactory", "register"]
