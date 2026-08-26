# Argus Test Creator — Android `getevent` Recording Implementation Prompt

## Objective

Add Android recording capability to the existing `argus-test-creator` application using Android's native `adb shell getevent` as the primary low-level input event source.

The goal is to let a user connect an Android device, start recording, interact normally with the device, and have the Creator convert raw Android input events into clean, maintainable Argus test actions.

The user should never need to see raw `getevent` output.

```text
Android device
      ↓
ADB
      ↓
adb shell getevent
      ↓
Android Recorder
      ↓
Raw Event Parser
      ↓
Gesture Normalizer
      ↓
Existing Authoring Model
      ↓
Argus Test YAML
```

---

## 1. Inspect the existing project first

Before modifying anything:

1. Thoroughly inspect the current `argus-test-creator` repository.
2. Understand the existing recorder abstraction.
3. Understand `RecordingSession`, `RecordingEvent`, normalization, screenshots, target/capability discovery, step generation, UI, and tests.
4. Find any existing Android/ADB integration and reuse it where appropriate.
5. Do not create a parallel recording architecture.

Extend the existing architecture.

---

## 2. Core requirements

Implement an Android recorder using:

```bash
adb shell getevent
```

It must recognize at least:

- tap
- swipe
- long press
- basic multi-touch
- useful Android hardware/navigation keys

Low-level events must be converted into semantic interactions.

The architecture must permit future gesture recognition without rewriting the parser.

---

## 3. `getevent` invocation

Investigate the actual `getevent` capabilities on supported devices.

Prefer a robust streaming form such as:

```bash
adb shell getevent -lt
```

and investigate:

```bash
adb shell getevent --help
```

Use a device/input-device-specific command where appropriate.

Do not assume one exact output format without testing it.

If textual output is used, implement a robust parser rather than brittle string splitting.

---

## 4. Android device discovery

Use:

```bash
adb devices
```

Identify:

- serial
- state
- model where available
- Android version
- screen resolution
- input devices

If multiple devices are connected, require explicit selection.

Never silently use the first device.

Every subsequent ADB operation must use the selected serial:

```bash
adb -s SERIAL ...
```

---

## 5. Touchscreen discovery

Use something like:

```bash
adb shell getevent -lp
```

to discover input devices.

Never hardcode `/dev/input/eventN`.

Identify likely touchscreen devices from capabilities such as:

- `EV_ABS`
- `ABS_MT_POSITION_X`
- `ABS_MT_POSITION_Y`
- `ABS_X`
- `ABS_Y`

Create an abstraction similar to:

```text
AndroidInputDevice
    path
    name
    capabilities
    is_touchscreen
    axis_ranges
```

If multiple candidate touchscreens exist, handle that explicitly.

---

## 6. Coordinate mapping

Raw Linux input coordinates may differ from Android screen coordinates.

For example:

```text
raw:   0 → 4095
screen: 0 → 1080
```

Create a dedicated, testable:

```text
AndroidCoordinateMapper
```

It must account for, where applicable:

- axis ranges
- scaling
- inversion
- portrait/landscape
- display rotation
- screen dimensions

Do not assume a single device's behavior applies to all Android devices.

---

## 7. Raw event model

Create strongly typed models rather than passing dictionaries throughout the application.

For example:

```text
AndroidRawInputEvent
    timestamp
    device
    event_type
    code
    value
```

Represent relevant event types/codes cleanly, including:

```text
EV_SYN
EV_KEY
EV_ABS
ABS_X
ABS_Y
ABS_MT_SLOT
ABS_MT_TRACKING_ID
ABS_MT_POSITION_X
ABS_MT_POSITION_Y
```

Unknown events must be safely ignored or preserved as raw metadata.

---

## 8. Parser separation

Implement:

```text
GetEventParser
```

with no UI dependency.

Pipeline:

```text
getevent stream
      ↓
GetEventParser
      ↓
AndroidRawInputEvent
```

Do not mix parsing with gesture recognition.

---

## 9. Multi-touch slot state

Modern Android touchscreens commonly use:

```text
ABS_MT_SLOT
ABS_MT_TRACKING_ID
ABS_MT_POSITION_X
ABS_MT_POSITION_Y
```

Implement proper slot tracking.

Conceptually:

```text
TouchSlot
    slot_id
    tracking_id
    x
    y
    active
    start_time
    last_time
```

