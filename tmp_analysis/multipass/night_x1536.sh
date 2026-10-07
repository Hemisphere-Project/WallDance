#!/bin/bash
# s2c (dark, still dancer at the far wall: the closest proxy for the 30 m night demo) at x@1536, same recipe as
# night_runs.sh: does the same model with more input pixels help where l@1536 hurt?
WT=/data/WallDance/.claude/worktrees/agent-ae6a534f4327c2597
cd $WT/application
export LD_LIBRARY_PATH=$(ls -d /data/WallDance/application/.venv/lib/python3*/site-packages/nvidia/*/lib | tr '\n' ':')
OUT=/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp/night
P=mur25m-ceinture-0610
nice -n 5 timeout 5400 /data/WallDance/application/.venv/bin/python tests/replay.py --project $P --slot 2 \
  --video ../projects/$P/recordings/slot_2_20261006_214531.avi --model yolo11x-pose --imgsz 1536 --quality --internal \
  --timeline $OUT/s2c_yolo11x-pose_1536.json \
  --set tracking_mode=yolo_first --set confidence=0.15 --set tracker_intermittent_confirm=true --set gamma=0.73 \
  --set clahe_clip=2.5 --set mog2_var_threshold=8.0 --set mog2_scale=0.7 --set belt_backing=true \
  --set entry_min_travel_h=0.25 --set height_guard=true --set fg_enabled=false --set roi_enabled=true \
  --set roi_x=173 --set roi_y=401 --set roi_w=1304 --set roi_h=566 --set roi_source_w=1776 --set roi_source_h=1300 \
  > $OUT/s2c_yolo11x-pose_1536.log 2>&1
echo "s2c_yolo11x-pose_1536 rc=$? $(date +%H:%M)"
