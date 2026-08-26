# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Argus Test Creator (run scripts/build-package.sh)."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

datas = collect_data_files("argus_test_creator", includes=["demo/web/*"])
datas += copy_metadata("argus-test-creator")  # entry points for recorder adapters

hiddenimports = collect_submodules("argus_test_creator.adapters") + [
    "argus_test_creator.ui.main",
]

a = Analysis(
    [str(SRC / "argus_test_creator" / "ui" / "main.py")],
    pathex=[str(SRC)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ArgusTestCreator",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ArgusTestCreator")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="ArgusTestCreator.app", bundle_identifier="dev.argus.testcreator")
