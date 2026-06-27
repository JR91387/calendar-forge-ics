#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            version=$("$cmd" -c "import sys; print(sys.version_info >= (3,8))" 2>/dev/null)
            [ "$version" = "True" ] && echo "$cmd" && return 0
        fi
    done
    return 1
}

PYTHON=$(find_python) || {
    echo "ERROR: Python 3.8 or later not found."
    echo "Install from https://www.python.org/downloads/ then re-run."
    exit 1
}

exec "$PYTHON" "$SCRIPT_DIR/icsscrub.py"
