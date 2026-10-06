#!/bin/bash
# usage: cat sweep_jobs.txt | xargs -d "\n" -P 2 -I{} ./sweep_run_one.sh "{}"   (manifests: $S/<name>.json or tests/scenarios; outputs: $S/sweep/)
# $1 = "label|manifest|args"
S=${WD_REVIEW_OUT:-/tmp/wd-review}; E=/data/WallDance/models/dev37
IFS='|' read -r lab man args <<< "$1"
cd /data/WallDance/application
eval .venv/bin/python tests/replay.py --scenario $S/$man.json --score --quality --trt --engine-dir $E --timeline $S/sweep/$lab.timeline.json --internal --out $S/sweep/$lab.summary.json $args > $S/sweep/$lab.log 2>&1
echo "$lab rc=$? $(date +%T)"
