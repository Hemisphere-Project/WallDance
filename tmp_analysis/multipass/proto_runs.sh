#!/bin/bash
# End-to-end prototype A/B: the worktree's replay (zoom pass in core/zoom_pass.py) on each scene, baseline
# (zoom off) vs each zoom mode; TRT x@IMGSZ main pass (dev37 engines), PyTorch x@640 zoom pass.
WT=/data/WallDance/.claude/worktrees/agent-ae6a534f4327c2597
cd $WT/application
export LD_LIBRARY_PATH=$(ls -d /data/WallDance/application/.venv/lib/python3*/site-packages/nvidia/*/lib | tr '\n' ':')
PY=/data/WallDance/application/.venv/bin/python
OUT=/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp/proto
mkdir -p $OUT
IMGSZ=${IMGSZ:-1280}
for s in ${SCENES:-facade-ghosts}; do
  for m in ${MODES:-off lost}; do
    tag=${s}_${IMGSZ}_${m}${TAG:-}
    X="--set zoom_mode=$m --set zoom_model=/data/WallDance/models/yolo11x-pose.pt ${EXTRA:-}"
    nice -n 5 timeout 5400 $PY tests/replay.py --scenario tests/scenarios/$s.json --trt --engine-dir models/dev37 \
      --imgsz $IMGSZ --quality --internal $X --timeline $OUT/$tag.json > $OUT/$tag.log 2>&1
    echo "$tag rc=$? $(date +%H:%M) $(grep '\[Zoom\]' $OUT/$tag.log | tail -1 | cut -c1-150)"
  done
done
