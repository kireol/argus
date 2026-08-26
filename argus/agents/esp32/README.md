# Argus ESP32 agents

Copy-in firmware helpers that answer Argus's serial protocol (see `docs/esp32.md`):

- `arduino/ArgusAgent.h` — header-only, Arduino/PlatformIO.
- `micropython/argus_agent.py` — MicroPython module.
- `examples/ssd1306_menu/` — PlatformIO project + Wokwi diagram used by the integration test.

These files are not part of the `argus` Python package.
