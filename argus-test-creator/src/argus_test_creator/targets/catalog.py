"""Built-in target profiles.

``PLATFORM_CAPABILITIES`` is derived from each Argus adapter's
``DeviceCapabilities`` (what Argus can *drive*) intersected with what the
corresponding Creator recorder can *observe*. Recorder adapters refine these
at connect time (e.g. Roku screenshots require a dev password).
"""

from __future__ import annotations

from argus_test_creator.models.capabilities import RecorderCapabilities, TargetProfile

# What Argus can do on each platform (from argus.adapters.*.capabilities).
PLATFORM_CAPABILITIES: dict[str, RecorderCapabilities] = {
    "fake": RecorderCapabilities(
        supports_tap=True, supports_swipe=True, supports_long_press=True, supports_drag=True,
        supports_pinch=True, supports_multi_touch=True, supports_keyboard=True,
        supports_touch=True, supports_app_lifecycle=True, supports_screenshot=True,
        supports_live_screen=True, supports_ocr=True, supports_logs=True,
        supports_instrumentation=True, supports_backend=True, supports_playback_state=True,
        supports_input_recording=True,
    ),
    "web": RecorderCapabilities(
        supports_tap=True, supports_swipe=True, supports_long_press=True, supports_drag=True,
        supports_pinch=True, supports_multi_touch=True, supports_keyboard=True,
        supports_mouse=True, supports_app_lifecycle=True, supports_screenshot=True,
        supports_live_screen=True, supports_ocr=True, supports_element_metadata=True,
        supports_logs=True, supports_instrumentation=True, supports_input_recording=True,
        limitations=("Pinch/multi-touch run in Argus on chromium only and are not recorded.",),
    ),
    "desktop": RecorderCapabilities(
        supports_tap=True, supports_swipe=True, supports_long_press=True, supports_drag=True,
        supports_keyboard=True, supports_mouse=True, supports_app_lifecycle=True,
        supports_screenshot=True, supports_live_screen=True, supports_ocr=True,
        supports_logs=True, supports_input_recording=True,
        limitations=("No pinch or multi-touch on desktop.",
                     "Recording needs accessibility/input-monitoring permission on macOS."),
    ),
    "android": RecorderCapabilities(
        supports_tap=True, supports_swipe=True, supports_long_press=True, supports_drag=True,
        supports_pinch=True, supports_multi_touch=True, supports_keyboard=True,
        supports_touch=True, supports_app_lifecycle=True, supports_screenshot=True,
        supports_live_screen=True, supports_ocr=True, supports_logs=True,
        supports_instrumentation=True, supports_input_recording=True,
        limitations=("Gestures are observed via `adb shell getevent`; pinch/multi-touch are "
                     "recorded as raw touch paths.",),
    ),
    "ios": RecorderCapabilities(
        supports_tap=True, supports_swipe=True, supports_long_press=True, supports_drag=True,
        supports_pinch=True, supports_multi_touch=True, supports_keyboard=True,
        supports_touch=True, supports_app_lifecycle=True, supports_screenshot=True,
        supports_ocr=True, supports_logs=True,
        limitations=("iOS input cannot be observed; author steps manually against screenshots.",),
    ),
    "roku": RecorderCapabilities(
        supports_keyboard=True, supports_app_lifecycle=True, supports_screenshot=True,
        supports_ocr=True, supports_logs=True, supports_instrumentation=True,
        limitations=("Roku accepts remote keys only (no tap/swipe).",
                     "Screenshots require the developer password.",
                     "Remote presses are not observable; use the Creator's remote to send keys."),
    ),
    "tvos": RecorderCapabilities(
        supports_keyboard=True, supports_app_lifecycle=True, supports_screenshot=True,
        supports_ocr=True, supports_logs=True, supports_instrumentation=True,
        limitations=("Simulator only: remote keys, no touch.",),
    ),
    "appletv": RecorderCapabilities(
        supports_keyboard=True, supports_app_lifecycle=True, supports_instrumentation=True,
        supports_playback_state=True,
        limitations=("Physical Apple TV has no screenshots — visual assertions unavailable.",),
    ),
    "esp32": RecorderCapabilities(
        supports_keyboard=True, supports_app_lifecycle=True, supports_screenshot=True,
        supports_logs=True, supports_instrumentation=True,
        limitations=("Framebuffer screenshots and key input depend on the firmware agent.",),
    ),
    "yocto": RecorderCapabilities(
        supports_app_lifecycle=True, supports_screenshot=True, supports_ocr=True,
        supports_logs=True, supports_instrumentation=True,
        limitations=("Input is not available over SSH; drive state through the backend.",),
    ),
}


def builtin_targets() -> list[TargetProfile]:
    """Targets available without any configuration."""
    return [
        TargetProfile(
            id="fake-movies",
            name="Movies Demo (fake target)",
            adapter="fake",
            platform="fake",
            argus_device_type="fake",
            argus_device_name="demo",
            capabilities=PLATFORM_CAPABILITIES["fake"],
            settings={"scenario": "movies", "screen_size": [1280, 720]},
            description="Built-in demo application. No hardware needed.",
        ),
        TargetProfile(
            id="browser-chromium",
            name="Web browser (chromium)",
            adapter="browser",
            platform="web",
            argus_device_type="browser",
            argus_device_name="web",
            argus_device_options={"browser": "chromium", "viewport": [1280, 720]},
            capabilities=PLATFORM_CAPABILITIES["web"],
            settings={"browser": "chromium", "url": "", "viewport": [1280, 720],
                      "headless": False},
            description="Records a web application through Playwright.",
        ),
        TargetProfile(
            id="desktop",
            name="Desktop application",
            adapter="desktop",
            platform="desktop",
            argus_device_type="desktop",
            argus_device_name="desktop",
            capabilities=PLATFORM_CAPABILITIES["desktop"],
            settings={"command": "", "monitor": 1},
            description="Records mouse/keyboard against a native application.",
        ),
        TargetProfile(
            id="android",
            name="Android device (ADB)",
            adapter="android",
            platform="android",
            argus_device_type="android",
            argus_device_name="android",
            capabilities=PLATFORM_CAPABILITIES["android"],
            settings={"serial": "", "app_package": "", "app_activity": "", "adb_path": "adb",
                      "input_device": "", "invert_x": False, "invert_y": False,
                      "swap_axes": False},
            description="Records touches and keys on an Android device through ADB "
                        "(`getevent`); the live view and remote can also send input.",
        ),
    ]


class TargetCatalog:
    """Registry of target profiles (built-ins plus project/user configured ones)."""

    def __init__(self, targets: list[TargetProfile] | None = None) -> None:
        self._targets: dict[str, TargetProfile] = {}
        for target in targets if targets is not None else builtin_targets():
            self.add(target)

    def add(self, target: TargetProfile) -> None:
        self._targets[target.id] = target

    def get(self, target_id: str) -> TargetProfile | None:
        return self._targets.get(target_id)

    def all(self) -> list[TargetProfile]:
        return list(self._targets.values())

    def for_platform(self, platform: str) -> list[TargetProfile]:
        return [t for t in self._targets.values() if t.platform == platform]
