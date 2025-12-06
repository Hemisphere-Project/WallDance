#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")"

# Sync dependencies
uv sync

# Install mmcv with CUDA ops from OpenMMLab prebuilt wheels
uv run pip install mmcv==2.2.0 -f https://download.openmmlab.com/mmcv/dist/cu121/torch2.4.0/index.html

# Patch mmdet to allow mmcv 2.2.0 (temporary fix until mmdet updates version constraints)
sed -i "s/mmcv_maximum_version = '2.2.0'/mmcv_maximum_version = '2.3.0'/" .venv/lib/python3.10/site-packages/mmdet/__init__.py

echo "Installation complete!"
echo "Run ./run.sh to start pose detection with tracking"