and:

```text
TouchState
    slots
```

Interpret:

```text
TRACKING_ID >= 0
```

as touch start and:

```text
TRACKING_ID == -1
```

as touch end.

Use `SYN_REPORT` appropriately as a frame boundary.

Do not assume every event belongs to finger 0.

---

## 10. Gesture recognizer

Create a separate:

```text
AndroidGestureRecognizer
```

Pipeline:

```text
AndroidRawInputEvent
      ↓
AndroidGestureRecognizer
      ↓
RecognizedGesture
```

Potential outputs:

```text
Tap
Swipe
LongPress
MultiTouch
KeyPress
Unknown
```

Do not put gesture logic inside the parser.

---

## 11. Tap recognition

A tap requires:

1. touch down
2. movement below configurable threshold
3. touch up
4. duration below long-press threshold

Make thresholds configurable, for example:

```text
tap_max_duration = 500ms
tap_max_distance = 20px
```

Do not scatter constants throughout the code.

Produce a semantic event such as:

```text
Tap(x, y, duration, timestamp)
```

---

## 12. Swipe recognition

Recognize a swipe when:

```text
touch down
    ↓
movement exceeds threshold
    ↓
touch up
```

Produce:

```text
Swipe(
    start_x,
    start_y,
    end_x,
    end_y,
    duration
)
```

Do not emit every intermediate coordinate as a test step.

Hundreds of raw events should become one semantic swipe.

---

## 13. Long press

Recognize:

```text
touch down
    ↓
little/no movement
    ↓
duration exceeds threshold
    ↓
touch up
```

as:

```text
LongPress(x, y, duration)
```

Do not represent a long press as `tap + wait`.

Use the current Argus semantic action if one exists.

---

## 14. Multi-touch

V1 must:

1. detect simultaneous active touch slots
2. preserve their trajectories
3. emit a structured multi-touch event
4. never turn two simultaneous touches into two independent taps

If the current Argus schema has a multi-touch action, use it.

Otherwise preserve the event in the authoring model so it can be mapped later.

Do not implement complex pinch/rotate recognition unless it naturally fits the existing architecture.

---

## 15. Android key events

Investigate `EV_KEY`.

Support useful keys where practical:

- BACK
- HOME
- MENU
- ENTER
- DPAD_UP
- DPAD_DOWN
- DPAD_LEFT
- DPAD_RIGHT
- DPAD_CENTER

Create a mapping:

```text
Linux key code
      ↓
Android key
      ↓
Argus key action
```

Do not expose Linux codes in final YAML if the existing Argus schema has semantic keys.

Unknown keys must not be silently discarded.

---

## 16. Long-running process management

`getevent` is a long-running process.

Implement:

- start
- stop
- cancellation
- timeout
- unexpected exit handling
- stderr capture
- clean shutdown
- no zombie processes
- no orphaned ADB shell processes

Do not use `subprocess.run()` for the live stream.

Use an incremental streaming subprocess implementation.

---

## 17. ADB abstraction

Do not scatter ADB subprocess calls throughout the code.

Use or extend a clean boundary such as:

```text
AdbClient
    list_devices()
    shell()
    get_device_info()
    get_input_devices()
    start_getevent()
    screenshot()
```

Fit this into the existing project architecture instead of blindly creating duplicate services.

---

## 18. Fake ADB

Create a fake ADB implementation for tests.

Provide fixtures such as:

```text
tap_event.txt
swipe_event.txt
long_press_event.txt
multitouch_event.txt
key_event.txt
```

The parser and gesture recognizer must be fully testable without hardware.

---

## 19. Screenshot integration

Integrate with the existing observation/screenshot system.

Prefer:

```text
semantic action detected
        ↓
capture/associate evidence
        ↓
authoring event
```

Do not capture a full-resolution screenshot for every raw event.

Where practical, associate before/after screenshots with semantic actions.

Keep screenshot work asynchronous.

---

## 20. UI integration

When Android is selected, show relevant information:

```text
Android Recorder

Device:
[ Pixel 8 — ABC123 ]

Status:
● Connected

Input device:
[ touchscreen ]

Resolution:
1080 × 2400

[ Start Recording ]
```

During recording:

```text
● Recording

Raw events:
1,238

Actions:
14

Current action:
Swipe

[ Pause ] [ Stop ]
```

