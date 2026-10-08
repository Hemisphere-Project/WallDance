#!/bin/bash
# 30 m confirmation (mur30m-0710), in the order it was run on dev37 (2026-10-08).  App code untouched.
set -e
D=/data/WallDance/tmp_analysis/confirm_0710
PY=/data/WallDance/application/.venv/bin/python
T=slot_2_20261007_200742.avi,slot_3_20261007_201246.avi,slot_4_20261007_201949.avi,slot_5_20261007_202412.avi
PH=150,253,253,253                       # person height as recorded per take
D27="--set confidence=0.15 --set sensitivity_conf_seed=0.15 --set tracker_intermittent_confirm=true --set tracking_mode=yolo_first"
cd /data/WallDance/application
# 0. engines: x@800 for dev37's TRT 11.3 (models/yolo11x-pose_800.engine is TRT tag 243); x@1280 / 960 symlinked from models/dev37
[ -f $D/engines/yolo11x-pose_800.engine ] || nice -n 5 $PY $D/build_engine.py yolo11x-pose 800
# 1. replays (tl/<V>_<take>.json + .info.json + .belt_static.npz)
nice -n 5 $PY $D/run_variant.py --takes $T --out $D/tl --label V0 --ph $PH --set fg_plate=plates/latest.npz
nice -n 5 $PY $D/run_variant.py --takes $T --out $D/tl --label V1 --ph $PH --set fg_plate=plates/latest.npz $D27 --set yolo_imgsz=1280
nice -n 5 $PY $D/flow_v2.py --calibrate-only --empty slot_2_20261007_200742.avi:1700 --out $D/flow_v2 --label v2
PL=/data/WallDance/projects/mur30m-0710/plates/flowcheck_flow_v2_slot_2_20261007_200742_1700.npz
nice -n 5 $PY $D/run_variant.py --takes $T --out $D/tl --label V2 --ph $PH $D27 --set yolo_imgsz=1280 \
    --set gamma=0.8 --set clahe_clip=1.0 --set mog2_var_threshold=8.0 --set mog2_scale=0.5 --set fg_enabled=true --set fg_plate=$PL
nice -n 5 $PY $D/run_variant.py --takes $T --out $D/tl --label V1b --ph $PH --set fg_plate=plates/latest.npz $D27 --set yolo_imgsz=800
nice -n 5 $PY $D/run_variant.py --takes $T --out $D/tl --label V1r --ph $PH --set fg_plate=plates/latest.npz $D27 --set yolo_imgsz=1280 \
    --set roi_enabled=true --set roi_x=238 --set roi_y=250 --set roi_w=1300 --set roi_h=900
# 2. tests/flow_check.py itself for V2 (its cache = the V1 / V2 timelines: same settings)
for s in slot_2_20261007_200742 slot_3_20261007_201246 slot_4_20261007_201949 slot_5_20261007_202412; do
  ln -sf ../tl/V1_$s.json $D/flow_v2/base_$s.json; ln -sf ../tl/V2_$s.json $D/flow_v2/v2_$s.json; done
nice -n 5 $PY $D/flow_v2.py --empty slot_2_20261007_200742.avi:1700 --label v2 --out $D/flow_v2 --engine-dir $D/engines
# 3. scene (walker GT, DN, belt), what YOLO sees on the empty wall, the door scale
nice -n 5 $PY $D/scene_measure.py --takes all
nice -n 5 $PY $D/empty_probe.py
$PY $D/door_scale.py
# 4. metrics, light, figures, report
nice -n 5 $PY $D/analyse.py
$PY $D/extra.py
$PY $D/light.py
nice -n 5 $PY $D/make_figs.py
$PY $D/verdict.py
$PY $D/report.py
