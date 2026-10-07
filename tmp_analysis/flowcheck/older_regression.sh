#!/bin/bash
# Part C: the release candidate on the older corpus vs the stored timelines of earlier code.
#  (a) like E1 (2026-10-07 14:00, remote-ops ~24d977b): scenario config, x@1280, nothing else
#  (b) like v5 (3cde9b3-era flags): belt backing + plate + entry rule 0.25
WT=/data/WallDance/.claude/worktrees/agent-aa2111d4744a81346
OUT=/data/WallDance/tmp_analysis/flowcheck/older
PY=/data/WallDance/application/.venv/bin/python
ENG=/data/WallDance/models/dev37
mkdir -p $OUT
cd $WT/application
declare -A PLATE=( [white-duo-full]="plates/plate_slot_2_20260403_101623_median.npz"
                   [texture-duo-full]="plates/plate_slot_5_20260401_161146_median.npz"
                   [bdx1005-s5-ghost]="plates/plate_slot_5_20261005_211639_median.npz"
                   [bdx1005-s8-shadows]="plates/plate_slot_8_20261005_213333_median.npz" )
for s in white-duo-full texture-duo-full bdx1005-s8-shadows texture-aerial outdoor-night hangar-aerial-full; do
  [ -f $OUT/${s}_rc_e1.json ] || nice -n 5 timeout 3000 $PY tests/replay.py --scenario tests/scenarios/$s.json --trt \
      --engine-dir $ENG --imgsz 1280 --quality --internal --timeline $OUT/${s}_rc_e1.json > $OUT/${s}_rc_e1.log 2>&1
  echo "$s e1 rc=$? $(date +%H:%M)"
done
for s in bdx1005-s5-ghost bdx1005-s8-shadows white-duo-full texture-duo-full; do
  [ -f $OUT/${s}_rc_v5.json ] || nice -n 5 timeout 3000 $PY tests/replay.py --scenario tests/scenarios/$s.json --trt \
      --engine-dir $ENG --quality --internal --set belt_backing=true --set fg_enabled=true \
      --set fg_plate=${PLATE[$s]} --set entry_min_travel_h=0.25 --timeline $OUT/${s}_rc_v5.json > $OUT/${s}_rc_v5.log 2>&1
  echo "$s v5 rc=$? $(date +%H:%M)"
done
