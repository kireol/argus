# Yocto / Embedded Linux

The Yocto adapter is a generic SSH-based adapter for embedded Linux devices
running, for example, a C++ application. SSH is pure Python (paramiko), so
it works from Windows hosts without a local `ssh` binary.

## Configuration

```yaml
devices:
  living_room:
    type: yocto
    host: ${YOCTO_HOST}
    port: 22
    username: ${YOCTO_USER}
    private_key: ${YOCTO_KEY}        # path to an SSH key; or use `password`
    connect_timeout: 15
    command_timeout: 30
    host_key_policy: reject          # reject (default, secure) | auto_add
    known_hosts: null                # optional custom known_hosts file
    screen_size: [1920, 1080]        # optional; else /sys fb size is probed

    screenshot:                      # REQUIRED for visual tests
      command: "weston-screenshooter -f {path}"
      remote_path: /tmp/utf_screenshot.png
      timeout: 20

    app:                             # enables lifecycle operations
      start: "systemctl start myapp"
      stop: "systemctl stop myapp"
      process: "myapp"               # checked with pidof/pgrep

    log_command: "journalctl -u myapp -n 200 --no-pager"   # optional

    instrumentation:                 # optional, see instrumentation.md
      base_url: http://${YOCTO_HOST}:8085
```

## Screenshots are configuration, not code

No display stack is assumed. The default provider runs *any* command on the
device that writes an image file, then copies it back. `{path}` is replaced
with `remote_path`:

| Display stack | Example command |
| --- | --- |
| Weston / Wayland | `weston-screenshooter -f {path}` |
| wlroots compositors | `grim {path}` |
| X11 | `xwd -root -out /tmp/s.xwd && convert /tmp/s.xwd {path}` |
| Framebuffer | `fbgrab {path}` |
| Custom app service | `curl -s -o {path} http://127.0.0.1:8085/test/screen` |

The last row is worth highlighting: if your application implements the
instrumentation `GET /test/screen` endpoint, screenshots work regardless of
the display stack.

New provider types (implementing `argus.adapters.ScreenshotProvider`) can be
added without modifying the adapter — see
[plugin-development.md](plugin-development.md).

## SSH security

- Host-key verification is **on by default** (`reject`): unknown hosts fail
  with a clear message. For disposable lab devices you may opt into
  `host_key_policy: auto_add`.
- Key-based auth is preferred; agent keys and `~/.ssh` keys are picked up
  automatically when no `private_key` is set.
- Passwords/keys come from environment variables and are never logged.

## Verifying the setup

```bash
argus validate       # SSH, device health, app, screenshot, instrumentation
argus --dry-run
```
