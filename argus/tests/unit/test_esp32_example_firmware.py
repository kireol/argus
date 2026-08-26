"""Guard against firmware/source drift.

The Wokwi e2e test (tests/integration/test_esp32_adapter_e2e.py) runs the *committed*
firmware.bin/firmware.elf for agents/esp32/examples/ssd1306_menu, not a freshly built
one. SOURCES.sha256 records the sha256 of the exact ArgusAgent.h + main.cpp those
binaries were built from (see BUILD.md for the exact recipe); this test recomputes it
and fails - with a rebuild instruction - if the sources have drifted from the binaries.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

AGENTS = Path(__file__).resolve().parents[2] / "agents/esp32"
EXAMPLE = AGENTS / "examples/ssd1306_menu"
HEADER = AGENTS / "arduino/ArgusAgent.h"
MAIN_CPP = EXAMPLE / "src/main.cpp"
SOURCES_SHA = EXAMPLE / "SOURCES.sha256"


def test_committed_firmware_matches_current_sources():
    expected = hashlib.sha256(HEADER.read_bytes() + MAIN_CPP.read_bytes()).hexdigest()
    recorded = SOURCES_SHA.read_text().strip().split()[0]
    assert recorded == expected, (
        "agents/esp32/examples/ssd1306_menu/firmware.bin and firmware.elf are stale: "
        "ArgusAgent.h or main.cpp changed since those binaries were built. Rebuild "
        "them (see BUILD.md: `pio run`, copy firmware.bin/firmware.elf, regenerate "
        "SOURCES.sha256) and commit the refreshed binaries."
    )
