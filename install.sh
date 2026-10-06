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

# ── Select the PyTorch wheel index for the current install target ────────────
if [ "$HAS_GPU" -eq 1 ]; then
    PYTORCH_INDEX="https://download.pytorch.org/whl/cu130"
else
    PYTORCH_INDEX="https://download.pytorch.org/whl/cpu"
fi

# ── Pinned mode (ARCH-4 / decision D3) ─────────────────────────────────────
# requirements-prod.txt = the exact package set of the field laptop's verified
# venv (`wdremote freeze-lock`). When present, reproduce it with `uv pip sync`
# instead of resolving the newest versions (TRT engines are version-locked).
if [ -f requirements-prod.txt ]; then
    PIN_PY=$(sed -n 's/^# wd-python: *//p' requirements-prod.txt | head -1)
    PIN_INDEX=$(sed -n 's/^# wd-torch-index: *//p' requirements-prod.txt | head -1)
    PIN_INDEX=${PIN_INDEX:-$PYTORCH_INDEX}
    echo "[WallDance] Pinned install from requirements-prod.txt (python ${PIN_PY:-default}, torch index $PIN_INDEX)"
    if [ ! -x .venv/bin/python ]; then
        uv venv .venv ${PIN_PY:+--python "$PIN_PY"}
    fi
    # Linux dev boxes: drop Windows-only pins (pywin32 & co.) from the sync set.
    grep -viE '^(pywin32|pywin32-ctypes|pypiwin32|winshell)==' requirements-prod.txt \
        > .requirements-prod.platform.txt
    uv pip sync .requirements-prod.platform.txt --python .venv/bin/python \
        --extra-index-url "$PIN_INDEX" --index-strategy unsafe-best-match \
        ${WALLDANCE_PIN_DRY_RUN:+--dry-run}
    rm -f .requirements-prod.platform.txt
    echo "[WallDance] Verifying core runtime dependencies..."
    [ -n "${WALLDANCE_PIN_DRY_RUN:-}" ] || uv run --no-sync python -c "import cv2, torch"
    echo "Installation complete (pinned)."
    exit 0
fi

# ── Remove stale resolver config (old installs generated uv.toml) ───────────
rm -f uv.toml

# ── Remove stale lock (index URLs may have changed) ─────────────────────────
rm -f uv.lock

install_selected_torch() {
    echo "[WallDance] Installing torch/torchvision from $PYTORCH_INDEX..."
    uv pip install --upgrade torch torchvision --index-url "$PYTORCH_INDEX"
}

verify_runtime_deps() {
    uv run --no-sync python -c "import cv2, torch"
}

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

install_selected_torch

# ── Auto-fix: force-install CUDA PyTorch via uv pip ─────────────────────────
auto_fix_torch() {
    echo "[WallDance] Trying PyTorch CUDA wheels in order: cu130, cu129, cu128, cu126, cu124"
    for CUDA_TAG in cu130 cu129 cu128 cu126 cu124; do
        echo "[WallDance] Trying $CUDA_TAG..."
        if uv pip install --upgrade torch torchvision \
                --index-url "https://download.pytorch.org/whl/$CUDA_TAG" 2>&1; then
            AUTO_OK=$(uv run --no-sync python -c "import torch; print('1' if torch.cuda.is_available() else '0')" 2>/dev/null || echo "0")
            if [ "$AUTO_OK" = "1" ]; then
                echo "OK: Automatic PyTorch upgrade succeeded with $CUDA_TAG."
                return 0
            else
                echo "[WallDance] $CUDA_TAG installed but CUDA still unavailable."
            fi
        else
            echo "[WallDance] Install attempt with $CUDA_TAG failed."
        fi
    done
    echo "WARNING: Automatic PyTorch upgrade did not resolve GPU support."
    echo "Action: use the latest stable/nightly command from https://pytorch.org/get-started/locally/"
    echo "        then re-run install.sh."
    return 1
}

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
            echo "[WallDance] This likely means the PyTorch index did not have a CUDA build for the required version."
            echo "[WallDance] Attempting automatic CUDA PyTorch upgrade..."
            auto_fix_torch
            # Re-check after fix attempt
            CUDA_OK2=$(uv run --no-sync python -c "import torch; print('1' if torch.cuda.is_available() else '0')" 2>/dev/null || echo "0")
            if [ "$CUDA_OK2" != "1" ]; then
                echo "WARNING: CUDA still not available after auto-fix. Continuing in CPU mode."
                echo "Fix: ensure CUDA drivers are installed, then run install.sh again."
            else
                echo "OK: CUDA is now available after PyTorch upgrade."
            fi
        else
            echo "OK: PyTorch with CUDA support is ready."
        fi
    else
        echo "OK: PyTorch (CPU) is ready."
        echo "Tip: use a smaller model for better CPU performance:"
        echo "     In config.py set YOLO_MODEL = \"yolo11n-pose.pt\" and YOLO_IMGSZ = 640"
    fi
fi

echo "[WallDance] Verifying core runtime dependencies..."
if ! verify_runtime_deps; then
    echo "ERROR: Core dependencies failed to import inside the WallDance environment."
    echo "Hint: inspect the errors above, then rerun install.sh after fixing the dependency issue."
    exit 1
fi

echo ""
echo "Installation complete!"
echo "Run ./run.sh to start WallDance pose detection"
