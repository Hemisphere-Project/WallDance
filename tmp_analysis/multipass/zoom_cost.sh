#!/bin/bash
# zoom_cost.py with the venv's CUDA/cuDNN libs first (the .pt -> ONNX -> TRT export runs a torch forward).
cd /data/WallDance/application
export LD_LIBRARY_PATH=$(ls -d .venv/lib/python3*/site-packages/nvidia/*/lib | tr '\n' ':')
timeout 2400 .venv/bin/python /data/WallDance/.claude/worktrees/agent-ae6a534f4327c2597/tmp_analysis/multipass/zoom_cost.py \
  /tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp/engine640 2>&1 | grep -E "^x@|laptop|rror" | tail -6