The user should not need to understand `getevent`.

---

## 21. Diagnostics

Provide an optional developer diagnostics view:

```text
Android Recording Diagnostics

ADB:
Connected

Device:
ABC123

Input device:
/dev/input/event5

Touchscreen:
✓

Raw events:
1,238

Recognized actions:
14

Ignored events:
902

Unknown events:
3
```

Raw streams should not appear in normal UI.

---

## 22. Logging

Use structured logging.

Useful events include:

```text
android.device.connected
android.input_device.selected
android.getevent.started
android.getevent.stopped
android.gesture.recognized
android.recording.error
```

Do not log every raw event at INFO level.

Raw events belong at DEBUG/TRACE level.

---

## 23. Performance

The raw event stream can be very high frequency.

Never:

- update UI for every raw event
- retain the entire raw stream indefinitely
- run OCR for every raw event
- take screenshots for every raw event
- block parsing on image processing

Prefer:

```text
ADB process
    ↓
Streaming parser
    ↓
Bounded event queue
    ↓
Gesture recognizer
    ↓
Semantic action queue
    ↓
Authoring model
    ↓
UI
```

The UI receives semantic events, not Linux input events.

Throttle UI updates to something reasonable such as 100–250ms.

Use bounded queues and backpressure.

---

## 24. Existing Authoring Model

Do not create a separate `AndroidTestModel`.

Use the existing authoring architecture:

```text
AndroidRawInputEvent
       ↓
AndroidGestureRecognizer
       ↓
RecognizedGesture
       ↓
Existing RecordingEvent / normalized event
       ↓
Existing AuthoringDocument
       ↓
Existing YAML generator
```

If the current model is too mouse-specific, improve it generically rather than adding Android-specific hacks throughout the code.

---

## 25. Capability reporting

Report Android capabilities explicitly, for example:

```text
supports_touch = true
supports_swipe = true
supports_long_press = true
supports_multi_touch = true
supports_hardware_keys = true/false
supports_screenshot = true
supports_live_preview = true/false
```

The UI must honor these capabilities.

Never pretend an unsupported capability exists.

---

## 26. Connection loss

Handle disconnection without crashing.

Example:

```text
Android device disconnected.

Recording paused.

[Reconnect]
[Stop Recording]
```

Preserve everything recorded so far.

If the same device reconnects, allow recording to resume where practical.

---

## 27. Security

Never execute arbitrary commands derived from:

- OCR
- screen text
- logs
- `getevent`
- device names

Pass serials and paths as subprocess arguments.

Prefer:

```python
subprocess.Popen(
    ["adb", "-s", serial, "shell", "getevent", ...]
)
```

Never construct shell commands using untrusted strings with `shell=True`.

Do not require root access unless absolutely unavoidable.

---

## 28. Testing requirements

At minimum test:

- fake Android device
- real Android device where possible
- portrait
- landscape
- rotation
- tap
- swipe
- long press
- multi-touch
- hardware key
- multiple connected devices
- disconnect/reconnect
- unknown input event
- malformed `getevent` line
- high-frequency event stream
- large recording
- unusual coordinate ranges
- inverted axes

Unit tests must cover:

- parser
- touch state machine
- gesture recognition
- coordinate mapping
- key mapping
- process lifecycle

Physical-device tests can be integration/manual tests outside CI.

---

## 29. Performance benchmark

Create a synthetic high-frequency event stream representing several minutes of realistic touch activity.

Measure:

- parsing throughput
- gesture recognition throughput
- memory use
- queue latency
- UI update rate

The parser should comfortably process input faster than normal human interaction.

Profile before doing micro-optimizations.

---

## 30. Documentation

Update documentation with an Android recording guide covering:

1. Install ADB.
2. Enable Android Developer Options.
3. Enable USB debugging.
4. Connect the device.
5. Verify:

```bash
adb devices
```

6. Launch Argus Test Creator.
7. Select Android.
8. Start recording.
9. Interact with the device.
10. Stop recording.
11. Review semantic actions.
12. Export/run with Argus.

Document known `getevent` limitations and device/OEM differences.

---

## 31. Doctor integration

If the project has a `doctor` command, extend it.

Android diagnostics should report:

```text
ADB:
✓ Found

Devices:
✓ 1 connected

Selected device:
...

Android version:
...

Input devices:
✓ Found

Touchscreen:
✓ Detected

getevent:
✓ Available

Screenshot:
✓ Available
```

