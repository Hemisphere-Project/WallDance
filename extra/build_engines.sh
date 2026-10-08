#!/bin/bash
# Build TensorRT engines: the app's DEFAULT engine first (core/config.py
# YOLO_MODEL @ YOLO_IMGSZ -- yolo11x-pose @ 1280 since D33), then the m/l/x grid.
#
#   ./extra/build_engines.sh                 # default engine, then the whole grid
#   ./extra/build_engines.sh --default-only  # only the default engine (a few minutes)
DEFAULT_ONLY=0
[ "$1" = "--default-only" ] && DEFAULT_ONLY=1

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

# Engines are built for m/l/x only (the tiers calib2's advisory ranks;
# Phase 2b: n/s are never the right auto pick — capacity is the reliable
# lever on hard scenes). n/s weights stay downloadable as last-resort
# insurance; the app prompts to build their engine on demand if selected.
ENGINE_MODELS=(
    yolo11m-pose yolo11l-pose yolo11x-pose
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

# The model/imgsz a new project runs (D33).  Read from config.py so the script
# cannot drift from the app; the fallback only covers a broken venv.
DEFAULT_BASE=yolo11x-pose
DEFAULT_IMGSZ=1280
if _def=$(uv run --no-sync python -c "
import sys; sys.path.insert(0, 'src')
from core.config import YOLO_MODEL, YOLO_IMGSZ
print(YOLO_MODEL.replace('.pt', ''), YOLO_IMGSZ)" 2>/dev/null) && [ -n "$_def" ]; then
    read -r DEFAULT_BASE DEFAULT_IMGSZ <<< "$_def"
else
    echo "=== Warning: could not read the default from core/config.py; assuming ${DEFAULT_BASE} @ ${DEFAULT_IMGSZ} ==="
fi
echo "=== Default engine: ${DEFAULT_BASE} @ ${DEFAULT_IMGSZ} ==="

download_model() {
    local m="$1"
    echo "=== Downloading ${m}.pt ==="
    uv run --no-sync python -c "
from ultralytics import YOLO
import shutil, os
m = YOLO('${m}.pt')            # auto-downloads from Ultralytics hub
src = '${m}.pt'
dst = os.path.join(r'$MODELS_DIR', src)
if os.path.abspath(src) != os.path.abspath(dst) and os.path.isfile(src):
    shutil.move(src, dst)
" || echo "=== Warning: failed to download ${m}.pt ==="
}

# The default model is not optional: fetch it without asking.
if [ ! -f "$MODELS_DIR/${DEFAULT_BASE}.pt" ]; then
    download_model "$DEFAULT_BASE"
fi

MISSING=()
if [ "$DEFAULT_ONLY" = 0 ]; then
    for m in "${ALL_MODELS[@]}"; do
        [ ! -f "$MODELS_DIR/${m}.pt" ] && MISSING+=("$m")
    done
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "=== Missing pose models (${#MISSING[@]}/${#ALL_MODELS[@]}): ==="
    for m in "${MISSING[@]}"; do echo "  - ${m}.pt"; done
    echo ""
    read -rp "Download missing models before building engines? [Y/n] " answer
    answer=${answer:-Y}
    if [[ "$answer" =~ ^[Yy] ]]; then
        for m in "${MISSING[@]}"; do
            download_model "$m"
        done
        echo "=== Downloads complete ==="
    else
        echo "Skipping downloads."
    fi
    echo ""
fi

# build_engine BASE SIZE -> 0 when models/BASE_SIZE.engine exists afterwards
build_engine() {
    local base="$1" size="$2"
    local model="$MODELS_DIR/${base}.pt"
    local engine="$MODELS_DIR/${base}_${size}.engine"
    if [ -f "$engine" ]; then
        echo "=== Skipping $engine (already exists) ==="
        return 0
    fi
    if [ ! -f "$model" ]; then
        echo "=== Skipping ${base} @ ${size} (no weights) ==="
        return 1
    fi
    echo "=== Building $engine ==="
    # Use python to run yolo through the venv (--no-sync to keep CUDA torch)
    uv run --no-sync python -c "
from ultralytics import YOLO
model = YOLO('$model')
model.export(format='engine', imgsz=$size, half=True, device=0)
"
    # Rename to include size in filename
    local default_engine="$MODELS_DIR/${base}.engine"
    if [ -f "$default_engine" ]; then
        mv "$default_engine" "$engine"
        echo "=== Created $engine ==="
        return 0
    fi
    echo "=== Warning: $default_engine not found after export ==="
    return 1
}

# ── The default engine FIRST (D33) ─────────────────────────────────
# What a new project runs.  Without it the app runs PyTorch (3-7x slower) and
# says so (banner + readiness FAIL).  Built before the grid, so an interrupted
# or failed grid still leaves a show-ready box.
if ! build_engine "$DEFAULT_BASE" "$DEFAULT_IMGSZ"; then
    echo "================================================================"
    echo "ERROR: the DEFAULT engine ${DEFAULT_BASE}_${DEFAULT_IMGSZ}.engine was NOT built."
    echo "The app would run PyTorch (3-7x slower). Fix the error above and re-run."
    echo "================================================================"
    exit 1
fi
if [ "$DEFAULT_ONLY" = 1 ]; then
    echo "=== Default engine ready: $MODELS_DIR/${DEFAULT_BASE}_${DEFAULT_IMGSZ}.engine ==="
    exit 0
fi

SIZES=(640 800 960 1280 1536 1920)

for base in "${ENGINE_MODELS[@]}"; do
    for size in "${SIZES[@]}"; do
        build_engine "$base" "$size"
    done
done

echo "=== All engines built! ==="

# Per-rig fps table (ROADMAP P-6 / Phase 2b): calib2 consumes
# models/fps_table.json for the imgsz FPS budget + model advisory.
echo "=== Measuring per-model fps -> models/fps_table.json ==="
uv run --no-sync python "$ROOT_DIR/extra/measure_engine_fps.py" \
    || echo "=== Warning: fps measurement failed (table not updated) ==="
