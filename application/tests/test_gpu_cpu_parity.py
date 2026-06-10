"""CPU↔GPU post-YOLO parity (ROADMAP bug #10, §4.1 step 4 risk plan (a)).

All golden/replay/tuning evidence validates the CPU path (``_process_cpu`` →
``_track_detections``); the show runs the GPU path (``_process_gpu`` →
``_run_yolo_and_track``), which hand-duplicates the same post-YOLO chain.
This test replays the same recorded frames through BOTH paths and pins their
current divergence, so the unification refactor is measurable: if the GPU
path's transform plumbing regresses (e.g. a letterbox-pad bug like #9),
per-frame agreement collapses far past these bounds.

The two paths are NOT bit-identical by design — enhancement differs (CPU
enhancer vs kornia GPU CLAHE/gamma) and YOLO sees a differently-interpolated
letterbox — so the bounds are tolerances over a measured baseline, not zero.

Baseline measured 2026-06-10 (dev RTX 3090, fp32, residence1-solo):
  slot 3: reported-count agreement 300/300, 1-track centroid p95 7.0 px
  slot 4: reported-count agreement 261/300 (87%), centroid p95 53 px
          (bridge/dropout regime — small input deltas cascade into
          different bridge decisions; that pre-existing gap IS bug #10's
          motivation, not a regression)

Opt-in — needs GPU + model weights + the recordings, ~5 min for both slots:

    WD_RUN_REPLAY=1 pytest tests/test_gpu_cpu_parity.py -v
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPLAY = HERE / "replay.py"
REPO = HERE.parent.parent
MODELS_DIR = REPO / "models"
PROJECTS_DIR = REPO / "projects"

FIXTURES = [
    # bounds = measured baseline + headroom (absorbs run jitter, catches
    # transform regressions, which blow these up by an order of magnitude)
    {"project": "residence1-solo", "slot": 3, "start": 1500, "frames": 300,
     "min_count_agreement": 0.95, "centroid_p95_max_px": 20.0},
    {"project": "residence1-solo", "slot": 4, "start": 1500, "frames": 300,
     "min_count_agreement": 0.78, "centroid_p95_max_px": 90.0},
]

# Summary-metric deltas allowed between the paths (measured: ≤2 everywhere).
METRIC_DELTA_TOL = {
    "real_tracks": 2,
    "ghost_tracks": 3,
    "marginal_tracks": 3,
    "total_tracks": 4,
    "swap_count": 3,
    "zero_detection_frames": 8,
}
AVG_DET_REL_TOL = 0.10


def _recording(project, slot):
    recs = sorted((PROJECTS_DIR / project / "recordings").glob(f"slot_{slot}_*"))
    recs = [r for r in recs if r.suffix in (".avi", ".mp4")]
    return recs[0] if recs else None


def _skip_reasons(fix):
    reasons = []
    if not os.environ.get("WD_RUN_REPLAY"):
        reasons.append("set WD_RUN_REPLAY=1 to run the GPU/CPU parity replay")
    if _recording(fix["project"], fix["slot"]) is None:
        reasons.append(f"missing recording {fix['project']} slot {fix['slot']}")
    if not list(MODELS_DIR.glob("*.pt")):
        reasons.append("no model weights in models/")
    return reasons


def _run_replay(fix, td, gpu: bool):
    tag = "gpu" if gpu else "cpu"
    out = Path(td) / f"summary_{tag}.json"
    tl = Path(td) / f"timeline_{tag}.json"
    cmd = [sys.executable, str(REPLAY),
           "--project", fix["project"], "--slot", str(fix["slot"]),
           "--start", str(fix["start"]), "--frames", str(fix["frames"]),
           "--out", str(out), "--timeline", str(tl), "--details"]
    if gpu:
        cmd.append("--gpu-path")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    assert out.exists() and tl.exists(), (
        f"{tag} replay produced no output (rc={proc.returncode})\n"
        f"STDERR tail:\n{proc.stderr[-2000:]}")
    return json.loads(out.read_text()), json.loads(tl.read_text())


def _centroid_p95(tl_cpu, tl_gpu):
    """p95 centroid distance over frames where both paths report one track."""
    dists = []
    for rc, rg in zip(tl_cpu, tl_gpu):
        tc, tg = rc.get("tracks", []), rg.get("tracks", [])
        if len(tc) == 1 and len(tg) == 1:
            c, g = tc[0]["centroid"], tg[0]["centroid"]
            dists.append(((c[0] - g[0]) ** 2 + (c[1] - g[1]) ** 2) ** 0.5)
    if not dists:
        return None
    dists.sort()
    return dists[min(len(dists) - 1, int(0.95 * len(dists)))]


@pytest.mark.parametrize("fix", FIXTURES,
                         ids=lambda f: f"{f['project']}_slot{f['slot']}")
def test_gpu_path_matches_cpu_path(fix):
    reasons = _skip_reasons(fix)
    if reasons:
        pytest.skip("; ".join(reasons))

    with tempfile.TemporaryDirectory() as td:
        sum_cpu, tl_cpu = _run_replay(fix, td, gpu=False)
        sum_gpu, tl_gpu = _run_replay(fix, td, gpu=True)

    assert sum_cpu["path"] == "cpu" and sum_gpu["path"] == "gpu"
    assert sum_cpu["frames_processed"] == sum_gpu["frames_processed"]

    problems = []

    # 1. Per-frame reported-count agreement (what OSC consumers see).
    n = min(len(tl_cpu), len(tl_gpu))
    agree = sum(1 for i in range(n)
                if tl_cpu[i]["reported"] == tl_gpu[i]["reported"])
    if n == 0 or agree / n < fix["min_count_agreement"]:
        problems.append(
            f"reported-count agreement {agree}/{n} "
            f"below {fix['min_count_agreement']:.0%}")

    # 2. Spatial agreement: solo-track centroids must land together.
    p95 = _centroid_p95(tl_cpu, tl_gpu)
    if p95 is not None and p95 > fix["centroid_p95_max_px"]:
        problems.append(
            f"1-track centroid p95 {p95:.1f}px "
            f"over {fix['centroid_p95_max_px']}px")

    # 3. Session-level metric deltas.
    for metric, tol in METRIC_DELTA_TOL.items():
        c, g = sum_cpu[metric], sum_gpu[metric]
        if abs(c - g) > tol:
            problems.append(f"{metric}: cpu={c} gpu={g} (tol +/-{tol})")
    c_avg, g_avg = sum_cpu["avg_detections"], sum_gpu["avg_detections"]
    if abs(c_avg - g_avg) > AVG_DET_REL_TOL * max(c_avg, 1e-6):
        problems.append(
            f"avg_detections: cpu={c_avg} gpu={g_avg} (rel tol {AVG_DET_REL_TOL})")

    assert not problems, (
        "CPU/GPU path parity drifted past the measured baseline "
        "(transform plumbing regression in the GPU path?):\n  "
        + "\n  ".join(problems))
