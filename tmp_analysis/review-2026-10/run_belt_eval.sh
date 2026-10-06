#!/bin/bash
cd /data/WallDance/application
S=${WD_REVIEW_OUT:-/tmp/wd-review}/belt
for s in 6 7 5; do
  echo "=== slot $s $(date +%T)"
  .venv/bin/python ../tmp_analysis/belt_eval.py --project 4_TANGO_HANGAR-whitebg3 --slot $s \
     --exposure-us 49327 --gain-db 27.8 --distance-m 25 --static persist \
     --poses-dir ../tmp_analysis/markers-bdx-1005/poses --dancers 2 --threads 2 \
     --out $S/s$s --no-plots > $S/s$s.log 2>&1
  echo "    rc=$? $(date +%T)"
done
echo BELT DONE
