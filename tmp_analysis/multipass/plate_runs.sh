#!/bin/bash
# Guarded zoom (a 'lost' find needs plate foreground) vs off, both with the take-median plate.
B=/data/WallDance/.claude/worktrees/agent-ae6a534f4327c2597/tmp_analysis/multipass/proto_runs.sh
SCENES=white-duo-full MODES="off both" EXTRA="--set fg_enabled=true --set fg_plate=plates/plate_slot_2_20260403_101623_median.npz" TAG=_plate bash $B
SCENES=facade-ghosts MODES="off both" EXTRA="--set fg_enabled=true --set fg_plate=/tmp/claude-1000/-data-WallDance/f3da8797-968d-4ec5-8467-0a4cddf78fc3/scratchpad/mp/plate_facade_median.npz" TAG=_plate bash $B
SCENES=texture-duo-full MODES="off both" EXTRA="--set fg_enabled=true --set fg_plate=plates/plate_slot_5_20260401_161146_median.npz" TAG=_plate bash $B
