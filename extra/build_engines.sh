#!/bin/bash
# Build TensorRT engines for all models and sizes

# Get workspace root
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/application"

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

# Models are in the workspace models folder
MODELS_DIR="$ROOT_DIR/models"
mkdir -p "$MODELS_DIR"

# Prevent ultralytics from auto-installing packages into the venv
export YOLO_AUTOINSTALL=0

# ── Offer to download missing pose models ──────────────────────────
ALL_MODELS=(
    yolo11n-pose yolo11s-pose yolo11m-pose yolo11l-pose yolo11x-pose
)

MISSING=()
for m in "${ALL_MODELS[@]}"; do
    [ ! -f "$MODELS_DIR/${m}.pt" ] && MISSING+=("$m")
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "=== Missing pose models (${#MISSING[@]}/${#ALL_MODELS[@]}): ==="
    for m in "${MISSING[@]}"; do echo "  - ${m}.pt"; done
    echo ""
    read -rp "Download missing models before building engines? [Y/n] " answer
    answer=${answer:-Y}
    if [[ "$answer" =~ ^[Yy] ]]; then
        for m in "${MISSING[@]}"; do
            echo "=== Downloading ${m}.pt ==="
            uv run --no-sync python -c "
from ultralytics import YOLO
import shutil, os
m = YOLO('${m}.pt')            # auto-downloads from Ultralytics hub
src = '${m}.pt'
dst = os.path.join(r'$MODELS_DIR', src)
if os.path.abspath(src) != os.path.abspath(dst) and os.path.isfile(src):
    shutil.move(src, dst)
"
            if [ $? -ne 0 ]; then
                echo "=== Warning: failed to download ${m}.pt ==="
            fi
        done
        echo "=== Downloads complete ==="
    else
        echo "Skipping downloads."
    fi
    echo ""
fi

SIZES=(640 800 960 1280 1536 1920)

for model in $MODELS_DIR/*.pt; do
    base=$(basename "$model" .pt)
    
    for size in "${SIZES[@]}"; do
        engine="$MODELS_DIR/${base}_${size}.engine"
        
        if [ -f "$engine" ]; then
            echo "=== Skipping $engine (already exists) ==="
            continue
        fi
        
        echo "=== Building $engine ==="
        # Use python to run yolo through the venv (--no-sync to keep CUDA torch)
        uv run --no-sync python -c "
from ultralytics import YOLO
model = YOLO('$model')
model.export(format='engine', imgsz=$size, half=True, device=0)
"
        
        # Rename to include size in filename
        default_engine="$MODELS_DIR/${base}.engine"
        if [ -f "$default_engine" ]; then
            mv "$default_engine" "$engine"
            echo "=== Created $engine ==="
        else
            echo "=== Warning: $default_engine not found after export ==="
        fi
    done
done

echo "=== All engines built! ==="