#!/bin/bash
S=${WD_REVIEW_OUT:-/tmp/wd-review}
while ! grep -q 'ALL DONE' $S/runs/run_all.log; do sleep 20; done
cd /data/WallDance/application
export WD_ENGINE_DIR=/data/WallDance/models/dev37
H=../tmp_analysis/audit-2026-10/perf/perf_harness.py
echo "=== perf natural x@1280 hangar-aerial $(date +%T)"; uptime
.venv/bin/python $H --scenario tests/scenarios/hangar-aerial.json --trt --model yolo11x-pose --imgsz 1280 --mode natural --frames 300 --osc --out $S/perf/x1280_aerial.json > $S/perf/x1280_aerial.log 2>&1; echo rc=$?
echo "=== perf natural x@1280 texture-duo + profile $(date +%T)"
.venv/bin/python $H --scenario tests/scenarios/texture-duo.json --trt --model yolo11x-pose --imgsz 1280 --mode natural --frames 300 --osc --profile-post $S/perf/duo_prof --out $S/perf/x1280_duo.json > $S/perf/x1280_duo.log 2>&1; echo rc=$?
echo "=== perf natural x@1280 hangar-aerial, PoseRunner OFF $(date +%T)"
WD_POSE_RUNNER=0 .venv/bin/python $H --scenario tests/scenarios/hangar-aerial.json --trt --model yolo11x-pose --imgsz 1280 --mode natural --frames 300 --osc --out $S/perf/x1280_aerial_noPR.json > $S/perf/x1280_aerial_noPR.log 2>&1; echo rc=$?
echo "=== pytest $(date +%T)"
.venv/bin/python -m pytest tests -q -x 2>&1 | tail -5
echo "PERF DONE $(date +%T)"
