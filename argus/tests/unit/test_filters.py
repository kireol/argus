"""Test selection filters and tag expressions."""

import pytest
from tests.conftest import make_test

from argus.engine.filters import TestFilter, _evaluate_tag_expression
from argus.exceptions import TestDefinitionError


@pytest.fixture
def tests():
    return [
        make_test(id="MOV-001", feature="Movies", tags=["smoke", "visual"], platforms=["android"]),
        make_test(id="MOV-002", feature="Movies", tags=["visual"], platforms=["yocto"]),
        make_test(id="PLY-001", feature="Playback", tags=["smoke"], platforms=["android", "yocto"]),
    ]


def ids(selected):
    return [t.id for t in selected]


def test_no_filter_selects_all(tests):
    assert len(TestFilter().apply(tests)) == 3


def test_filter_by_id(tests):
    assert ids(TestFilter(test_ids=["MOV-002"]).apply(tests)) == ["MOV-002"]


def test_filter_by_feature_case_insensitive(tests):
    assert ids(TestFilter(features=["movies"]).apply(tests)) == ["MOV-001", "MOV-002"]


def test_filter_by_tag(tests):
    assert ids(TestFilter(tags=["smoke"]).apply(tests)) == ["MOV-001", "PLY-001"]


def test_multiple_tags_are_anded(tests):
    assert ids(TestFilter(tags=["smoke", "visual"]).apply(tests)) == ["MOV-001"]


def test_filter_by_platform(tests):
    assert ids(TestFilter(platforms=["yocto"]).apply(tests)) == ["MOV-002", "PLY-001"]


def test_combined_filters(tests):
    selected = TestFilter(platforms=["android"], features=["Movies"], tags=["smoke"]).apply(tests)
    assert ids(selected) == ["MOV-001"]


def test_tag_expression(tests):
    assert ids(TestFilter(tag_expression="smoke and visual").apply(tests)) == ["MOV-001"]
    assert ids(TestFilter(tag_expression="smoke and not visual").apply(tests)) == ["PLY-001"]
    assert ids(TestFilter(tag_expression="smoke or visual").apply(tests)) == [
        "MOV-001",
        "MOV-002",
        "PLY-001",
    ]


class TestTagExpressionParser:
    def test_single_tag(self):
        assert _evaluate_tag_expression("smoke", {"smoke"}) is True
        assert _evaluate_tag_expression("smoke", {"visual"}) is False

    def test_parentheses(self):
        assert _evaluate_tag_expression("(a or b) and c", {"b", "c"}) is True
        assert _evaluate_tag_expression("(a or b) and c", {"b"}) is False

    def test_not(self):
        assert _evaluate_tag_expression("not slow", {"smoke"}) is True
        assert _evaluate_tag_expression("not not slow", {"slow"}) is True

    @pytest.mark.parametrize("expression", ["and", "a and", "(a", "a )", "a b"])
    def test_invalid(self, expression):
        with pytest.raises(TestDefinitionError):
            _evaluate_tag_expression(expression, {"a", "b"})
