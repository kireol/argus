#!/usr/bin/env bash
# Run the full quality gate.
set -euo pipefail
cd "$(dirname "$0")/.."
../.venv/bin/ruff check src tests
../.venv/bin/mypy src
../.venv/bin/python -m pytest -q "$@"
