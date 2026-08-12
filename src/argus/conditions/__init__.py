"""Condition system: declarative predicates evaluated against the system under test."""

from argus.conditions.base import Condition, ConditionFactory
from argus.conditions.builtin import register

__all__ = ["Condition", "ConditionFactory", "register"]
