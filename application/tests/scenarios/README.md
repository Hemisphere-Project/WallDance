# Scenario manifests (TUNING.md Phase A1; re-founded 2026-06-10)

A scenario pins one recorded window to its **ground-truth dancer count** so
detection quality is scored against truth, not against the old conflated proxies
(`avg_detections` / `zero_detection_frames` — a fall in either could be
ghost-removal *or* dancer-loss; only known-N disambiguates).

One JSON file per scored window. Consumed by [`scoring.py`](../scoring.py) and
[`replay.py`](../replay.py) (`--scenario`).  `drafts/` holds manifests whose
ground truth is not yet usable (varying N pending per-range labels) — excluded
from the unit-test validation and the golden regression.

## Schema

| field | type | meaning |
|-------|------|---------|
| `name` | str | manifest id (used in score output) |
| `project` | str | project under `projects/` |
| `slot` | int | recording slot (resolved to `recordings/slot_<n>_*.avi`) |
| `start` | int | first frame of the window (absolute, into the recording) |
| `frames` | int | window length in frames |
| `warmup` | int | frames excluded from scoring at the window start (track-confirmation lag at a mid-recording cut; ≈ `TRACK_WARMUP_THRESHOLD`=15) |
| `fps` | float | recording fps (for *-seconds metrics); from the `.avi.meta` sidecar (the AVI header often lies) |
| `expected_count` | int \| list | ground-truth dancer count N — see below |
| `tags` | list[str] | condition tags (lighting, texture, count, dropout, scale, motion-speed) — for Phase E sensitivity slicing |
| `ground_truth` | obj | provenance: how/when N was verified, known drop regions, notes |
| `config` | obj | **frozen config snapshot** the scenario replays with (flattened, as `replay._build_processor` consumes it). Pinned so goldens are reproducible from the repo alone — the 2026-06 reorganisation orphaned the original goldens precisely because configs lived only in mutable project folders. `replay.py` prefers this over the project's latest config; `--set` still overrides on top |
| `recording_fingerprint` | obj | `{file, bytes, frames}` of the recording at GT-verification time. `replay.py` hard-fails on mismatch — re-organised/re-cut footage must trigger a GT re-verification, not silently invalidate it |
| `pass` | obj | scene-class pass line (CORPUS_ANALYSIS §8): `{"class": "A"\|"B"\|"S", "drop_rate": ..., "ghost_rate": ..., "longest_drop_s": ...}`. Class A (indoor rigged) 0.05/0.05/1.0s; class B (outdoor/uncontrolled) 0.10/0.15/2.0s; class S (stress) no thresholds. Evaluated by `scoring.evaluate_pass` (`replay --score` prints the verdict) |

### `expected_count`

* **Constant:** an int (e.g. `1`) — N is the same every frame.
* **Per-range:** a list of window-**relative** inclusive ranges, first match wins,
  with an optional default:
  ```json
  "expected_count": [
    {"from": 0,   "to": 119, "n": 1},
    {"from": 120, "to": 200, "n": 2},
    {"default": 0}
  ]
  ```

## Establishing ground truth (the keystone — do not guess)

N must be **verified against the actual footage**, not inferred from the
detector's own output (that's circular). Protocol:

1. Run the per-frame reported-count timeline (`replay.py --scenario ... --timeline`).
2. Brighten + montage the suspicious frames (zeros = candidate drops, >N =
   candidate ghosts) — the IR footage is near-black; strong gamma+CLAHE is
   needed just to count bodies (visualization only, *not* the detector's path).
   `tmp_analysis/gen_gt_sheets.py` generates an every-20-frames sheet per
   manifest for the operator pass.
3. Eyeball: dancer-present-but-unreported ⇒ drop; reported-but-absent ⇒ ghost;
   detector-correct ⇒ N matches. Record the verdict in `ground_truth`.
4. **Count every visible person, or pin a stage ROI** — bystanders at the frame
   edge (TOGO), assistants beside the stage (testflou) are real detections; N
   must agree with what the detector can legitimately see (corpus-analysis
   lesson, 2026-06-10).
5. Pin the `recording_fingerprint` at verification time.

## Current corpus (re-founded 2026-06-10, CORPUS_ANALYSIS §5)

**Golden trio** (regression-tested in `test_regression_replay.py`):
`hangar-floor` + `hangar-aerial` (ex `residence1-solo` slots 3/4, same files)
+ `texture-aerial` (operator-picked). **Tuning set:** texture-duo,
texture-wallhang, white-duo, blur-runner, outdoor-night, outdoor-sitter,
facade-ghosts (class S). **Drafts pending per-range labels:** dark-crowd,
white-walkers. Together these cover multi-dancer, aerial/inverted, small-far,
texture ghosts, outdoor day/night, defocus, static-person, and ghost-flood —
the gaps TUNING.md §2 named. Moving-camera clips stay out of tracking
scenarios (YOLO-only stress assets).
