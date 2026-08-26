"""Android recorder (ADB + ``getevent``).

Public surface: the recorder itself, the ADB boundary, the fake ADB for tests,
and the pure building blocks (parser, touch state, coordinates, gestures).
"""

from argus_test_creator.adapters.android.adb import (
    AdbClient,
    AdbProcess,
    EventStream,
    SubprocessAdbClient,
)
from argus_test_creator.adapters.android.coordinates import AndroidCoordinateMapper
from argus_test_creator.adapters.android.diagnostics import (
    AndroidRecordingDiagnostics,
    DiagnosticsSnapshot,
)
from argus_test_creator.adapters.android.fake_adb import FakeAdbClient, FakeDevice
from argus_test_creator.adapters.android.gestures import AndroidGestureRecognizer, GestureConfig
from argus_test_creator.adapters.android.getevent_parser import GetEventParser, parse_input_devices
from argus_test_creator.adapters.android.recorder import (
    AndroidRecorder,
    register,
    select_touchscreen,
)
from argus_test_creator.adapters.android.touch_state import TouchState

__all__ = [
    "AdbClient",
    "AdbProcess",
    "AndroidCoordinateMapper",
    "AndroidGestureRecognizer",
    "AndroidRecorder",
    "AndroidRecordingDiagnostics",
    "DiagnosticsSnapshot",
    "EventStream",
    "FakeAdbClient",
    "FakeDevice",
    "GestureConfig",
    "GetEventParser",
    "SubprocessAdbClient",
    "TouchState",
    "parse_input_devices",
    "register",
    "select_touchscreen",
]
