#!/bin/bash
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR/application" || {
    echo "ERROR: Could not open application directory."
    echo "Hint: run run.sh from the WallDance repository root."
    exit 1
}

if ! command -v uv &> /dev/null; then
    echo "ERROR: uv is missing or not callable."
    echo "Hint: run install.sh first."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "ERROR: WallDance is not installed in application/.venv."
    echo "Hint: run ./install.sh first."
    exit 1
fi

# Ensure PyTorch's bundled CUDA/cuDNN libs take priority over
# potentially outdated system-installed versions. The venv may hold any
# python3.x (pyproject allows 3.10-3.12), so discover the layout.
NVIDIA_PACKAGES=""
for _d in "$ROOT_DIR/application/.venv/lib/python"*/site-packages/nvidia; do
    [ -d "$_d" ] && NVIDIA_PACKAGES="$_d" && break
done
if [ -d "$NVIDIA_PACKAGES" ]; then
    _EXTRA_LD=""
    for _subdir in "$NVIDIA_PACKAGES"/*/lib; do
        [ -d "$_subdir" ] && _EXTRA_LD="$_subdir${_EXTRA_LD:+:$_EXTRA_LD}"
    done
    if [ -n "$_EXTRA_LD" ]; then
        export LD_LIBRARY_PATH="$_EXTRA_LD${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi
fi

if ! uv run --no-sync python -c "import cv2, torch" &> /dev/null; then
    echo "ERROR: WallDance dependencies are incomplete in application/.venv."
    echo "Hint: rerun ./install.sh and fix any dependency errors before starting the app."
    exit 1
fi

uv run --no-sync python src/main.py "$@"
