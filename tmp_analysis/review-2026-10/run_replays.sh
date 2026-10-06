#!/bin/bash
# Replays on dev37 TRT x@1280 engines (models/dev37). Slots ON (default), timelines keep tracker+internal+emitted+ref.
cd /data/WallDance/application
R=${WD_REVIEW_OUT:-/tmp/wd-review}/runs
PY=.venv/bin/python
E=/data/WallDance/models/dev37
run() { name=$1; shift; echo "=== $name $(date +%T)"; $PY tests/replay.py "$@" --trt --engine-dir $E --timeline $R/$name.timeline.json --internal --out $R/$name.summary.json > $R/$name.log 2>&1; echo "    rc=$? $(date +%T)"; }
run aerial_full --scenario tests/scenarios/hangar-aerial-full.json --score --quality
run white_duo --scenario tests/scenarios/white-duo.json --score --quality
run texture_duo --scenario tests/scenarios/texture-duo.json --score --quality
run wallhang_clip --scenario tests/scenarios/texture-wallhang-clip.json --score --quality
run texture_aerial --scenario tests/scenarios/texture-aerial.json --score --quality
run blur_runner --scenario tests/scenarios/blur-runner.json --score --quality
# 2026-10-05 Bordeaux belt takes (project config, forced x@1280), belt on / off
for s in 5 6 4 8; do
  run bdx_s${s}_belt   --project 4_TANGO_HANGAR-whitebg3 --slot $s --model yolo11x-pose --imgsz 1280 --quality
  run bdx_s${s}_nobelt --project 4_TANGO_HANGAR-whitebg3 --slot $s --model yolo11x-pose --imgsz 1280 --quality --set use_ir_belt=false
done
# textured-wall ghost scenes (April project configs, app max_age order)
run tangoH2_s9 --project tango-H2 --slot 9 --model yolo11x-pose --imgsz 1280 --quality
run tangoH_s8  --project tango-H  --slot 8 --model yolo11x-pose --imgsz 1280 --quality
run floor_full --scenario tests/scenarios/hangar-floor-full.json --score --quality
echo "ALL DONE $(date +%T)"
