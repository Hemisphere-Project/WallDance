#!/bin/bash
# mp_study.py over the study scenes; timelines from E1 (k1) or timelines.sh.
cd /data/WallDance/application
S=/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp
STUDY=/data/WallDance/.claude/worktrees/agent-ae6a534f4327c2597/tmp_analysis/multipass/mp_study.py
mkdir -p $S/study
for item in ${ITEMS:-outdoor-night:e1/outdoor-night_k1 texture-aerial:e1/texture-aerial_k1 bdx1005-s8-shadows:e1/bdx1005-s8-shadows_k1 texture-duo-full:e1/texture-duo-full_k1}; do
  s=${item%%:*}; tl=$S/${item#*:}.json
  nice -n 5 timeout 5400 .venv/bin/python $STUDY --scenario $s --timeline $tl --out $S/study/$s.json \
    --live-every ${EVERY:-6} --max-gap ${MAXGAP:-250} > $S/study/$s.log 2>&1
  echo "$s rc=$? $(date +%H:%M) $(grep -v deprecated $S/study/$s.log | tail -1 | cut -c1-120)"
done
