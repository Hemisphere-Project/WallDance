#!/bin/bash
# The night project's partial walk-in take (slot_2 21:18, 352 decodable frames here) replayed under the 10:33
# calibration (gamma 1.8 / CLAHE 1.5) that made YOLO see the equipment ghost: a timeline with the person AND
# the ghost, for the verification study.  Wall-band ROI of the landscape crop, the validated D27 settings.
cd /data/WallDance/application
export LD_LIBRARY_PATH=$(ls -d .venv/lib/python3*/site-packages/nvidia/*/lib | tr '\n' ':')
S=/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp
P=mur25m-ceinture-0610
nice -n 5 timeout 3000 .venv/bin/python tests/replay.py --project $P --slot 2 \
  --video ../projects/$P/recordings/slot_2_20261006_211831.avi --model yolo11x-pose --imgsz 1280 --trt \
  --engine-dir models/dev37 --quality --internal --timeline $S/tl/night_s2a_g18.json \
  --set tracking_mode=yolo_first --set confidence=0.15 --set tracker_intermittent_confirm=true \
  --set roi_enabled=true --set roi_x=173 --set roi_y=401 --set roi_w=1304 --set roi_h=566 \
  --set roi_source_w=1776 --set roi_source_h=1300 --set gamma=1.8 --set clahe_clip=1.5 \
  --set mog2_var_threshold=16 --set mog2_scale=0.5 > $S/tl/night_s2a_g18.log 2>&1
echo "night_s2a_g18 rc=$? $(date +%H:%M)"
