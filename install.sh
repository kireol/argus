#!/usr/bin/env bash
# Universal Test Framework installer (macOS / Linux).
#
# Usage:
#   ./install.sh            # standard installation
#   ./install.sh --dev      # development installation (argus[dev])
#
# Safe to run repeatedly: an existing installation is upgraded in place.
# Requires no administrator privileges. Never modifies user configuration.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
BIN_DIR="$HOME/.local/bin"
EXTRAS="yocto,ocr"
MIN_PYTHON_MINOR=12

if [[ "${1:-}" == "--dev" ]]; then
    EXTRAS="yocto,ocr,dev"
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m⚠\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mINSTALLATION FAILED\033[0m\n\n%s\n\nNo existing configuration was modified.\n' "$*" >&2; exit 1; }

say ""
say "Universal Test Framework — installer"
say "────────────────────────────────────"
say ""
say "Platform: $(uname -s) $(uname -m)"

# ---------------------------------------------------------------- find python
# Preference order: uv (manages its own Python), then a compatible python3.
UV_BIN="$(command -v uv || true)"
PYTHON_BIN=""

find_python() {
    for candidate in python3.13 python3.12 python3; do
        local path
        path="$(command -v "$candidate" || true)"
        [[ -z "$path" ]] && continue
        local minor
        minor="$("$path" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)"
        local major
        major="$("$path" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)"
        if [[ "$major" -eq 3 && "$minor" -ge "$MIN_PYTHON_MINOR" ]]; then
            PYTHON_BIN="$path"
            return 0
        fi
    done
    return 1
}

if [[ -n "$UV_BIN" ]]; then
    ok "uv found: $UV_BIN"
elif find_python; then
    ok "Python found: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"
else
    DETECTED="$(command -v python3 >/dev/null && python3 --version 2>&1 || echo 'none')"
    die "Python 3.${MIN_PYTHON_MINOR} or newer is required.

Detected:
    ${DETECTED}

Install Python 3.${MIN_PYTHON_MINOR}+ and run the installer again:
    macOS:  brew install python@3.12   (or install uv: https://docs.astral.sh/uv/)
    Linux:  use your distribution's package manager, pyenv, or uv"
fi

# ------------------------------------------------------------------ create venv
say ""
say "Installing framework..."
cd "$REPO_ROOT"

if [[ -n "$UV_BIN" ]]; then
    # uv provisions a compatible Python automatically if needed.
    "$UV_BIN" venv --quiet --allow-existing --python ">=3.${MIN_PYTHON_MINOR}" "$VENV_DIR" \
        || die "Could not create the virtual environment with uv."
    "$UV_BIN" pip install --quiet -p "$VENV_DIR/bin/python" -e ".[${EXTRAS}]" \
        || die "Dependency installation failed. Check network access / package index configuration."
else
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        "$PYTHON_BIN" -m venv "$VENV_DIR" || die "Could not create the virtual environment."
    fi
    "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip \
        || die "Could not upgrade pip inside the virtual environment."
    "$VENV_DIR/bin/python" -m pip install --quiet -e ".[${EXTRAS}]" \
        || die "Dependency installation failed. Check network access / package index configuration."
fi
ok "Framework and dependencies installed (extras: ${EXTRAS})"

# ------------------------------------------------------------- launcher command
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/argus" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/argus" "\$@"
EOF
chmod +x "$BIN_DIR/argus"
ok "Launcher installed: $BIN_DIR/argus"

# ----------------------------------------------------------------- directories
mkdir -p "$REPO_ROOT/results"
ok "Result directory ready"

# ----------------------------------------------------------------- health check
say ""
say "Validating installation..."
"$VENV_DIR/bin/argus" version >/dev/null || die "The argus command does not run."
"$VENV_DIR/bin/argus" --help >/dev/null || die "The argus command does not run."
if "$VENV_DIR/bin/argus" validate --framework-only >/dev/null 2>&1; then
    ok "Framework validation passed"
else
    warn "Framework validation reported issues — run 'argus validate --framework-only' for details."
fi

# ----------------------------------------------------------------------- summary
say ""
say "────────────────────────────────────"
printf '\033[32mINSTALLATION COMPLETE\033[0m\n'
say ""
if ! command -v argus >/dev/null || [[ "$(command -v argus)" != "$BIN_DIR/argus" && "$(command -v argus)" != "$VENV_DIR/bin/argus" ]]; then
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        warn "$BIN_DIR is not on your PATH."
        say  "  Add this line to your shell profile (e.g. ~/.zshrc), then open a new terminal:"
        say  "      export PATH=\"\$HOME/.local/bin:\$PATH\""
        say ""
    fi
fi
say "Next steps:"
say "    argus init        # create your configuration"
say "    argus validate    # check your environment"
say "    argus run --config config/fake.yaml    # demo run (no hardware needed)"
say ""
