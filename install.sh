#!/bin/bash
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR/application" || {
    echo "ERROR: Could not open application directory."
    echo "Hint: run install.sh from the WallDance repository root."
    exit 1
}

# ── Detect Python ────────────────────────────────────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is missing or not callable."
    echo "Hint: install Python 3.10-3.12 (e.g., sudo apt install python3)"
    exit 1
fi

# ── Detect or install uv ────────────────────────────────────────────────────
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

# ── Detect NVIDIA GPU ───────────────────────────────────────────────────────
HAS_GPU=0
if command -v nvidia-smi &> /dev/null && nvidia-smi &> /dev/null; then
    HAS_GPU=1
fi

# Allow manual override: ./install.sh --cpu  or  ./install.sh --gpu
for arg in "$@"; do
    case "$arg" in
        --cpu) HAS_GPU=0 ;;
        --gpu) HAS_GPU=1 ;;
    esac
done

if [ "$HAS_GPU" -eq 1 ]; then
    echo "[WallDance] NVIDIA GPU detected → installing with CUDA support."
else
    echo "[WallDance] No NVIDIA GPU detected → installing CPU-only (lower FPS, but works for dev/test)."
fi

# ── Generate uv.toml – override the "pytorch" named index URL ────────────────
# pyproject.toml declares a named index "pytorch" (explicit = true) so only
# torch and torchvision are fetched from it; everything else uses PyPI.
# We just need to point it at the right variant (CPU vs CUDA).
if [ "$HAS_GPU" -eq 1 ]; then
    PYTORCH_INDEX="https://download.pytorch.org/whl/cu130"
else
    PYTORCH_INDEX="https://download.pytorch.org/whl/cpu"
fi

cat > uv.toml <<UVEOF
index-strategy = "unsafe-best-match"

[[index]]
name = "pytorch"
url = "$PYTORCH_INDEX"
explicit = true
UVEOF

# ── Remove stale venv + lock (index URLs may have changed) ──────────────────
rm -f uv.lock

# ── Sync dependencies ───────────────────────────────────────────────────────
UV_EXTRAS=""
if [ "$HAS_GPU" -eq 1 ]; then
    UV_EXTRAS="--extra gpu"
fi

echo "[WallDance] Resolving and installing dependencies (this may take a few minutes)..."

# Try with IDS camera support; fall back without it
if ! uv sync $UV_EXTRAS --extra ids; then
    echo ""
    echo "[WallDance] IDS camera SDK not available — installing without IDS support."
    echo "[WallDance] (This is normal on laptops / dev machines.)"
    uv sync $UV_EXTRAS
fi

# ── Verify PyTorch ───────────────────────────────────────────────────────────
echo "[WallDance] Verifying PyTorch..."
if ! uv run --no-sync python -c "import torch" &> /dev/null; then
    echo "WARNING: PyTorch import failed. Runtime will likely use CPU fallback."
    echo "Hint: run install.sh again or check Python environment consistency."
else
    if [ "$HAS_GPU" -eq 1 ]; then
        CUDA_OK=$(uv run --no-sync python -c "import torch; print('1' if torch.cuda.is_available() else '0')" 2>/dev/null || echo "0")
        if [ "$CUDA_OK" != "1" ]; then
            echo "WARNING: CUDA not available to PyTorch despite NVIDIA GPU being present."
            echo "Fix: ensure CUDA drivers are installed, then run install.sh again."
        else
            echo "OK: PyTorch with CUDA support is ready."
        fi
    else
        echo "OK: PyTorch (CPU) is ready."
        echo "Tip: use a smaller model for better CPU performance:"
        echo "     In config.py set YOLO_MODEL = \"yolo11n-pose.pt\" and YOLO_IMGSZ = 640"
    fi
fi

echo ""
echo "Installation complete!"
echo "Run ./run.sh to start WallDance pose detection"
