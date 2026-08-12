"""Condition system: leaves, composition, wait_until."""

import pytest
from tests.conftest import make_context, make_screen

from argus.adapters.fake import FakeBackend, FakeDevice, FakeInstrumentation
from argus.engine.wait import wait_until
from argus.exceptions import ConditionError
from argus.models.test_definition import ConditionSpec
from argus.ocr.fake import FakeOCRProvider


def build(context, spec_dict):
    return context.conditions.build(ConditionSpec.model_validate(spec_dict), context)


@pytest.fixture
def context(base_config, artwork_a):
    device = FakeDevice(screenshots=[make_screen(artwork_a)])
    return make_context(
        base_config,
        device=device,
        backend=FakeBackend({"movieId": 123}),
        instrumentation=FakeInstrumentation(
            status={"ready": True, "screen": "movie_details", "capabilities": []},
            state={"player": {"state": "playing"}},
        ),
    )


class TestLeafConditions:
    def test_image_present(self, context):
        condition = build(context, {"type": "image_present", "image": "movie_123.png"})
        assert condition.needs_observation
        result = condition.evaluate(context, context.observe())
        assert result.passed

    def test_image_not_present(self, context):
        condition = build(context, {"type": "image_not_present", "image": "movie_456.png"})
        assert condition.evaluate(context, context.observe()).passed

    def test_captures_observation_when_none_given(self, context):
        condition = build(context, {"type": "image_present", "image": "movie_123.png"})
        assert condition.evaluate(context, None).passed

    def test_instrumentation_value(self, context):
        condition = build(
            context, {"type": "instrumentation_value", "key": "ready", "equals": True}
        )
        assert not condition.needs_observation
        assert condition.evaluate(context, None).passed

    def test_instrumentation_value_mismatch(self, context):
        condition = build(
            context, {"type": "instrumentation_value", "key": "screen", "equals": "home"}
        )
        assert not condition.evaluate(context, None).passed

    def test_application_state_dotted_key(self, context):
        condition = build(
            context,
            {"type": "application_state", "key": "player.state", "equals": "playing"},
        )
        assert condition.evaluate(context, None).passed

    def test_backend_value(self, context):
        condition = build(context, {"type": "backend_value", "key": "movieId", "equals": 123})
        assert condition.evaluate(context, None).passed

    def test_pixel_matches(self, context, artwork_a):
        condition = build(
            context,
            {"type": "pixel_matches", "x": 5, "y": 5, "color": "#101018", "tolerance": 5},
        )
        assert condition.evaluate(context, context.observe()).passed

    def test_text_present_with_fake_ocr(self, context):
        context.verifiers._ocr = FakeOCRProvider("Star Wars Episode IV")
        condition = build(context, {"type": "text_present", "text": "star wars"})
        assert condition.evaluate(context, context.observe()).passed
        condition = build(context, {"type": "text_not_present", "text": "Matrix"})
        assert condition.evaluate(context, context.observe()).passed

    def test_unknown_type_rejected(self, context):
        with pytest.raises(ConditionError, match="Unknown condition type"):
            build(context, {"type": "does_not_exist"})

    def test_named_region_resolution(self, context, base_config):
        from argus.models.common import Region

        base_config.regions["artwork"] = Region(x=40, y=30, width=150, height=150)
        condition = build(
            context,
            {"type": "image_present", "image": "movie_123.png", "region": "artwork"},
        )
        assert condition.evaluate(context, context.observe()).passed

    def test_unknown_named_region_rejected(self, context):
        with pytest.raises(ConditionError, match="Unknown named region"):
            build(
                context,
                {"type": "image_present", "image": "movie_123.png", "region": "nope"},
            )


class TestComposition:
    def test_all(self, context):
        condition = build(
            context,
            {
                "all": [
                    {"type": "image_present", "image": "movie_123.png"},
                    {"type": "instrumentation_value", "key": "ready", "equals": True},
                ]
            },
        )
        assert condition.needs_observation
        assert condition.evaluate(context, context.observe()).passed

    def test_all_fails_when_one_fails(self, context):
        condition = build(
            context,
            {
                "all": [
                    {"type": "image_present", "image": "movie_123.png"},
                    {"type": "image_present", "image": "movie_456.png"},
                ]
            },
        )
        assert not condition.evaluate(context, context.observe()).passed

    def test_any(self, context):
        condition = build(
            context,
            {
                "any": [
                    {"type": "image_present", "image": "movie_456.png"},
                    {"type": "image_present", "image": "movie_123.png"},
                ]
            },
        )
        assert condition.evaluate(context, context.observe()).passed

    def test_not(self, context):
        condition = build(
            context, {"not": {"type": "image_present", "image": "movie_456.png"}}
        )
        assert condition.evaluate(context, context.observe()).passed

    def test_spec_requires_exactly_one_form(self):
        with pytest.raises(ValueError):
            ConditionSpec.model_validate(
                {"type": "image_present", "all": [{"type": "image_present"}]}
            )
        with pytest.raises(ValueError):
            ConditionSpec.model_validate({})


class TestWaitUntil:
    def test_immediate_success_single_check(self, context):
        condition = build(context, {"type": "image_present", "image": "movie_123.png"})
        outcome = wait_until(context, condition, timeout=5.0, poll_interval=0.05)
        assert outcome.passed
        assert outcome.attempts == 1

    def test_timeout(self, context):
        condition = build(context, {"type": "image_present", "image": "movie_456.png"})
        outcome = wait_until(context, condition, timeout=0.3, poll_interval=0.05)
        assert not outcome.passed
        assert outcome.attempts >= 2
        assert outcome.elapsed >= 0.3

    def test_succeeds_when_screen_changes(self, base_config, artwork_a, artwork_b):
        device = FakeDevice(
            screenshots=[make_screen(artwork_b), make_screen(artwork_b), make_screen(artwork_a)]
        )
        context = make_context(base_config, device=device)
        condition = build(context, {"type": "image_present", "image": "movie_123.png"})
        outcome = wait_until(context, condition, timeout=5.0, poll_interval=0.01)
        assert outcome.passed
        assert outcome.attempts == 3
