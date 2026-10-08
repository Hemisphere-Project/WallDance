#!/bin/bash
# PLAN_25M §C.15 -- no snapshot update on "plate stale" frames: the replay gates, in the order run on dev37
# (2026-10-08).  WT = this checkout (the fix).  PRE = the same tree without the fix (git archive of c0baba9's
# application/ + projects / models symlinks), for the "before" runs that have no stored 46a0262 timeline.
# Outputs: /data/WallDance/tmp_analysis/stale_plate/ (tl/, night/, older/, *.log); compare.py reads them.
WT=$(cd "$(dirname "$0")/../.." && pwd)
PRE=${PRE:?set PRE to an unpatched copy (see above)}
OUT=/data/WallDance/tmp_analysis/stale_plate
PY=/data/WallDance/application/.venv/bin/python
ENG=/data/WallDance/models/dev37
S=$WT/tmp_analysis/stale_plate
mkdir -p $OUT/tl $OUT/night $OUT/older
T=slot_2_20261007_200742.avi,slot_3_20261007_201246.avi,slot_4_20261007_201949.avi,slot_5_20261007_202412.avi
PH=150,253,253,253
D27="--set confidence=0.15 --set sensitivity_conf_seed=0.15 --set tracker_intermittent_confirm=true --set tracking_mode=yolo_first"
step() { echo "== $1 $(date +%H:%M:%S)"; }

# 0. the mechanism, before the fix: slot 4 with D27 + the plate probe (identical rows to confirm_0710/tl/V1_slot_4)
step "probe before"
cd $PRE/application
[ -f $OUT/probe/V1pre_slot_4_20261007_201949.json ] || WD_APP=$PRE/application nice -n 5 $PY $S/run_variant.py \
    --takes slot_4_20261007_201949.avi --out $OUT/probe --label V1pre --ph 253 --probe \
    --set fg_plate=plates/latest.npz $D27 --set yolo_imgsz=1280 > $OUT/probe_V1pre.log 2>&1

# 1. mur30m-0710 with the fix: V1 (D27, the live snapshot) and V2 (D27 + the Calibrate snapshot of
#    confirm_0710), + the plate probe.  Before = confirm_0710/tl/V1_*, V2_* (46a0262).
cd $WT/application
step "V1"
nice -n 5 $PY $S/run_variant.py --takes $T --out $OUT/tl --label V1 --ph $PH --probe \
    --set fg_plate=plates/latest.npz $D27 --set yolo_imgsz=1280 > $OUT/tl_V1.log 2>&1
step "V2"
PL=/data/WallDance/projects/mur30m-0710/plates/flowcheck_flow_v2_slot_2_20261007_200742_1700.npz
nice -n 5 $PY $S/run_variant.py --takes $T --out $OUT/tl --label V2 --ph $PH --probe $D27 --set yolo_imgsz=1280 \
    --set gamma=0.8 --set clahe_clip=1.0 --set mog2_var_threshold=8.0 --set mog2_scale=0.5 --set fg_enabled=true \
    --set fg_plate=$PL > $OUT/tl_V2.log 2>&1

# 2. 2026-10-06 night takes, operator flow (flowcheck/night_flow.sh with the fix).  The baselines (project
#    config, no snapshot: the fix cannot touch them) are night_rc_46a0262's, linked in as flow_check's cache.
step "night"
for f in /data/WallDance/tmp_analysis/flowcheck/night_rc_46a0262/base_*.json; do ln -sf $f $OUT/night/; done
S2A=slot_2_20261006_211831.avi; S2C=slot_2_20261006_214531.avi; S3=slot_3_20261006_212855.avi
S4=slot_4_20261006_213310.avi; S6=slot_6_20261006_214306.avi; S7=slot_7_20261006_220640.avi
nice -n 5 $PY tests/flow_check.py --project mur25m-night-common --empty slot_1_20261006_211624.avi:100 \
    --takes $S6,$S7,$S4,$S3,$S2C,$S2A --label s1 --out $OUT/night --engine-dir $ENG > $OUT/night_s1.log 2>&1
nice -n 5 $PY tests/flow_check.py --project mur25m-night-common --empty slot_2_20261006_214010.avi:20 \
    --takes $S6,$S7,$S4,$S2C --label dark --out $OUT/night --engine-dir $ENG > $OUT/night_dark.log 2>&1

# 3. older corpus (flowcheck/older_regression.sh), before (PRE) and after (WT): the stored older/*_rc_*
#    timelines are de998d3's (before 46a0262's tracker change), so both sides are re-run here.
declare -A PLATE=( [white-duo-full]="plates/plate_slot_2_20260403_101623_median.npz"
                   [texture-duo-full]="plates/plate_slot_5_20260401_161146_median.npz"
                   [bdx1005-s5-ghost]="plates/plate_slot_5_20261005_211639_median.npz"
                   [bdx1005-s8-shadows]="plates/plate_slot_8_20261005_213333_median.npz" )
for side in pre fix; do
  if [ $side = pre ]; then cd $PRE/application; else cd $WT/application; fi
  for s in white-duo-full texture-duo-full bdx1005-s8-shadows texture-aerial outdoor-night hangar-aerial-full; do
    step "older $side e1 $s"
    [ -f $OUT/older/${s}_${side}_e1.json ] || nice -n 5 timeout 3000 $PY tests/replay.py \
        --scenario tests/scenarios/$s.json --trt --engine-dir $ENG --imgsz 1280 --quality --internal \
        --timeline $OUT/older/${s}_${side}_e1.json > $OUT/older/${s}_${side}_e1.log 2>&1
  done
  for s in bdx1005-s5-ghost bdx1005-s8-shadows white-duo-full texture-duo-full; do
    step "older $side v5 $s"
    [ -f $OUT/older/${s}_${side}_v5.json ] || nice -n 5 timeout 3000 $PY tests/replay.py \
        --scenario tests/scenarios/$s.json --trt --engine-dir $ENG --quality --internal \
        --set belt_backing=true --set fg_enabled=true --set fg_plate=${PLATE[$s]} --set entry_min_travel_h=0.25 \
        --timeline $OUT/older/${s}_${side}_v5.json > $OUT/older/${s}_${side}_v5.log 2>&1
  done
done
step "done"
