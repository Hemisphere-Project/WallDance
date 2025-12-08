#!/bin/bash
# filepath: /data/WallDance/build_engines.sh

cd /data/WallDance

SIZES=(640 800 960 1280 1920)

for model in models/*.pt; do
    base=$(basename "$model" .pt)
    
    for size in "${SIZES[@]}"; do
        engine="models/${base}_${size}.engine"
        
        if [ -f "$engine" ]; then
            echo "=== Skipping $engine (already exists) ==="
            continue
        fi
        
        echo "=== Building $engine ==="
        # Use python -m to run yolo through the venv
        uv run --directory 05-WallDance python -c "
from ultralytics import YOLO
model = YOLO('$model')
model.export(format='engine', imgsz=$size, half=True, device=0)
"
        
        # Rename to include size in filename
        default_engine="models/${base}.engine"
        if [ -f "$default_engine" ]; then
            mv "$default_engine" "$engine"
            echo "=== Created $engine ==="
        else
            echo "=== Warning: $default_engine not found after export ==="
        fi
    done
done

echo "=== All engines built! ==="