"""Linux input key names → Argus key names.

``getevent -l`` reports Linux ``KEY_*`` names. Argus's Android adapter takes
semantic keys (``BACK``, ``HOME``, ``DPAD_UP``...). Keys without a semantic
mapping are *not* dropped: they pass through as ``KEY_<NAME>`` so the user
sees them and can edit or remove the step.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Linux key name → Argus key. Order/values mirror Android ``KEYCODE_*`` names.
KEY_MAP: dict[str, str] = {
    "KEY_BACK": "BACK",
    "KEY_HOME": "HOME",
    "KEY_HOMEPAGE": "HOME",
    "KEY_MENU": "MENU",
    "KEY_ENTER": "ENTER",
    "KEY_KPENTER": "ENTER",
    "KEY_UP": "DPAD_UP",
    "KEY_DOWN": "DPAD_DOWN",
    "KEY_LEFT": "DPAD_LEFT",
    "KEY_RIGHT": "DPAD_RIGHT",
    "KEY_SELECT": "DPAD_CENTER",
    "KEY_OK": "DPAD_CENTER",
    "BTN_DPAD_UP": "DPAD_UP",
    "BTN_DPAD_DOWN": "DPAD_DOWN",
    "BTN_DPAD_LEFT": "DPAD_LEFT",
    "BTN_DPAD_RIGHT": "DPAD_RIGHT",
    "KEY_TAB": "TAB",
    "KEY_SPACE": "SPACE",
    "KEY_BACKSPACE": "BACKSPACE",
    "KEY_DELETE": "DEL",
    "KEY_ESC": "BACK",
    "KEY_VOLUMEUP": "VOLUME_UP",
    "KEY_VOLUMEDOWN": "VOLUME_DOWN",
    "KEY_MUTE": "VOLUME_MUTE",
    "KEY_POWER": "POWER",
    "KEY_CAMERA": "CAMERA",
    "KEY_SEARCH": "SEARCH",
    "KEY_PLAYPAUSE": "MEDIA_PLAY_PAUSE",
    "KEY_PLAY": "MEDIA_PLAY",
    "KEY_PAUSE": "MEDIA_PAUSE",
    "KEY_NEXTSONG": "MEDIA_NEXT",
    "KEY_PREVIOUSSONG": "MEDIA_PREVIOUS",
    "KEY_APPSELECT": "APP_SWITCH",
    "KEY_PAGEUP": "PAGE_UP",
    "KEY_PAGEDOWN": "PAGE_DOWN",
    "KEY_END": "MOVE_END",
}

#: Keys that are meaningful to *observe* but noisy when typed (touch buttons,
#: tool types, wake-up chatter). They are ignored by the recognizer.
IGNORED_KEYS: frozenset[str] = frozenset({
    "BTN_TOUCH", "BTN_TOOL_FINGER", "BTN_TOOL_PEN", "BTN_TOOL_DOUBLETAP", "BTN_TOOL_TRIPLETAP",
    "BTN_TOOL_QUADTAP", "BTN_STYLUS", "BTN_STYLUS2", "KEY_WAKEUP",
})

_MODIFIERS = {"KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_LEFTCTRL", "KEY_RIGHTCTRL",
              "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_LEFTMETA", "KEY_RIGHTMETA"}


@dataclass(frozen=True)
class MappedKey:
    argus_key: str
    linux_key: str
    mapped: bool
    android_keycode: str


def map_linux_key(linux_name: str) -> MappedKey | None:
    """Return the Argus key for a Linux key name, or ``None`` for ignored keys."""
    if linux_name in IGNORED_KEYS:
        return None
    argus = KEY_MAP.get(linux_name)
    if argus is not None:
        return MappedKey(argus, linux_name, True, f"KEYCODE_{argus}")
    if linux_name in _MODIFIERS:
        return None
    name = linux_name.removeprefix("KEY_")
    if len(name) == 1 and name.isalnum():
        # Physical keyboards: KEY_A → "a" (Argus types single characters as keys).
        return MappedKey(name.lower(), linux_name, True, f"KEYCODE_{name}")
    if name.isdigit():
        return MappedKey(name, linux_name, True, f"KEYCODE_{name}")
    return MappedKey(f"KEY_{name}" if not linux_name.startswith("KEY_") else linux_name,
                     linux_name, False, f"KEYCODE_{name}")


def is_modifier(linux_name: str) -> bool:
    return linux_name in _MODIFIERS


__all__ = ["IGNORED_KEYS", "KEY_MAP", "MappedKey", "is_modifier", "map_linux_key"]
