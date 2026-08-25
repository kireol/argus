# Building the example

```bash
cd agents/esp32/examples/ssd1306_menu
pio run
cp .pio/build/esp32dev/firmware.bin .pio/build/esp32dev/firmware.elf .
```

`firmware.bin`/`firmware.elf` are committed so `tests/integration/test_esp32_adapter_e2e.py`
can run in Wokwi without a toolchain. Rebuild and re-commit them when `main.cpp`
or `ArgusAgent.h` changes. Flash a real board with
`argus`'s `firmware:` option or `pio run -t upload`.
