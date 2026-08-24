# Apple TV

Argus supports two Apple TV setups with different capabilities:

| | `tvos_sim` (Simulator) | `appletv` (physical, pyatv) |
| --- | --- | --- |
| Screenshots | yes (`simctl io screenshot`) | **no** |
| Logs | yes (`log stream` for the app process) | **no** |
| Remote keys | via `osascript` keyboard shortcuts | via pyatv |
| App launch / stop | `simctl launch` / `terminate` | `launch_app` / Home |
| Playback state (`now_playing`) | no | **yes** |

## tvOS Simulator (`tvos_sim`)

### Prerequisites

- macOS with Xcode and a tvOS simulator (`xcrun simctl list devices`).
- Accessibility permission for your terminal (System Settings → Privacy &
  Security → Accessibility) so `osascript` can send keys to the Simulator.

### Configuration

```yaml
devices:
  sim:
    type: tvos_sim
    platform: tvos_sim
    bundle_id: com.example.tvapp     # required
    udid: booted                      # or a simulator UDID
    app_path: build/Example.app       # optional — installed on connect and reset
    boot: true                        # boot the simulator if needed
    process_name: Example             # log stream predicate (default: last part of bundle_id)
    timeout: 30
```

### Keys

`DPAD_UP/DOWN/LEFT/RIGHT` → arrow keys, `ENTER`/`DPAD_CENTER` → Return (Select),
`BACK`/`MENU` → Escape (Menu), `MEDIA_PLAY_PAUSE` → Space, `HOME` → ⌘⇧H, single
characters → typed. Anything else raises a capability error. The Simulator window
is brought to the front before each key.

### Limitations

- No touch/trackpad gestures (`tap`/`swipe` unsupported).
- Keys go to whichever Simulator window is frontmost; run one simulator at a time.
- Argus never shuts the simulator down on disconnect.

## Physical Apple TV (`appletv`)

### Prerequisites

```bash
pip install "argus[appletv]"
atvremote --address 192.168.1.50 wizard   # pair; prints Companion/AirPlay credentials
```

### Configuration

```yaml
devices:
  living_room:
    type: appletv
    platform: appletv
    host: 192.168.1.50                # or identifier: <pyatv id>
    app_id: com.example.tvapp         # required — launched by start_application
    credentials:
      companion: "..."                # from atvremote wizard
      airplay: "..."
    timeout: 10
```

### Verification without screenshots

A physical Apple TV exposes no screenshot or log API, so verify through the
`now_playing` condition (state, title, app id, position advancing) and through
backend/instrumentation conditions:

```yaml
- action: press_key
  key: MEDIA_PLAY_PAUSE
- action: wait_until
  timeout: 15s
  condition:
    type: now_playing
    state: playing
    title: "Big Buck Bunny"
    position_advancing: true
```

Image/text conditions raise a capability error on this device.

### Keys

Android-style names map onto these pyatv RemoteControl method names: `up`,
`down`, `left`, `right`, `select`, `menu`, `home`, `play`, `pause`,
`play_pause`, `stop`, `next`, `previous`, `volume_up`, `volume_down`. Only
these method names are accepted.

### Limitations

- "Stop application" presses Home; tvOS has no kill API.
- Playback metadata depends on the app publishing now-playing info.
