#!/bin/bash
# Replay timelines (20 fps, scenario configs, TRT x@1280 on dev37) for the multi-pass study scenes not in E1.
cd /data/WallDance/application
export LD_LIBRARY_PATH=$(ls -d .venv/lib/python3*/site-packages/nvidia/*/lib | tr '\n' ':')
OUT=/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp/tl
mkdir -p $OUT
for s in ${SCENES:-dark-crowd facade-ghosts blur-runner bdx1005-s5-ghost}; do
  nice -n 5 timeout 3000 .venv/bin/python tests/replay.py --scenario tests/scenarios/$s.json --trt --engine-dir models/dev37 \
    --imgsz 1280 --quality --internal --timeline $OUT/${s}.json > $OUT/${s}.log 2>&1
  echo "$s rc=$? $(date +%H:%M)"
done
