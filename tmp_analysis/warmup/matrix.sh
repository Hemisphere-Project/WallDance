#!/bin/bash
# Start-of-take hole: counterfactual matrix on the first 30 s of the six night takes.
#   base   = the project config as is (frame 0 = the take's first frame, a cold pipeline)
#   prime  = 15 s of the matching empty take first (bright 21:16 for s2a/s3, dark 21:40 for the rest)
#   ph127  = person_height_px at the wall height (what a calibrated project would hold)
#   ph650  = person_height_px at the walk-in height (close to the camera)
#   basek  = base, rows with the raw YOLO boxes (the "first confident skeleton" for summary2.py)
#   grow   = the own-size gate prototype (needs: git apply own_height_grow_prototype.patch)
# Tables: summary2.py out basek base,prime,ph127,ph650 ; wall re-entries: long_runs.sh base + reentry.py
# usage: matrix.sh <variant>[,<variant>...] [frames]
D=$(cd "$(dirname "$0")" && pwd)
PY=/data/WallDance/application/.venv/bin/python
OUT=$D/out
FR=${2:-600}
mkdir -p $OUT
declare -A TAKE=( [s2a]=slot_2_20261006_211831.avi [s2c]=slot_2_20261006_214531.avi [s3]=slot_3_20261006_212855.avi
                  [s4]=slot_4_20261006_213310.avi [s6]=slot_6_20261006_214306.avi [s7]=slot_7_20261006_220640.avi )
declare -A EMPTY=( [s2a]=slot_1_20261006_211624.avi:100 [s3]=slot_1_20261006_211624.avi:100
                   [s2c]=slot_2_20261006_214010.avi:20 [s4]=slot_2_20261006_214010.avi:20
                   [s6]=slot_2_20261006_214010.avi:20 [s7]=slot_2_20261006_214010.avi:20 )
IFS=, read -ra VARS <<< "$1"
for v in "${VARS[@]}"; do
  for t in s2a s2c s3 s4 s6 s7; do
    out=$OUT/${t}_${v}.json
    [ -f $out ] && continue
    extra=()
    case $v in
      base|basek) ;;
      prime) extra=(--prime ${EMPTY[$t]}:300) ;;
      ph127) extra=(--set person_height_px=127) ;;
      ph650) extra=(--set person_height_px=650) ;;
      prime_ph127) extra=(--prime ${EMPTY[$t]}:300 --set person_height_px=127) ;;
      grow) extra=(--set tracker_own_height_grow=true) ;;
      grow_ph127) extra=(--set tracker_own_height_grow=true --set person_height_px=127) ;;
      *) extra=($(echo "$v" | tr '+' ' ')) ;;    # raw: --set+key=value
    esac
    nice -n 5 $PY $D/primed_replay.py --take ${TAKE[$t]} --frames $FR --timeline $out "${extra[@]}" 2>&1 \
        | grep -E "^\[(primed|HeightGuard)\]|Error" | sed "s/^/$t $v: /"
  done
done
echo "done $(date +%H:%M)"
