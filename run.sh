#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

# Find a Python >= 3.8
find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            version=$("$cmd" -c "import sys; print(sys.version_info >= (3,8))" 2>/dev/null)
            if [ "$version" = "True" ]; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python) || {
    echo "ERROR: Python 3.8 or later not found."
    echo "Install from https://www.python.org/downloads/ then re-run."
    exit 1
}

# Create venv once
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV"
    "$VENV/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
fi

exec "$VENV/bin/python" "$SCRIPT_DIR/icsscrub.py"
