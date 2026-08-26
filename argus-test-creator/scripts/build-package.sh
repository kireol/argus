#!/usr/bin/env bash
# Build a standalone bundle with PyInstaller (see docs/packaging.md).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-../.venv/bin/python}
"$PY" -m PyInstaller --noconfirm --clean packaging/pyinstaller.spec
echo "Built: dist/ArgusTestCreator*"
