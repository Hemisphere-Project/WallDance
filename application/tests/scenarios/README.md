# Scenario manifests (TUNING.md Phase A1)

A scenario pins one recorded window to its **ground-truth dancer count** so
detection quality is scored against truth, not against the old conflated proxies
(`avg_detections` / `zero_detection_frames` — a fall in either could be
ghost-removal *or* dancer-loss; only known-N disambiguates).

One JSON file per scored window. Consumed by [`scoring.py`](../scoring.py) and
[`replay.py`](../replay.py) (`--scenario`).

## Schema

| field | type | meaning |
|-------|------|---------|
| `name` | str | manifest id (used in score output) |
| `project` | str | project under `projects/` |
| `slot` | int | recording slot (resolved to `recordings/slot_<n>_*.avi`) |
| `start` | int | first frame of the window (absolute, into the recording) |
| `frames` | int | window length in frames |
| `warmup` | int | frames excluded from scoring at the window start (track-confirmation lag at a mid-recording cut; ≈ `TRACK_WARMUP_THRESHOLD`=15) |
| `fps` | float | recording fps (for *-seconds metrics); from the `.avi.meta` sidecar |
| `expected_count` | int \| list | ground-truth dancer count N — see below |
| `tags` | list[str] | condition tags (lighting, texture, count, dropout, scale, motion-speed) — for Phase E sensitivity slicing |
| `ground_truth` | obj | provenance: how/when N was verified, known drop regions, notes |

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
detector's own output (that's circular). The seed scenarios were verified by:

1. Run the per-frame reported-count timeline (`replay.py --scenario ... --timeline`).
2. Brighten + montage the suspicious frames (zeros = candidate drops, >N =
   candidate ghosts) — the IR footage is near-black; strong gamma+CLAHE is
   needed just to count bodies (visualization only, *not* the detector's path).
3. Eyeball: dancer-present-but-unreported ⇒ drop; reported-but-absent ⇒ ghost;
   detector-correct ⇒ N matches. Record the verdict in `ground_truth`.

## Current corpus & its gap

Both seeds are `residence1-solo` (single dancer, `motion_first`, poor light,
textured wall). Per TUNING.md §2 this leaves **YOLO-dropout, multi-dancer,
`yolo_first`, small/far** paths under-exercised — broader labeled footage is the
main thing the corpus still needs.
