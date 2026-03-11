#!/bin/bash
set -e

FORCE_CPU=0
if [ "$1" == "--cpu" ]; then
    FORCE_CPU=1
    shift
fi

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

if [ "$FORCE_CPU" -eq 1 ]; then
    echo "[WallDance] CPU mode enabled (--cpu)."
    export CUDA_VISIBLE_DEVICES="-1"
fi

# Ensure PyTorch's bundled CUDA/cuDNN libs take priority over
# potentially outdated system-installed versions.
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

uv run --no-sync python src/main.py "$@"
