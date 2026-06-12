#!/bin/bash
# Build TensorRT engines for all models and sizes

# Get workspace root
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/application"

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

# Models are in the workspace models folder
MODELS_DIR="$ROOT_DIR/models"
mkdir -p "$MODELS_DIR"

# Prevent ultralytics from auto-installing packages into the venv
export YOLO_AUTOINSTALL=0

# ── Offer to download missing pose models ──────────────────────────
# yolo11 family only: the Phase 2b corpus benchmark (ROADMAP 4.2 2b,
# tmp_analysis/phase2b/SUMMARY.md) measured yolo26 losing or tying every
# tier with an incompatible confidence scale — removed 2026-06-12.
ALL_MODELS=(
    yolo11n-pose yolo11s-pose yolo11m-pose yolo11l-pose yolo11x-pose
)

# Harvest any weights already present in application/ (downloaded earlier)
# into models/ so they are not re-downloaded and so the
# model manager — which reads from models/ — can find them.
for m in "${ALL_MODELS[@]}"; do
    if [ ! -f "$MODELS_DIR/${m}.pt" ] && [ -f "${m}.pt" ]; then
        echo "=== Found ${m}.pt in application/, moving to models/ ==="
        mv "${m}.pt" "$MODELS_DIR/${m}.pt"
    fi
done

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

# Per-rig fps table (ROADMAP P-6 / Phase 2b): calib2 consumes
# models/fps_table.json for the imgsz FPS budget + model advisory.
echo "=== Measuring per-model fps -> models/fps_table.json ==="
uv run --no-sync python "$ROOT_DIR/extra/measure_engine_fps.py" \
    || echo "=== Warning: fps measurement failed (table not updated) ==="