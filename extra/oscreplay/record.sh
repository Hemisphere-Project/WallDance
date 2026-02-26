#!/usr/bin/env bash
# ─── OSC Record Launcher ─────────────────────────────────────────────
# Finds Python 3 and launches osc_record.py
# Usage: ./record.sh [extra args for osc_record.py]
# ──────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Locate Python 3 ---
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VERSION=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo "")
        if [ "$VERSION" = "3" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo "  ╔══════════════════════════════════════════════════════════╗"
    echo "  ║  Python 3 not found!                                    ║"
    echo "  ║                                                         ║"
    echo "  ║  Install it:                                            ║"
    echo "  ║    Ubuntu/Debian : sudo apt install python3             ║"
    echo "  ║    Fedora        : sudo dnf install python3             ║"
    echo "  ║    Arch          : sudo pacman -S python                ║"
    echo "  ║    macOS         : brew install python3                 ║"
    echo "  ║    Or visit      : https://www.python.org/downloads/   ║"
    echo "  ╚══════════════════════════════════════════════════════════╝"
    echo ""
    exit 1
fi

echo "[Launcher] Using $PYTHON ($($PYTHON --version 2>&1))"
exec "$PYTHON" "$SCRIPT_DIR/osc_record.py" "$@"
