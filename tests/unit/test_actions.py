"""Built-in action behavior."""

import pytest
from tests.conftest import make_context, make_screen

from argus.actions.base import ActionRegistry
from argus.adapters.fake import FakeBackend, FakeDevice
from argus.exceptions import ActionError


@pytest.fixture
def registry():
    return ActionRegistry()


@pytest.fixture
def context(base_config, artwork_a, tmp_path):
    return make_context(
        base_config,
        device=FakeDevice(screenshots=[make_screen(artwork_a)]),
        backend=FakeBackend(),
        artifact_dir=tmp_path / "artifacts",
    )


def test_unknown_action_rejected(registry):
    with pytest.raises(ActionError, match="Unknown action"):
        registry.get("does.not.exist")


def test_registry_lists_builtin_actions(registry):
    names = registry.names()
    for expected in (
        "backend.get",
        "backend.set",
        "device.start",
        "device.tap",
        "wait",
        "wait_until",
        "verify",
        "screenshot",
        "log",
    ):
        assert expected in names


def test_backend_set_updates_state(registry, context):
    action = registry.get("backend.set")
    result = action.execute(context, {"data": {"movieId": 123}})
    assert result.passed
    assert context.backend.state == {"movieId": 123}


def test_backend_set_requires_data(registry, context):
    with pytest.raises(ActionError, match="data"):
        registry.get("backend.set").execute(context, {})


def test_device_input_actions(registry, context):
    registry.get("device.tap").execute(context, {"x": 10, "y": 20})
    registry.get("device.swipe").execute(
        context, {"from_x": 0, "from_y": 0, "to_x": 100, "to_y": 100}
    )
    registry.get("device.key").execute(context, {"key": "HOME"})
    device = context.device
    assert device.taps == [(10, 20)]
    assert device.swipes == [(0, 0, 100, 100)]
    assert device.keys == ["HOME"]


def test_device_lifecycle(registry, context):
    registry.get("device.start").execute(context, {})
    assert context.device.app_running
    registry.get("device.stop").execute(context, {})
    assert not context.device.app_running


def test_verify_pass_and_fail(registry, context):
    verify = registry.get("verify")
    passing = verify.execute(
        context, {"condition": {"type": "image_present", "image": "movie_123.png"}}
    )
    assert passing.passed
    assert passing.verification is not None
    failing = verify.execute(
        context, {"condition": {"type": "image_present", "image": "movie_456.png"}}
    )
    assert not failing.passed
    assert failing.failure_category == "assertion"


def test_wait_until_timeout_category(registry, context):
    result = registry.get("wait_until").execute(
        context,
        {
            "condition": {"type": "image_present", "image": "movie_456.png"},
            "timeout": "200ms",
            "poll_interval": "50ms",
        },
    )
    assert not result.passed
    assert result.failure_category == "timeout"


def test_screenshot_saves_artifact(registry, context, tmp_path):
    result = registry.get("screenshot").execute(context, {"file": "shot"})
    assert result.passed
    assert (tmp_path / "artifacts" / "shot.png").exists()


def test_actions_without_device_fail_cleanly(registry, base_config):
    context = make_context(base_config, device=None)
    from argus.exceptions import TestExecutionError

    with pytest.raises(TestExecutionError, match="needs a device"):
        registry.get("device.tap").execute(context, {"x": 1, "y": 1})


def test_actions_without_backend_fail_cleanly(registry, base_config):
    context = make_context(base_config, backend=None)
    from argus.exceptions import TestExecutionError

    with pytest.raises(TestExecutionError, match="backend"):
        registry.get("backend.set").execute(context, {"data": {}})
