#!/bin/bash
# Tomorrow's laptop window (PLAN_25M §B), 4G-friendly.  NOT meant to be run blind: copy step by step.
# Everything heavy runs ON the laptop; only JSON, small JPEGs and compressed logs come back.
# Prerequisites: laptop on the tailnet, WallDance closed or in STANDBY (heavy = STANDBY only),
# project name of tonight's takes, photo of the paper sheet.  Run from the repo root on dev37.
W="python3 extra/wdremote.py"
P=mur25m-ceinture-0610                        # tonight's project (check the paper sheet)
REL=$(git rev-parse --short remote-ops)        # the release candidate

# ---- B0 (5 min): connect, state, pull text --------------------------------------------
$W doctor && $W status && $W inventory
$W pull --tier P0 --since 2026-10-06           # configs, rig sheet, .meta v2 + camlog, sessions, logs (KB)
# operator note v2 (FR) on the laptop desktop (the shell's real Desktop: OneDrive / "Bureau" handled)
$W py --with tmp_analysis/field/TERRAIN_2026-10-07_FR.pdf tmp_analysis/plan25m/to_desktop.py -- TERRAIN_2026-10-07_FR.pdf

# ---- B1 (5 min): what each take is (provenance + sheets to check the dancer count) -----
$W py --with tmp_analysis/marker_evallib.py --with application/src/core/marker_model.py \
      tmp_analysis/marker_eval.py -- info --project $P                         # exposure/gain/AE/offset/distance, ~2 KB
for s in 1 2 3 4 5 6 7 8 9; do                                                    # ~200 KB each: what the take shows, N
  $W py tmp_analysis/plan25m/take_sheets.py -- --project $P --slot $s --stride 40 --full-stride 200 --cols 4 --tile-w 360
done
# -> classify the takes (on-axis / off-axis light, N per range, operator near the lens?) BEFORE reading any number.

# ---- B2 (20-30 min): belt + IR budget on real belts ------------------------------------
BE="--with application/src/core/belt_detector.py --with tmp_analysis/marker_eval.py --with tmp_analysis/marker_evallib.py --with application/src/core/marker_model.py"
$W py $BE tmp_analysis/belt_eval.py -- --project $P --slot 2-6,9 --empty-slot 1 --static both --dancers 2 --poses-dir ../tmp_analysis/poses-0610
$W py $BE tmp_analysis/belt_eval.py -- --project $P --slot 7 --empty-slot 1      # exposure ladder (camlog plateaus)
$W py $BE tmp_analysis/belt_eval.py -- --project $P --slot 8 --empty-slot 1      # projector offset vs slot 4
# Decide D19: belt ON if belt-at-hips >= 50 % on skeleton frames and FP <= 0.05/frame, else OFF.

# ---- B4 (15 min): release gates on the DEV slot -----------------------------------------
$W deploy $REL
$W --slot dev pytest -x -q
$W --slot dev replay white-duo --trt --score
$W --slot dev run -- "set WD_POSE_RUNNER=0&& .venv\\Scripts\\python.exe tests\\replay.py --scenario tests\\scenarios\\white-duo.json --trt --score"
#   -> the two summaries must be byte-equal (PoseRunner on 8.4.63); else WD_POSE_RUNNER=0 in run.bat for the demo.
$W release-check $REL --tests                    # prints the push command for Thomas (push 1 = no reinstall)

# ---- B3 (15 min): the demo KPI on tonight's show-like takes, recommended settings --------
REC="--set tracking_mode=yolo_first --set confidence=0.15 --set tracker_intermittent_confirm=true"
for s in 9 4 5; do
  $W --slot dev replay --timeline -- --project $P --slot $s --model yolo11x-pose --imgsz 1280 --trt --quality --internal $REC
done
#   locally, per fetched timeline (N from the sheets; per-range N needs a small manifest):
#   cd application && .venv/bin/python ../tmp_analysis/plan25m/kpi.py --n 2 <tmp_analysis/remote-runs/<stamp>/timeline.json>
#   and the same take with the project's own settings (no $REC) for the before/after.

# ---- B3c (5 min): ghost spots on the EMPTY-wall take (slot 1) -> exclusion proposals ---------------------
$W --slot dev replay --timeline -- --project $P --slot 1 --model yolo11x-pose --imgsz 1280 --trt --quality --set confidence=0.10
#   python3 tmp_analysis/plan25m/ghost_spots.py tmp_analysis/remote-runs/<stamp>/timeline.json --empty
#   -> prints ExcludeAt commands; check each spot on the take sheet / snapshot: NEVER on a dancer's path.
#   Re-run B3 with --set exclusion_cells=... (or after ExcludeAt + SaveConfig) to measure the spare-slot ghost.

# ---- B3b (10-15 min): clean-plate foreground N-lock (BRAINSTORM §3.2), slot 1 = the empty-wall plate --------
# one shot on the laptop: decodes the plate + each take (ROI from the project), finds B3's replay timeline per take
# in tmp_analysis/remote/, runs nlock.py --variant fg --bg plate; one JSON line per take comes back (~1 KB).
$W py --with tmp_analysis/brainstorm-2026-10/fg_lib.py --with tmp_analysis/brainstorm-2026-10/nlock.py \
      tmp_analysis/brainstorm-2026-10/fg_takes.py -- --project $P --slots 4,5,6,9 --plate-slot 1 --n 2
#   compare onD / coast_share / hole_max_s with kpi.py's emitted row for the same take (B3): if the foreground wins
#   clearly on tonight's on-axis takes, D26 = build the hook before the demo.

# ---- B5 (10 min): perf with TouchDesigner (slot 9 session of the evening) ----------------
$W ls logs
#   $W pull logs/walldance_<evening stamp>.log  ;  grep '\[Budget\]\|\[PerfSpike\]' -> process_wall, yolo, mog2_wait, track
$W run -- nvidia-smi --query-gpu=clocks.sm,power.draw,temperature.gpu,clocks_throttle_reasons.active --format=csv

# ---- B6 (10 min): settings for the day (STANDBY, or RUN with "Allow remote control") -----
$W cmd SetImgsz value=1280                     # HEAVY (STANDBY only): a new project defaults to 800; x@1280 is the working default
$W cmd SetTrackingMode value=yolo_first
$W cmd SetConfidence value=0.15
$W cmd SetIntermittentConfirm enabled=true
$W cmd SetMaxDancers value=2
$W cmd SetCoastSeconds value=2.0
$W cmd SetStability value=0.5
$W cmd SetStaticGhostGuard enabled=true
$W cmd ToggleIrBelt enabled=true               # or false per B2
$W cmd SetSlotFilterInput value=smoothed       # raw_skeleton = less lag, judge on TD (D18)
#   ROI = the wall only:      $W cmd SetRoiRect x=.. y=.. w=.. h=..   (frame px)
#   exclusion ONLY where dancers never go (a lamp, a door sign): $W cmd ExcludeAt x=.. y=..
$W cmd SaveConfig
$W snapshot                                     # check ROI / points on the preview
