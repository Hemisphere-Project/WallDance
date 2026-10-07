#!/bin/bash
# Reference for "just use a bigger image": facade-ghosts with the main pass at its own config size 1920
# (no dev37 engine at 1920: the GPU + PyTorch path), zoom off.
WT=/data/WallDance/.claude/worktrees/agent-ae6a534f4327c2597
cd $WT/application
export LD_LIBRARY_PATH=$(ls -d /data/WallDance/application/.venv/lib/python3*/site-packages/nvidia/*/lib | tr '\n' ':')
OUT=/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp/proto
nice -n 5 timeout 5400 /data/WallDance/application/.venv/bin/python tests/replay.py --scenario tests/scenarios/facade-ghosts.json \
  --imgsz 1920 --quality --internal --timeline $OUT/facade-ghosts_1920pt_off.json > $OUT/facade-ghosts_1920pt_off.log 2>&1
echo "facade-ghosts_1920pt_off rc=$? $(date +%H:%M)"
nice -n 5 timeout 5400 /data/WallDance/application/.venv/bin/python tests/replay.py --scenario tests/scenarios/facade-ghosts.json \
  --imgsz 1280 --quality --internal --timeline $OUT/facade-ghosts_1280pt_off.json > $OUT/facade-ghosts_1280pt_off.log 2>&1
echo "facade-ghosts_1280pt_off rc=$? $(date +%H:%M)"
