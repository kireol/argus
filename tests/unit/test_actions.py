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
        "shell.run",
    ):
        assert expected in names


def test_shell_run_with_args(registry, context, tmp_path):
    marker = tmp_path / "shell_ran.txt"
    result = registry.get("shell.run").execute(
        context,
        {
            "command": "python3",
            "args": ["-c", f"open({str(marker)!r}, 'w').write('ok')"],
            "timeout": "10s",
        },
    )
    assert result.passed, result.message
    assert marker.read_text() == "ok"
    assert result.details.get("exit_code") == 0
    assert "stdout" in result.details
    assert "stderr" in result.details


def test_shell_run_nonzero_exit_fails(registry, context):
    result = registry.get("shell.run").execute(
        context,
        {"command": "python3", "args": ["-c", "raise SystemExit(3)"], "timeout": "10s"},
    )
    assert not result.passed
    assert result.failure_category == "backend"
    assert result.details.get("exit_code") == 3


def test_shell_run_expands_config_variables_into_env(registry, context):
    context.variables["MARKER_VALUE"] = "from-config"
    result = registry.get("shell.run").execute(
        context,
        {
            "command": "python3",
            "args": ["-c", "import os; print(os.environ['MARKER_VALUE'])"],
            "timeout": "10s",
        },
    )
    assert result.passed, result.message
    assert "from-config" in result.details.get("stdout", "")


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


def test_verify_reuses_matching_wait_until(registry, base_config, artwork_a, tmp_path):
    """verify after wait_until with the same condition skips a second capture."""
    device = FakeDevice(screenshots=[make_screen(artwork_a)])
    context = make_context(
        base_config,
        device=device,
        backend=FakeBackend(),
        artifact_dir=tmp_path / "artifacts",
    )
    condition = {"type": "image_present", "image": "movie_123.png"}
    wait = registry.get("wait_until").execute(
        context,
        {"condition": condition, "timeout": "2s", "poll_interval": "50ms"},
    )
    assert wait.passed
    shots_after_wait = device.screenshot_count
    verify = registry.get("verify").execute(context, {"condition": condition})
    assert verify.passed
    assert verify.details.get("reused_wait_until") is True
    assert device.screenshot_count == shots_after_wait


def test_verify_does_not_reuse_after_intervening_step(
    registry, base_config, artwork_a, tmp_path
):
    device = FakeDevice(
        screenshots=[make_screen(artwork_a), make_screen(artwork_a)]
    )
    context = make_context(
        base_config,
        device=device,
        backend=FakeBackend(),
        artifact_dir=tmp_path / "artifacts",
    )
    condition = {"type": "image_present", "image": "movie_123.png"}
    assert registry.get("wait_until").execute(
        context,
        {"condition": condition, "timeout": "2s", "poll_interval": "50ms"},
    ).passed
    # Simulate runner clearing the reuse marker when another action runs.
    context.state.pop("_reuse_wait_verify", None)
    verify = registry.get("verify").execute(context, {"condition": condition})
    assert verify.passed
    assert not verify.details.get("reused_wait_until")


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
