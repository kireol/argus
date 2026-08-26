"""Duration parsing and variable expansion."""

import pytest

from argus.exceptions import ConfigurationError
from argus.utilities.duration import format_duration, parse_duration
from argus.utilities.variables import expand_variables


class TestParseDuration:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("10s", 10.0),
            ("250ms", 0.25),
            ("2m", 120.0),
            ("1h", 3600.0),
            ("1.5s", 1.5),
            ("5", 5.0),
            (5, 5.0),
            (0.5, 0.5),
        ],
    )
    def test_valid(self, value, expected):
        assert parse_duration(value) == expected

    @pytest.mark.parametrize("value", ["", "abc", "10x", "-5s", -1])
    def test_invalid(self, value):
        with pytest.raises(ConfigurationError):
            parse_duration(value)

    def test_format(self):
        assert format_duration(1.42) == "1.42s"
        assert format_duration(0.25) == "250ms"
        assert format_duration(90) == "1m30.0s"


class TestExpandVariables:
    def test_full_reference_preserves_type(self):
        assert expand_variables("${movie_id}", {"movie_id": 123}) == 123

    def test_interpolation(self):
        result = expand_variables("movie_${movie_id}.png", {"movie_id": 123})
        assert result == "movie_123.png"

    def test_nested_structures(self):
        value = {"data": {"movieId": "${movie_id}"}, "list": ["${name}", "x"]}
        result = expand_variables(value, {"movie_id": 7, "name": "n"})
        assert result == {"data": {"movieId": 7}, "list": ["n", "x"]}

    def test_default_value(self):
        assert expand_variables("${missing:-fallback}", {}) == "fallback"

    def test_strict_raises_on_unresolved(self):
        with pytest.raises(ConfigurationError, match="missing"):
            expand_variables("${missing}", {}, strict=True)

    def test_non_strict_keeps_literal(self):
        assert expand_variables("${missing}", {}, strict=False) == "${missing}"

    def test_non_string_passthrough(self):
        assert expand_variables(42, {}) == 42
        assert expand_variables(None, {}) is None
