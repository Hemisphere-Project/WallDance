#!/bin/bash
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR/application" || {
    echo "ERROR: Could not open application directory."
    echo "Hint: run install.sh from the WallDance repository root."
    exit 1
}

# Detect Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is missing or not callable."
    echo "Hint: install Python 3.10-3.12 (e.g., sudo apt install python3)"
    exit 1
fi

# Detect or install uv
if ! command -v uv &> /dev/null; then
    echo "uv was not found. Attempting to install uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    
    # Source the env file to make uv available in current session
    if [ -f "$HOME/.cargo/env" ]; then
        source "$HOME/.cargo/env"
    fi
    
    if ! command -v uv &> /dev/null; then
        echo "ERROR: Failed to install or locate uv."
        echo "Hint: install uv manually from https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
fi

if [ -f "uv.lock" ]; then
    uv sync --frozen
else
    uv sync
fi

echo "[WallDance] Checking PyTorch/CUDA compatibility..."
if ! uv run --no-sync python -c "import torch" &> /dev/null; then
    echo "WARNING: PyTorch import failed. Runtime will likely use CPU fallback."
    echo "Hint: run install.sh again or check Python environment consistency."
else
    CUDA_OK=$(uv run --no-sync python -c "import torch; print('1' if torch.cuda.is_available() else '0')" 2>/dev/null || echo "0")
    if [ "$CUDA_OK" != "1" ]; then
        echo "WARNING: CUDA is not available to PyTorch. WallDance will run in CPU fallback mode (low FPS)."
        echo "Fix: install a GPU-compatible PyTorch/CUDA build, then run install.sh again."
    fi
fi

echo "Installation complete!"
echo "Run ./run.sh to start WallDance pose detection"
