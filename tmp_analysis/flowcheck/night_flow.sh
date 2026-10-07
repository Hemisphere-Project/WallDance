#!/bin/bash
# Part B on last night's takes, recut to one frame (projects/mur25m-night-common):
#  s1   = Calibrate on slot 1 (the bright empty take, from 5 s in) -> s2a s2c s3 s4 s6 s7
#  dark = Calibrate on the dark empty take (21:40)                -> s2c s4 s6 s7
# The baseline replays (the config as is, no snapshot) are shared through the --out folder.
RUN=${1:-night_$(date +%Y%m%d_%H%M)}
WT=/data/WallDance/.claude/worktrees/agent-aa2111d4744a81346
OUT=/data/WallDance/tmp_analysis/flowcheck/$RUN
PY=/data/WallDance/application/.venv/bin/python
ENG=/data/WallDance/models/dev37
S2A=slot_2_20261006_211831.avi; S2C=slot_2_20261006_214531.avi; S3=slot_3_20261006_212855.avi
S4=slot_4_20261006_213310.avi; S6=slot_6_20261006_214306.avi; S7=slot_7_20261006_220640.avi
mkdir -p $OUT
cd $WT/application
nice -n 5 $PY tests/flow_check.py --project mur25m-night-common --empty slot_1_20261006_211624.avi:100 \
    --takes $S6,$S7,$S4,$S3,$S2C,$S2A --label s1 --out $OUT --engine-dir $ENG 2>&1 | grep "^\[flow\]"
nice -n 5 $PY tests/flow_check.py --project mur25m-night-common --empty slot_2_20261006_214010.avi:20 \
    --takes $S6,$S7,$S4,$S2C --label dark --out $OUT --engine-dir $ENG 2>&1 | grep "^\[flow\]"
echo "done $(date +%H:%M) -> $OUT"
