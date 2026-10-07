#!/bin/bash
# The night project's dark takes (closest proxies for the ~30 m night demo): x@1280 vs l@1536 (and, on s4, the
# smaller-dancer emulation x@960 vs l@1280), PyTorch path for every variant (no dev37 engines at 1536), the
# validated recipe (D27: yolo_first, conf 0.15, intermittent; D28: gamma 0.73 / CLAHE 2.5 / MOG2 8 @ 0.7) and the
# shipping slot flags (belt backing, entry rule 0.25, height guard), wall-band ROIs from validate_night.py.
WT=/data/WallDance/.claude/worktrees/agent-ae6a534f4327c2597
cd $WT/application
export LD_LIBRARY_PATH=$(ls -d /data/WallDance/application/.venv/lib/python3*/site-packages/nvidia/*/lib | tr '\n' ':')
OUT=/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp/night
mkdir -p $OUT
P=mur25m-ceinture-0610
REC="--set tracking_mode=yolo_first --set confidence=0.15 --set tracker_intermittent_confirm=true --set gamma=0.73 --set clahe_clip=2.5 --set mog2_var_threshold=8.0 --set mog2_scale=0.7 --set belt_backing=true --set entry_min_travel_h=0.25 --set height_guard=true --set fg_enabled=false --set roi_enabled=true"
LAND="--set roi_x=173 --set roi_y=401 --set roi_w=1304 --set roi_h=566 --set roi_source_w=1776 --set roi_source_h=1300"
PORT="--set roi_x=29 --set roi_y=513 --set roi_w=1304 --set roi_h=566 --set roi_source_w=1488 --set roi_source_h=1528"
run() { take=$1 slot=$2 file=$3 roi=$4 model=$5 sz=$6
  tag=${take}_${model}_${sz}
  nice -n 5 timeout 5400 /data/WallDance/application/.venv/bin/python tests/replay.py --project $P --slot $slot \
    --video ../projects/$P/recordings/$file.avi --model $model --imgsz $sz --quality --internal \
    --timeline $OUT/$tag.json $REC $roi > $OUT/$tag.log 2>&1
  echo "$tag rc=$? $(date +%H:%M)"; }
run s4 4 slot_4_20261006_213310 "$PORT" yolo11x-pose 1280
run s4 4 slot_4_20261006_213310 "$PORT" yolo11l-pose 1536
run s4 4 slot_4_20261006_213310 "$PORT" yolo11x-pose 960
run s4 4 slot_4_20261006_213310 "$PORT" yolo11l-pose 1280
run s2c 2 slot_2_20261006_214531 "$LAND" yolo11x-pose 1280
run s2c 2 slot_2_20261006_214531 "$LAND" yolo11l-pose 1536
