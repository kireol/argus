# Argus Demo -- Yocto / embedded Linux example

A small `pygame` app for an embedded Linux (Yocto) target, packaged as a
systemd service and a minimal BitBake layer, driven by Argus over SSH.

It implements the shared "Argus Demo" behaviour: a Home screen with a
`Count: N` counter and a `Settings` control, a Settings screen with a
`Dark theme` toggle and a `Back` control, and a colour swatch that is green
(`#2ecc71`) in light theme and purple (`#8e44ad`) in dark theme.

## Prerequisites

- A Yocto/embedded Linux target reachable over SSH (key or password auth),
  with Python 3 and `pygame` available (the recipe below adds
  `python3-pygame` as a runtime dependency).
- On the host running Argus: `pip install "argus[yocto]"` (for `paramiko`),
  and `curl` on `PATH` (used by the tests, see below).
- To build the layer: a working Yocto/OpenEmbedded build environment
  (`bitbake`) with `meta-python` available (provides `python3-pygame`,
  `python3-core`) alongside `poky`.
- To run the app locally for a quick sanity check (no target needed):
  `pygame` in your local Python environment (`uv pip install pygame` or
  `pip install pygame`).

## Layer layout

```
examples/yocto/
  app/                          # <- edit this. Local, runnable Python source.
    argus_demo.py
    argus-demo.service
  meta-argus-demo/               # <- a minimal BitBake layer
    conf/layer.conf
    recipes-argus/argus-demo/
      argus-demo_1.0.bb          # points FILESEXTRAPATHS at ../../../app/, see below
  argus.yaml
  tests/demo.yaml
```

### Editable source vs. recipe files

`app/` is the single, editable copy of the source -- there is no second copy
under the recipe to keep in sync. BitBake's local-file fetcher normally only
looks in the recipe's own `FILESPATH` (the recipe's directory and its
`files/` subdirectory), but that search path is just configuration:
`argus-demo_1.0.bb` extends it with

```
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../app:"
```

`THISDIR` is `meta-argus-demo/recipes-argus/argus-demo/`; three `..` levels
up is `examples/yocto/`, then into `app/` -- so
`SRC_URI = "file://argus_demo.py file://argus-demo.service"` resolves
straight to the files you edit, with nothing to fall out of sync. Edit
`app/argus_demo.py` or `app/argus-demo.service` and `bitbake argus-demo`
picks up the change on the next build, no copy step required.

The one thing to watch: this path is relative to the layer's location
*inside this repository*. If you copy `meta-argus-demo/` out on its own (a
separate layer checkout, a different manifest, etc.), `app/` has to travel
with it at the same relative position (`../../../app` from the recipe
directory), or you adjust `FILESEXTRAPATHS` to point wherever it ends up.

## Build

Add the layer and build the recipe from your Yocto build directory:

```bash
bitbake-layers add-layer /path/to/argus/examples/yocto/meta-argus-demo
bitbake argus-demo
```

The recipe (`argus-demo_1.0.bb`) installs `argus_demo.py` as `/usr/bin/argus-demo`,
installs `argus-demo.service` under the systemd unit dir, `inherit systemd`s
it (`SYSTEMD_SERVICE:${PN} = "argus-demo.service"`, auto-enabled), and depends
on `python3-pygame`, `python3-core`, `python3-json`, `python3-netserver`,
`python3-threading`, `python3-io`, and `python3-argparse` at runtime. These
module packages come from OE-core's `python3` manifest (`meta/recipes-devtools/
python/python3_*.bb`'s `PACKAGES`/`FILES` split) and match the stdlib modules
`argus_demo.py` imports, but the recipe has not itself been build-verified in
a real Yocto build (no Yocto toolchain was available in this environment) —
double-check the package names against your layer's python3 manifest if the
image build fails to resolve them. Include `argus-demo` in your image
(`IMAGE_INSTALL:append = " argus-demo"`) and flash/boot as usual. The
screenshot command in `argus.yaml` runs `curl` *on the target* (it's how
the adapter pulls a PNG off the instrumentation server over SSH), so also
make sure `curl` is present in the target image, e.g.
`IMAGE_INSTALL:append = " argus-demo curl"` — most `core-image-*` targets
already include it, but minimal/custom images may not.

## Run the app

On the target, once installed:

```bash
systemctl start argus-demo
journalctl -u argus-demo -f
```

To try the app locally on your workstation first (no target, no display
stack assumptions -- just a normal window):

```bash
uv pip install pygame            # or: pip install pygame
python3 examples/yocto/app/argus_demo.py --windowed
curl -s http://127.0.0.1:8085/test/status
curl -s http://127.0.0.1:8085/test/screen -o /tmp/screen.png
```

`--windowed` opens a normal 1280x720 window instead of trying fullscreen
(pass `--windowed 1024x600` for a different size); omit it to run fullscreen
at the display's own resolution (what the target does).

## Run the tests

From the repository root, with the target reachable over SSH:

```bash
YOCTO_HOST=192.168.1.50 YOCTO_USER=root YOCTO_KEY=~/.ssh/id_ed25519 \
    .venv/bin/argus run --config examples/yocto/argus.yaml
