from __future__ import annotations

from argus_test_creator.adapters.android.keys import is_modifier, map_linux_key


def test_navigation_keys_map_to_argus_names():
    assert map_linux_key("KEY_BACK").argus_key == "BACK"
    assert map_linux_key("KEY_HOME").argus_key == "HOME"
    assert map_linux_key("KEY_MENU").argus_key == "MENU"
    assert map_linux_key("KEY_ENTER").argus_key == "ENTER"
    for direction in ("UP", "DOWN", "LEFT", "RIGHT"):
        assert map_linux_key(f"KEY_{direction}").argus_key == f"DPAD_{direction}"
    assert map_linux_key("KEY_SELECT").argus_key == "DPAD_CENTER"
    assert map_linux_key("KEY_BACK").android_keycode == "KEYCODE_BACK"
    assert map_linux_key("KEY_BACK").mapped is True


def test_unknown_keys_pass_through_never_dropped():
    mapped = map_linux_key("KEY_FROBNICATE")
    assert mapped is not None
    assert mapped.argus_key == "KEY_FROBNICATE"
    assert mapped.mapped is False


def test_touch_tool_buttons_and_modifiers_are_ignored():
    assert map_linux_key("BTN_TOUCH") is None
    assert map_linux_key("BTN_TOOL_FINGER") is None
    assert map_linux_key("KEY_LEFTSHIFT") is None
    assert is_modifier("KEY_LEFTCTRL")


def test_alphanumeric_physical_keyboard():
    assert map_linux_key("KEY_A").argus_key == "a"
    assert map_linux_key("KEY_7").argus_key == "7"
