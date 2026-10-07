#!/bin/bash
# E4 verification runs: s5 (static figure by the door), facade-ghosts (ghost flood), the night empty take
# (equipment ghost under the 10:33 gamma 1.8 / CLAHE 1.5) and its partial walk-in take (person + ghost).
cd /data/WallDance/application
S=/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp
V=/data/WallDance/.claude/worktrees/agent-ae6a534f4327c2597/tmp_analysis/multipass/mp_verify.py
mkdir -p $S/verify
run() { name=$1; shift; nice -n 5 timeout 3000 .venv/bin/python $V "$@" --out $S/verify/$name.json > $S/verify/$name.log 2>&1;
        echo "== $name rc=$? $(date +%H:%M)"; grep -v "deprecated\|^\[" $S/verify/$name.log | tail -3; }
run s5 --scenario bdx1005-s5-ghost --timeline $S/../older/bdx1005-s5-ghost_v5.json --per 30
run facade --scenario facade-ghosts --timeline $S/tl/facade-ghosts.json --per 25
NIGHT=/data/WallDance/projects/mur25m-ceinture-0610/recordings
run night_empty --video $NIGHT/slot_1_20261006_211624.avi --roi 173,401,1304,566 --gamma 1.8 --clahe 1.5 --imgsz 1280 \
    --fixed 654,685,174,static --frames 100:300:5
run night_empty_073 --video $NIGHT/slot_1_20261006_211624.avi --roi 173,401,1304,566 --gamma 0.73 --clahe 2.5 --imgsz 1280 \
    --fixed 654,685,174,static --frames 100:300:5
if [ -f $S/tl/night_s2a_g18.json ]; then
  run night_s2a_g18 --video $NIGHT/slot_2_20261006_211831.avi --roi 173,401,1304,566 --gamma 1.8 --clahe 1.5 --imgsz 1280 \
      --timeline $S/tl/night_s2a_g18.json --per 30
fi