Failures must include actionable remediation.

---

## 32. Do not confuse recording with replay

This feature is strictly for recording.

`getevent` is the observation source.

When the generated test runs, Argus executes semantic actions such as:

```text
device.tap
device.swipe
device.key
```

Do not put raw `getevent` commands into generated tests.

---

## 33. Generated test quality

A recording such as:

```text
tap Movies
swipe
tap Batman
```

must become clean semantic steps.

Never generate:

```text
EV_ABS
EV_ABS
EV_SYN
EV_ABS
EV_ABS
EV_SYN
...
```

Low-level Android implementation details must remain hidden from final Argus YAML.

---

## 34. Implementation sequence

Implement in this order:

### Step 1
Inspect the current architecture.

### Step 2
Implement or extend the ADB abstraction.

### Step 3
Implement Android device discovery.

### Step 4
Implement touchscreen/input-device discovery using `getevent -lp`.

### Step 5
Implement streaming `getevent`.

### Step 6
Implement the parser.

### Step 7
Implement touch-slot state tracking.

### Step 8
Implement coordinate mapping.

### Step 9
Implement gesture recognition.

### Step 10
Map gestures into the existing authoring model.

### Step 11
Integrate screenshots/observations.

### Step 12
Integrate with the UI.

### Step 13
Add diagnostics.

### Step 14
Add tests.

### Step 15
Add documentation.

### Step 16
Run the full test suite and static analysis.

Keep the application runnable after every phase.

---

## 35. Required real-device workflow

Do not consider the feature complete after implementing only the parser.

A real Android device must support:

```text
Connect Android device
        ↓
Creator discovers device
        ↓
Creator identifies touchscreen
        ↓
Start recording
        ↓
Tap
        ↓
Recognize tap
        ↓
Swipe
        ↓
Recognize swipe
        ↓
Long press
        ↓
Recognize long press
        ↓
Press Android BACK
        ↓
Recognize key
        ↓
Stop recording
        ↓
Review semantic actions
        ↓
Edit actions
        ↓
Generate valid Argus YAML
        ↓
Run generated test through Argus
```

---

## 36. Definition of done

The Android `getevent` recorder is complete when:

- [ ] Android devices can be discovered.
- [ ] Multiple connected devices are handled correctly.
- [ ] The selected ADB serial is always used.
- [ ] Touchscreen input devices are discovered dynamically.
- [ ] `/dev/input/eventN` is never hardcoded.
- [ ] `getevent` runs as a streaming subprocess.
- [ ] The subprocess can be cleanly stopped.
- [ ] No zombie/orphan processes remain.
- [ ] Raw events are parsed into typed models.
- [ ] `EV_SYN` handling is correct.
- [ ] Multi-touch slots are handled correctly.
- [ ] Raw coordinates are mapped to screen coordinates.
- [ ] Device orientation is considered.
- [ ] Taps are recognized.
- [ ] Swipes are recognized.
- [ ] Long presses are recognized.
- [ ] Multi-touch is detected without false taps.
- [ ] Useful Android key events are recognized.
- [ ] Raw events are not exposed as final Argus test steps.
- [ ] Semantic actions use the existing Authoring Model.
- [ ] Screenshots can be associated with recorded actions.
- [ ] UI remains responsive during recording.
- [ ] High-frequency events do not flood the UI.
- [ ] Memory usage remains bounded.
- [ ] Device disconnects are handled gracefully.
- [ ] Fake ADB tests exist.
- [ ] Parser tests exist.
- [ ] Gesture recognition tests exist.
- [ ] Coordinate mapping tests exist.
- [ ] Integration tests exist where practical.
- [ ] Documentation is updated.
- [ ] Existing functionality continues to pass all tests.

---

## Final engineering principle

The implementation must make this statement true:

> **`getevent` is an implementation detail of the Android recorder, not part of the Argus test language.**

The user should think:

> "I recorded my Android test."

They should never have to think:

> "I recorded Linux input events."

The final abstraction is:

```text
Android hardware/input
        ↓
getevent
        ↓
Android recorder
        ↓
semantic interactions
        ↓
Argus Test Creator
        ↓
Argus test
```

Build this as a clean extension of the existing Argus Test Creator architecture, not as a one-off Android-specific feature.
