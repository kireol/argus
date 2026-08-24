# Roku

The Roku adapter drives a Roku in **developer mode** running a **sideloaded**
channel. Control uses Roku's External Control Protocol (ECP); screenshots and
sideloading use the developer web installer; the BrightScript debug console is
captured as the device log.

## Prerequisites

1. Enable developer mode on the Roku (Home ×3, Up ×2, Right, Left, Right, Left,
   Right) and note the password you set.
2. Package your channel as a `.zip` (or sideload it yourself through
   `http://<roku-ip>/`).

No extra Python dependency is needed.

## Configuration

```yaml
devices:
  tv:
    type: roku
    platform: roku                  # label used by tests' `platforms:` filter
    host: 192.168.1.42              # required
    dev_password: ${ROKU_DEV_PASSWORD}  # developer-mode password; needed for screenshots and sideloading
    channel_zip: build/channel.zip  # optional — sideloaded on connect
    ecp_port: 8060                  # default
    debug_port: 8085                # BrightScript console, default
    installer_port: 80              # developer web installer (default)
    timeout: 10                     # seconds per request
```

Tests filter with `platforms: [roku]`.

## What the adapter does

| Operation | Implementation |
| --- | --- |
| Connect | `GET /query/device-info`, optional sideload (`POST /plugin_install`), start the console reader |
| Screenshot | `POST /plugin_inspect` (Screenshot) then `GET /pkgs/dev.jpg` — sideloaded channel only |
| Start app | `POST /launch/dev` (clears captured logs) |
| Stop app | `POST /keypress/Home` |
| Reset app | stop + start |
| Key | `POST /keypress/<Key>`; Android names map (`DPAD_LEFT` → `Left`, `ENTER` → `Select`, `BACK` → `Back`, `MEDIA_PLAY_PAUSE` → `Play`, `MEDIA_FAST_FORWARD` → `Fwd`); single characters send `Lit_<char>`; ECP names such as `InstantReplay` pass through |
| Logs | lines from the debug console on port 8085 (reconnects automatically) |
| Screen size | from `ui-resolution` (`720p`, `1080p`, `2160p`) |

Without `dev_password` the device reports `supports_screenshot: false` and visual
conditions raise a capability error — use `log_contains` or backend/instrumentation
conditions instead. `tap`/`swipe` are not supported (no pointer on Roku).

## Asserting on the debug console

```yaml
- action: wait_until
  timeout: 10s
  condition:
    type: log_contains
    pattern: "Player: state=(PLAYING|BUFFERING)"
```

## Limitations

- Store channels cannot be screenshotted or sideloaded; only the `dev` channel.
- Screenshots are JPEG/PNG captures of the channel's own render, not HDMI output.
- One Roku per device entry; the debug console allows a single client at a time.
