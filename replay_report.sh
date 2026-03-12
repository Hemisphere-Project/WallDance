#!/bin/bash
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR/application" || {
    echo "ERROR: Could not open application directory."
    echo "Hint: run replay_report.sh from the WallDance repository root."
    exit 1
}

if ! command -v uv &> /dev/null; then
    echo "ERROR: uv is missing or not callable."
    echo "Hint: run install.sh first."
    exit 1
fi

# Keep the same runtime environment as run.sh and avoid an automatic
# uv sync, which can disturb the working Torch/CUDA install.
NVIDIA_PACKAGES="$ROOT_DIR/application/.venv/lib/python3.10/site-packages/nvidia"
if [ -d "$NVIDIA_PACKAGES" ]; then
    _EXTRA_LD=""
    for _subdir in "$NVIDIA_PACKAGES"/*/lib; do
        [ -d "$_subdir" ] && _EXTRA_LD="$_subdir${_EXTRA_LD:+:$_EXTRA_LD}"
    done
    if [ -n "$_EXTRA_LD" ]; then
        export LD_LIBRARY_PATH="$_EXTRA_LD${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi
fi

uv run --no-sync python replay_report.py "$@"