```

## What the tests show

`tests/demo.yaml` (YOC-001..YOC-009) exercises Home/Settings navigation, the
counter, the dark-theme swatch colour, instrumentation, device logs, reset,
and a screenshot artifact -- see `docs/test-authoring.md` for the actions and
conditions used.

The feature-level setup uses `device.restart` rather than `device.start`: it
guarantees a clean process (fresh counter/theme/screen) at the start of the
suite even if `argus-demo` was already running from a previous run, whereas
`device.start` on an already-running app would be a no-op that leaves stale
state in place.

### Screenshots without a display stack

`docs/yocto.md` documents the Yocto adapter's screenshot mechanism as *any*
command that writes an image file on the device, copied back over SSH --
there is no `screenshot: {type: instrumentation}` shortcut in
`src/argus/adapters/yocto.py` (`from_config` only ever looks at
`screenshot.command`). This app already runs an HTTP instrumentation server
that serves the current frame as a PNG at `GET /test/screen`
(`pygame.image.save` to a `BytesIO`), so `argus.yaml` uses:

```yaml
screenshot:
  command: "curl -s -o {path} http://127.0.0.1:8085/test/screen"
  remote_path: /tmp/argus_demo_screen.png
```

which works on any target regardless of display stack -- no Weston, no X11,
no framebuffer driver assumptions. If your target does run a compositor,
the alternatives from `docs/yocto.md` work too, e.g.:

```yaml
screenshot:
  command: "weston-screenshooter -f {path}"   # Weston/Wayland
# command: "fbgrab {path}"                     # plain framebuffer
```

### Why `shell.run` + curl for input

`docs/test-authoring.md` lists `device.key` and `device.tap` as the normal
way to drive a device. Reading `src/argus/adapters/yocto.py` and
`src/argus/adapters/base.py`, though: `YoctoAdapter` never overrides
`tap()` or `press_key()`, and its `capabilities` property never sets
`supports_tap`/`supports_keyboard` -- so both actions would fail immediately
with `DeviceCapabilityError`. This is a deliberate consequence of the
adapter's design (generic SSH, no display-stack assumption, so no
input-injection mechanism is baked in either).

Instead, every test that needs to press a key uses `shell.run` to `curl`
a JSON body to a **testing-only** `POST /test/input` endpoint on the app's
own instrumentation server (the same server that already serves `/test/
status`, `/test/state`, and `/test/screen`):

```yaml
- action: shell.run
  command: curl
  args:
    - -sf
    - -X
    - POST
    - "http://${YOCTO_HOST}:8085/test/input"
    - -H
    - "Content-Type: application/json"
    - -d
    - '{"key":"RETURN"}'
```

`shell.run` executes on the machine running Argus, not on the target, so the
curl call reaches the instrumentation port directly over the network --
no SSH round-trip needed for input at all. The app enqueues the key and
applies it on its next frame, exactly as if it came from a real keyboard.
This approach works on any target, with or without a compositor. If your
target *does* run a compositor and you'd rather inject real input events,
the equivalent with `xdotool` over SSH is:

```yaml
- action: shell.run
  command: ssh
  args: ["-i", "${YOCTO_KEY}", "${YOCTO_USER}@${YOCTO_HOST}",
         "DISPLAY=:0 xdotool key Return"]
```

but that requires X11 (or an Xwayland-compatible `xdotool` build) on the
target, which this example does not assume.

### Why a fixed `wait` after reset

This repository's convention is "never `wait`, always `wait_until`" (see
`docs/test-authoring.md` and `constraints.md`). `tests/demo.yaml` is one
documented exception: right after `device.reset`/`device.restart`
(`systemctl restart argus-demo` under the hood), the app process is briefly
gone and its instrumentation HTTP server and rendered frame are not yet
available. Reading `src/argus/engine/wait.py` and `src/argus/actions/
builtin.py`, `wait_until` does **not** retry through an exception raised by
the condition itself -- `InstrumentationError` (connection refused) and
`ScreenshotError` both abort the whole `wait_until` step on the very first
poll instead of being retried, and neither is in the retryable failure
category list in `src/argus/engine/runner.py`. A `log_contains "App ready"`
`wait_until` doesn't help either, since old "App ready" lines from before
the restart are still within the journal's last 200 lines. A short fixed
`wait: 2s` immediately after every reset avoids hitting that window; raise
it if your target's boot/restart is slower.

## Troubleshooting

- **`.venv/bin/argus validate` fails with "no host configured"**: set `YOCTO_HOST`,
  `YOCTO_USER`, `YOCTO_KEY` (or use `password` in a local override instead
  of a key).
- **SSH connects but the screenshot/log steps fail**: confirm
  `systemctl status argus-demo` is active on the target and
  `curl -s http://127.0.0.1:8085/test/status` succeeds *from the target
  itself* (that's exactly what the screenshot command runs over SSH).
- **`device.key`/`device.tap` raise `DeviceCapabilityError`**: expected --
  see "Why `shell.run` + curl for input" above; this example does not use
  either action.
- **`shell.run` curl steps fail with connection refused**: the instrumentation
  port (8085) must be reachable from the machine running Argus, not just
  from the target itself -- check firewalls/NAT between the two.
- **Host key rejected**: the adapter defaults to `host_key_policy: reject`;
  for a disposable lab device, add `host_key_policy: auto_add` under
  `devices.board` in a local copy of `argus.yaml`.
