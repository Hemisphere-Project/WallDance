#!/bin/bash
# Wait for the recut (8 "N -> N frames" lines in its log), then run the night flow scenarios.
LOG=${1:?recut log}
RUN=${2:-night_rc}
until [ "$(grep -c ' frames, ' "$LOG" 2>/dev/null)" -ge 8 ]; do sleep 20; done
cat "$LOG"
bash "$(dirname "$0")/night_flow.sh" "$RUN"
