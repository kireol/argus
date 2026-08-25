# Desktop (Windows / Linux / macOS)

The `desktop` adapter drives a native application on the machine running
Argus: it launches the app as a process, screenshots the display with
[pyautogui](https://pyautogui.readthedocs.io/), sends mouse and keyboard
input, and captures the process's stdout/stderr as device logs.

| Operation | Implementation |
| --- | --- |
| Connect | import `pyautogui`, read the screen size; on macOS also take one screenshot — an all-black capture means Screen Recording permission is missing (fails with an OS-specific remediation when there is no display / permission) |
| Start app | `Popen([command, *args])` with `cwd`/`env`, then `startup_wait` |
| Stop app | `terminate()`, wait `stop_timeout`, then `kill()` |
| Reset app | stop → `reset_command` (shell) → start |
| App running? | process alive |
| Screenshot | `pyautogui.screenshot()`, cropped to `region` if set |
| Screen size | screenshot pixels; `scale` = screenshot width ÷ logical width (HiDPI) |
| Tap | `click` |
| Swipe | `mouseDown` → `moveTo(duration)` → `mouseUp` |
| Long press | `mouseDown` → hold → `mouseUp` |
| Drag | `mouseDown` → hold → `moveTo(duration)` → `mouseUp` |
| Pinch / multi-touch | **unsupported** — no portable touch injection; zoom with `device.key: Ctrl+Plus` (`Cmd+Plus` on macOS) |
| Keys | `press` for single keys (Android names map: `BACK` → `escape`, `DPAD_*` → arrows); `hotkey` for chords like `Ctrl+Shift+t` |
| Logs | process stdout + stderr |

Coordinates in tests are **screenshot pixels** (inside `region` when set),
as on every other adapter; the adapter converts to pyautogui's logical
coordinates on HiDPI displays.

## Prerequisites

- `pip install "argus[desktop]"`.
- **Linux (Ubuntu):** an X11 session with `DISPLAY` set, and
  `sudo apt install scrot python3-tk python3-dev`. Wayland sessions must run
  Argus under XWayland or a virtual display (`xvfb-run argus run ...`).
- **macOS:** grant your terminal **Screen Recording** and **Accessibility**
  permission (System Settings → Privacy & Security). Without Screen
  Recording, screenshots come back black and the adapter reports it.
- **Windows:** nothing extra; run the terminal at the same integrity level
  as the app (an elevated app ignores input from a non-elevated terminal).

## Configuration

```yaml
devices:
  desktop_app:
    type: desktop
    platform: linux                      # windows | linux | macos; defaults to the host OS
    command: ./build/ExampleApp          # required; executable or script
    args: ["--fullscreen"]               # optional
    cwd: ./build                         # optional
    env: {EXAMPLE_ENV: "1"}              # optional, merged over the environment
    startup_wait: 2s                     # optional, sleep after launch
    stop_timeout: 5s                     # optional, terminate → kill grace
    reset_command: ./scripts/reset.sh    # optional, run between stop and start
    region: [0, 0, 1920, 1080]           # optional, screenshot crop [x, y, w, h] in pixels
```

One configuration file can serve all three OSes by using `${VAR}`
placeholders for `command` and setting `platforms: [windows, linux, macos]`
in tests; `platform:` selects which entry a run uses.

## Gestures

`device.tap`, `device.swipe`, `device.long_press` and `device.drag` work
(as mouse actions). `device.pinch` and `device.multi_touch` fail with a
`DeviceCapabilityError`; use keyboard zoom instead:

```yaml
- action: device.key
  key: Ctrl+Plus
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `pyautogui is not installed` | `pip install "argus[desktop]"` |
| `no display available` on Linux | set `DISPLAY`, or run under `xvfb-run` |
| screenshot entirely black (macOS) | grant Screen Recording permission, restart the terminal |
| input ignored (macOS) | grant Accessibility permission |
| input ignored (Windows) | run the terminal with the same elevation as the app |
| `region ... exceeds the screenshot` | the crop must lie inside the screen in **pixels**, not logical points |
| `Application executable not found` | `command` is resolved relative to `cwd` (or the current directory) |
