#!/bin/bash
# The configuration alternative to a zoom pass for small people: a bigger single input, x vs l model
# (PyTorch path on dev37: no engines at these sizes; quality only, costs from the laptop's TRT table).
WT=/data/WallDance/.claude/worktrees/agent-ae6a534f4327c2597
cd $WT/application
export LD_LIBRARY_PATH=$(ls -d /data/WallDance/application/.venv/lib/python3*/site-packages/nvidia/*/lib | tr '\n' ':')
OUT=/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp/proto
for spec in ${SPECS:-yolo11x-pose:1536 yolo11l-pose:1536 yolo11l-pose:1920}; do
  m=${spec%%:*}; sz=${spec#*:}
  tag=${SCENE:-facade-ghosts}_${m}_${sz}pt
  nice -n 5 timeout 5400 /data/WallDance/application/.venv/bin/python tests/replay.py --scenario tests/scenarios/${SCENE:-facade-ghosts}.json \
    --model $m --imgsz $sz --quality --internal --timeline $OUT/$tag.json > $OUT/$tag.log 2>&1
  echo "$tag rc=$? $(date +%H:%M)"
done
