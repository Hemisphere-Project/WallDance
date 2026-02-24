#!/bin/bash
# Build TensorRT engines for all models and sizes

# Get workspace root
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/application"

# Models are in the workspace models folder
MODELS_DIR="$ROOT_DIR/models"

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
        # Use python to run yolo through the venv
        uv run python -c "
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