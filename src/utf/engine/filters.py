"""Test selection filters.

Supports exact filters (id, feature, tag, platform) and simple boolean tag
expressions such as ``"smoke and movies"`` or ``"smoke and not slow"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from utf.exceptions import TestDefinitionError
from utf.models.test_definition import TestDefinition

_TOKEN_RE = re.compile(r"\(|\)|\band\b|\bor\b|\bnot\b|[A-Za-z0-9_.-]+")


@dataclass
class TestFilter:
    """Declarative test selection criteria; criteria combine with AND."""

    test_ids: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    tag_expression: str | None = None

    def matches(self, test: TestDefinition) -> bool:
        if self.test_ids and test.id not in self.test_ids:
            return False
        if self.features and test.feature.lower() not in [f.lower() for f in self.features]:
            return False
        if self.tags and not all(tag in test.tags for tag in self.tags):
            return False
        if self.platforms and not any(p in test.platforms for p in self.platforms):
            return False
        if self.tag_expression and not _evaluate_tag_expression(
            self.tag_expression, set(test.tags)
        ):
            return False
        return True

    def apply(self, tests: list[TestDefinition]) -> list[TestDefinition]:
        return [t for t in tests if self.matches(t)]

    def describe(self) -> dict[str, object]:
        described: dict[str, object] = {}
        if self.test_ids:
            described["tests"] = self.test_ids
        if self.features:
            described["features"] = self.features
        if self.tags:
            described["tags"] = self.tags
        if self.platforms:
            described["platforms"] = self.platforms
        if self.tag_expression:
            described["tag_expression"] = self.tag_expression
        return described


class _ExpressionParser:
    """Recursive-descent parser for boolean tag expressions.

    Grammar: expr := term ('or' term)*
             term := factor ('and' factor)*
             factor := 'not' factor | '(' expr ')' | TAG
    """

    def __init__(self, tokens: list[str], tags: set[str], expression: str) -> None:
        self._tokens = tokens
        self._tags = tags
        self._expression = expression
        self._pos = 0

    def parse(self) -> bool:
        result = self._expr()
        if self._pos != len(self._tokens):
            raise self._error()
        return result

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> str:
        token = self._peek()
        if token is None:
            raise self._error()
        self._pos += 1
        return token

    def _expr(self) -> bool:
        value = self._term()
        while self._peek() == "or":
            self._next()
            value = self._term() or value
        return value

    def _term(self) -> bool:
        value = self._factor()
        while self._peek() == "and":
            self._next()
            value = self._factor() and value
        return value

    def _factor(self) -> bool:
        token = self._next()
        if token == "not":
            return not self._factor()
        if token == "(":
            value = self._expr()
            if self._next() != ")":
                raise self._error()
            return value
        if token in {")", "and", "or"}:
            raise self._error()
        return token in self._tags

    def _error(self) -> TestDefinitionError:
        return TestDefinitionError(
            f"Invalid tag expression: {self._expression!r}",
            remediation='Use tags with "and", "or", "not", e.g. "smoke and movies".',
        )


def _evaluate_tag_expression(expression: str, tags: set[str]) -> bool:
    """Evaluate a boolean tag expression against a tag set (no eval involved)."""
    tokens = _TOKEN_RE.findall(expression)
    if not tokens:
        raise TestDefinitionError(f"Empty tag expression: {expression!r}")
    return _ExpressionParser(tokens, tags, expression).parse()
