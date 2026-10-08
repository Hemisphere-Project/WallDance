#!/bin/bash
# The night takes from frame 0 through their mid-take re-entries at the wall (state stays faithful:
# height guard, tracker, slots), with the raw YOLO capture.  usage: long_runs.sh <variant-name> [--set k=v ...]
D=$(cd "$(dirname "$0")" && pwd)
PY=/data/WallDance/application/.venv/bin/python
V=$1; shift
for a in "slot_2_20261006_214531.avi s2c 820" "slot_3_20261006_212855.avi s3 1160" "slot_4_20261006_213310.avi s4 1000"; do
  set -- $a "$@"
  take=$1; name=$2; fr=$3; shift 3
  [ -f $D/out/${name}_long_${V}.json ] || nice -n 5 $PY $D/primed_replay.py --take $take --frames $fr \
      --timeline $D/out/${name}_long_${V}.json "$@" 2>&1 | grep -E "^\[primed|Error"
done
echo "done $(date +%H:%M)"
