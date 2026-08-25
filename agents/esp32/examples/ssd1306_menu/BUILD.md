# Building the example

```bash
cd agents/esp32/examples/ssd1306_menu
pio run
cp .pio/build/esp32dev/firmware.bin .

# firmware.elf is committed debug-stripped (symbols for backtraces, no full debug info):
strip_bin=$(ls ~/.platformio/packages/toolchain-xtensa-esp32*/bin/*-strip | head -1)
"$strip_bin" --strip-debug .pio/build/esp32dev/firmware.elf -o firmware.elf
```

`firmware.bin`/`firmware.elf` are committed so `tests/integration/test_esp32_adapter_e2e.py`
can run in Wokwi without a toolchain. Rebuild and re-commit them when `main.cpp`
or `ArgusAgent.h` changes. Flash a real board with
`argus`'s `firmware:` option or `pio run -t upload`.

## SOURCES.sha256 (drift guard)

`SOURCES.sha256` records the sha256 of the exact firmware sources the committed
`firmware.bin`/`firmware.elf` were built from. `tests/unit/test_esp32_example_firmware.py`
recomputes it and fails - telling you to rebuild - if `../../arduino/ArgusAgent.h` or
`src/main.cpp` have changed since the binaries were built. Regenerate it together with
the binaries above, from this directory:

```bash
cat ../../arduino/ArgusAgent.h src/main.cpp | shasum -a 256 | awk '{print $1}' > SOURCES.sha256
```
